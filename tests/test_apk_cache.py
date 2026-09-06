from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qsx_alpine import apk_cache
from qsx_alpine.errors import QSXError


def test_apk_solver_error_with_success_exit_is_failure() -> None:
    result = subprocess.CompletedProcess(["apk"], 0, "", "ERROR: unable to select packages:\n")
    with pytest.raises(QSXError, match="unable to select packages"):
        apk_cache._check_apk_result(result)


@pytest.mark.parametrize("code,output", [(1, "UNTRUSTED signature"), (0, "WARNING: missing index")])
def test_apk_failure_and_incomplete_index_are_not_accepted(code: int, output: str) -> None:
    with pytest.raises(QSXError):
        apk_cache._check_apk_result(subprocess.CompletedProcess(["apk"], code, "", output))


@pytest.mark.parametrize("packages", [[], ["--allow-untrusted"], ["../git"], ["git;id"], ["git\n"]])
def test_invalid_requests_do_not_create_a_cache(tmp_path: Path, packages: list[str]) -> None:
    destination = tmp_path / "cache"
    with pytest.raises(QSXError):
        apk_cache.fetch_packages(destination, packages)
    assert not destination.exists()


def test_offline_missing_cache_does_not_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("offline cache verification attempted a download")

    monkeypatch.setattr(apk_cache, "_download_minrootfs", forbidden)
    destination = tmp_path / "cache"
    with pytest.raises(QSXError, match="offline.*no complete cache"):
        apk_cache.fetch_packages(destination, ["git"], offline=True)
    assert not destination.exists()


@pytest.fixture
def cache_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    """Synthetic inputs for cache-integrity tests, not proof of APK signatures."""
    root = tmp_path / "cache"
    root.mkdir()
    for name in ("inputs", "packages", "indexes", "keys"):
        (root / name).mkdir()
    (root / "inputs" / apk_cache.MINIROOTFS_NAME).write_bytes(b"synthetic bootstrap")
    (root / "inputs" / (apk_cache.MINIROOTFS_NAME + ".asc")).write_bytes(b"synthetic signature")
    (root / "inputs/ncopa.asc").write_bytes(b"synthetic release public key")
    (root / "keys/key.rsa.pub").write_bytes(b"synthetic public key")
    for name in ("main", "community"):
        (root / f"indexes/APKINDEX.{name}.tar.gz").write_bytes(name.encode())
    package = root / "packages/example-1.0-r0.apk"
    data = b"pkgname = example\npkgver = 1.0-r0\narch = noarch\n"
    with tarfile.open(package, "w:gz") as archive:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    artifacts = []
    for directory, kind in (("inputs", "bootstrap"), ("packages", "package"),
                            ("indexes", "repository_index"), ("keys", "trust_key")):
        for path in sorted((root / directory).iterdir()):
            artifacts.append({
                "path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "kind": kind,
            })
    package_record = next(item for item in artifacts if item["kind"] == "package")
    manifest = {
        "schema_version": 1, "alpine_release": apk_cache.ALPINE_RELEASE,
        "architecture": apk_cache.ARCHITECTURE, "requested_packages": ["example"],
        "repositories": apk_cache.REPOSITORIES, "apk_version": "apk-tools unit fixture",
        "minirootfs": {
            **next(item for item in artifacts if item["path"].endswith(".tar.gz")
                   and item["kind"] == "bootstrap"),
            "path": f"inputs/{apk_cache.MINIROOTFS_NAME}", "url": apk_cache.MINIROOTFS_URL,
            "checksum_url": apk_cache.MINIROOTFS_URL + ".sha512",
            "sha512": apk_cache.MINIROOTFS_SHA512,
            "signature_path": f"inputs/{apk_cache.MINIROOTFS_NAME}.asc",
            "signature_url": apk_cache.MINIROOTFS_URL + ".asc",
            "public_key_path": "inputs/ncopa.asc",
            "public_key_url": apk_cache.MINIROOTFS_KEY_URL,
            "public_key_fingerprint": apk_cache.MINIROOTFS_SIGNER,
            "signature_verification": {"status": "UNIT_FIXTURE"},
        },
        "packages": [{**package_record, "name": "example", "version": "1.0-r0",
                      "architecture": "noarch", "depends": [], "provides": []}],
        "artifacts": artifacts,
    }
    (root / "lock.json").write_text(json.dumps(manifest))
    runtime = tmp_path / "fake-runtime"
    (runtime / "etc/apk/keys").mkdir(parents=True)
    (runtime / "etc/apk/keys/key.rsa.pub").write_bytes(b"synthetic public key")

    def run(*args: str, network: bool = False) -> str:
        assert not network
        assert args[0] in ("--version", "verify")
        return "apk-tools unit fixture" if args[0] == "--version" else ""

    @contextmanager
    def mock_runtime(*args: Any) -> Iterator[Any]:
        yield SimpleNamespace(runtime=runtime, run=run, bootstrap_signature={"status": "UNIT_FIXTURE"})

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("cache hit attempted network download")

    monkeypatch.setattr(apk_cache, "_runtime", mock_runtime)
    monkeypatch.setattr(apk_cache, "_download_minrootfs", forbidden)
    return root, manifest


@pytest.mark.parametrize("offline", [False, True])
def test_cache_hit_verifies_without_download(
    cache_fixture: tuple[Path, dict[str, Any]], offline: bool,
) -> None:
    root, expected = cache_fixture
    assert apk_cache.fetch_packages(root, ["example", "example"], offline=offline) == expected


@pytest.mark.parametrize("relative", ["packages/example-1.0-r0.apk", "indexes/APKINDEX.main.tar.gz"])
def test_corrupt_cache_is_refused_without_refetch(
    cache_fixture: tuple[Path, dict[str, Any]], relative: str,
) -> None:
    root, _ = cache_fixture
    path = root / relative
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(QSXError, match="integrity mismatch"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_changed_package_list_refuses_same_destination(
    cache_fixture: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = cache_fixture
    with pytest.raises(QSXError, match="different package list"):
        apk_cache.fetch_packages(root, ["python3"])


def test_unlocked_extra_apk_is_refused(cache_fixture: tuple[Path, dict[str, Any]]) -> None:
    root, _ = cache_fixture
    (root / "packages/extra.apk").write_bytes(b"unexpected payload")
    with pytest.raises(QSXError, match="file set differs"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_artifact_symlink_is_refused(cache_fixture: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    root, _ = cache_fixture
    package = root / "packages/example-1.0-r0.apk"
    original = tmp_path / "original.apk"
    package.rename(original)
    package.symlink_to(original)
    with pytest.raises(QSXError, match="must not be symlinks"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_lock_path_traversal_is_refused(cache_fixture: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = cache_fixture
    manifest["artifacts"][0]["path"] = "../outside"
    (root / "lock.json").write_text(json.dumps(manifest))
    with pytest.raises(QSXError, match="unsafe artifact path"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_changed_bootstrap_pin_is_refused(cache_fixture: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = cache_fixture
    manifest["minirootfs"]["sha512"] = "0" * 128
    (root / "lock.json").write_text(json.dumps(manifest))
    with pytest.raises(QSXError, match="pinned official bootstrap"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_changed_signed_package_metadata_is_refused(
    cache_fixture: tuple[Path, dict[str, Any]],
) -> None:
    root, manifest = cache_fixture
    manifest["packages"][0]["version"] = "9.9-r0"
    (root / "lock.json").write_text(json.dumps(manifest))
    with pytest.raises(QSXError, match="metadata differs"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_runtime_rejects_unpinned_archive_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "untrusted.tar.gz"
    archive.write_bytes(b"wrong bytes")
    with pytest.raises(QSXError, match="SHA-512 mismatch"):
        apk_cache._extract_runtime(archive, tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


def test_malformed_package_record_returns_qsx_error(
    cache_fixture: tuple[Path, dict[str, Any]],
) -> None:
    root, manifest = cache_fixture
    manifest["packages"][0]["path"] = []
    (root / "lock.json").write_text(json.dumps(manifest))
    with pytest.raises(QSXError, match="invalid paths"):
        apk_cache.fetch_packages(root, ["example"], offline=True)


def test_invalid_bootstrap_signature_blocks_runtime_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cache"
    root.mkdir()

    def bad_signature(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise QSXError("invalid publisher signature")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("runtime extraction happened before bootstrap signature verification")

    monkeypatch.setattr(apk_cache, "verify_gpg_signature", bad_signature)
    monkeypatch.setattr(apk_cache, "_extract_runtime", forbidden)
    with (
        pytest.raises(QSXError, match="invalid publisher signature"),
        apk_cache._runtime(root, root / "input.tar.gz"),
    ):
        pytest.fail("unverified native APK became executable")
