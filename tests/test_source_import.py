from __future__ import annotations

import hashlib
import os
import tarfile
from pathlib import Path
from typing import BinaryIO

import pytest

from qsx_alpine.errors import QSXError
from qsx_alpine.runtime import _source_archive


def test_snapshot_manifest_matches_archived_data_and_preserves_safe_links(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "data.txt").write_bytes(b"synthetic project data\n")
    (source / "data.txt").chmod(0o6751)
    (source / "subdir").mkdir(mode=0o750)
    (source / "subdir/link").symlink_to("../data.txt")
    (source / "dangling").symlink_to("future.txt")
    (source / ".env.local").write_text("SYNTHETIC_EXCLUDED_DATA\n")
    archive = tmp_path / "project.tar"
    manifest = _source_archive(source, archive)
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    with tarfile.open(archive) as saved:
        assert saved.getnames() == ["project/" + entry["path"] for entry in manifest["entries"]]
        stream = saved.extractfile("project/data.txt")
        assert stream is not None
        data = stream.read()
        assert entries["data.txt"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert data == b"synthetic project data\n"
        assert saved.getmember("project/data.txt").mode == 0o751
        assert saved.getmember("project/subdir/link").linkname == "../data.txt"
        assert saved.getmember("project/dangling").linkname == "future.txt"
    assert ".env.local" not in entries


def test_snapshot_rejects_data_changed_while_archiving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    data = source / "data.txt"
    data.write_bytes(b"original data")
    original = tarfile.TarFile.addfile

    def change_during_copy(
        output: tarfile.TarFile, member: tarfile.TarInfo, fileobj: BinaryIO | None = None
    ) -> None:
        if member.name != "project/data.txt":
            original(output, member, fileobj)
            return
        data.write_bytes(b"modified data")
        try:
            original(output, member, fileobj)
        finally:
            data.write_bytes(b"original data")

    monkeypatch.setattr(tarfile.TarFile, "addfile", change_during_copy)
    with pytest.raises(QSXError, match="source changed"):
        _source_archive(source, tmp_path / "project.tar")
    assert data.read_bytes() == b"original data"


def test_snapshot_does_not_copy_through_replaced_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "project"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "data.txt").write_text("project data")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "data.txt").write_text("sibling data")
    archive = tmp_path / "project.tar"
    original = tarfile.open

    def change_after_inventory(*args: object, **kwargs: object) -> tarfile.TarFile:
        output = original(*args, **kwargs)  # type: ignore[arg-type]
        nested.rename(tmp_path / "saved-nested")
        nested.symlink_to(sibling, target_is_directory=True)
        return output

    with monkeypatch.context() as patch:
        patch.setattr(tarfile, "open", change_after_inventory)
        with pytest.raises(QSXError):
            _source_archive(source, archive)
    with tarfile.open(archive) as saved:
        assert "project/nested/data.txt" not in saved.getnames()


@pytest.mark.parametrize("target", ["/absolute", "../../outside", "link"])
def test_snapshot_rejects_unsafe_or_cyclic_links(tmp_path: Path, target: str) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "link").symlink_to(target)
    with pytest.raises(QSXError, match="source symlink"):
        _source_archive(source, tmp_path / "project.tar")


def test_snapshot_validates_link_chains_and_keeps_safe_directory_links(tmp_path: Path) -> None:
    source = tmp_path / "project"
    (source / "subdir").mkdir(parents=True)
    (source / "root-link").symlink_to(".")
    (source / "directory-link").symlink_to("subdir")
    (source / "subdir/parent").symlink_to("../root-link")
    result = _source_archive(source, tmp_path / "safe.tar")
    assert len(result["entries"]) == 4
    (source / "subdir/chain").symlink_to("parent/../outside")
    with pytest.raises(QSXError, match="source symlink"):
        _source_archive(source, tmp_path / "rejected.tar")


def test_snapshot_preserves_regular_hardlinks_as_data_and_omits_exclusions(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    first = source / "dane ze spacją.txt"
    first.write_bytes(b"ordinary data")
    (source / "hardlink").hardlink_to(first)
    (source / ".codex").mkdir()
    os.mkfifo(source / ".codex/excluded-fifo")
    archive = tmp_path / "project.tar"
    result = _source_archive(source, archive)
    assert [entry["path"] for entry in result["entries"]] == [first.name, "hardlink"]
    with tarfile.open(archive) as saved:
        assert all(member.isfile() for member in saved.getmembers())
        for member in saved.getmembers():
            stream = saved.extractfile(member)
            assert stream is not None and stream.read() == b"ordinary data"


def test_snapshot_fails_on_unsupported_or_unreadable_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    os.mkfifo(source / "fifo")
    with pytest.raises(QSXError, match="unsupported"):
        _source_archive(source, tmp_path / "special.tar")
    (source / "fifo").unlink()

    def unreadable(directory: int) -> list[str]:
        raise PermissionError("synthetic unreadable directory")

    monkeypatch.setattr(os, "listdir", unreadable)
    with pytest.raises(QSXError, match="snapshot failed"):
        _source_archive(source, tmp_path / "unreadable.tar")


def test_empty_snapshot_remains_valid(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar"
    result = _source_archive(None, archive)
    assert result["entries"] == []
    with tarfile.open(archive) as saved:
        assert saved.getmembers() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="requires ordinary directory permission checks")
def test_snapshot_requires_only_search_permission_on_source_ancestors(tmp_path: Path) -> None:
    ancestor = tmp_path / "search-only"
    source = ancestor / "project"
    source.mkdir(parents=True)
    (source / "data.txt").write_bytes(b"readable project")
    ancestor.chmod(0o111)
    try:
        result = _source_archive(source, tmp_path / "project.tar")
        assert [entry["path"] for entry in result["entries"]] == ["data.txt"]
    finally:
        ancestor.chmod(0o700)
