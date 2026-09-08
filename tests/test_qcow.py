from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from qsx_alpine import qcow
from qsx_alpine.errors import QSXError
from qsx_alpine.runtime import create_instance
from qsx_alpine.templates import _check_qcow, _digest, verify_image
from qsx_alpine.util import sha256


def image(path: Path, options: str | None = None) -> Path:
    argv = ["qemu-img", "create", "-f", "qcow2"]
    if options:
        argv += ["-o", options]
    subprocess.run([*argv, str(path), "1M"], check=True, capture_output=True)
    return path


def manifest(path: Path) -> Path:
    log = path.with_suffix(".boot.log")
    log.write_text("SYNTHETIC_READY\n")
    value = {"schema_version": 1, "image": path.name, "image_sha256": sha256(path),
             "size_bytes": path.stat().st_size, "boot_log": log.name,
             "boot_log_sha256": sha256(log), "readiness_marker": "SYNTHETIC_READY",
             "resources": {"memory_mib": 512, "vcpus": 1, "disk_gib": 2},
             "agent_argv": ["python3", "-q"], "agent_profile_sha256": "a" * 64,
             "recipe_sha256": "b" * 64}
    value["manifest_sha256"] = _digest(value)
    target = path.with_suffix(".manifest.json")
    target.write_text(json.dumps(value))
    return target


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
def test_external_data_image_is_rejected_by_public_verification(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    selected = image(bundle / "external.qcow2", f"data_file={tmp_path / 'data.raw'},data_file_raw=on")
    with pytest.raises(QSXError, match="external|standalone"):
        verify_image(manifest(selected))


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
@pytest.mark.parametrize("options", ["compat=0.10", "compat=1.1", "compression_type=zstd", "extended_l2=on"])
def test_supported_standalone_images_remain_accepted(tmp_path: Path, options: str) -> None:
    selected = image(tmp_path / "standalone.qcow2", options)
    assert _check_qcow(selected)["virtual-size"] == 1024**2
    verify_image(manifest(selected))


@pytest.mark.parametrize("variant", ["backing", "data-flag", "raw-data-flag", "data-extension",
                                    "unknown-feature", "bad-version", "bad-cluster", "truncated"])
def test_dependency_headers_are_rejected_before_native_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str,
) -> None:
    header = bytearray(512)
    struct.pack_into(">II", header, 0, 0x514649FB, 3)
    struct.pack_into(">I", header, 20, 9)
    struct.pack_into(">I", header, 100, 104)
    if variant == "backing":
        struct.pack_into(">Q", header, 8, 200)
    elif variant == "data-flag":
        struct.pack_into(">Q", header, 72, 4)
    elif variant == "raw-data-flag":
        struct.pack_into(">Q", header, 88, 2)
    elif variant == "data-extension":
        # Reject the named dependency even with its feature flags cleared.
        struct.pack_into(">II", header, 104, 0x44415441, 8)
        header[112:120] = b"data.raw"
    elif variant == "unknown-feature":
        struct.pack_into(">Q", header, 72, 1 << 63)
    elif variant == "bad-version":
        struct.pack_into(">I", header, 4, 9)
    elif variant == "bad-cluster":
        struct.pack_into(">I", header, 20, 31)
    else:
        del header[20:]
    selected = tmp_path / "synthetic-header.qcow2"
    selected.write_bytes(header)

    def forbidden(argv: list[str]) -> str:
        raise AssertionError(f"native parser reached before admission: {argv}")

    monkeypatch.setattr(qcow, "run", forbidden)
    with pytest.raises(QSXError):
        _check_qcow(selected)


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
def test_instance_rejects_external_data_before_creating_storage(tmp_path: Path) -> None:
    selected = image(tmp_path / "external.qcow2", f"data_file={tmp_path / 'data.raw'}")
    with pytest.raises(QSXError, match="external"):
        create_instance("Rejected", manifest(selected), tmp_path / "instances")
    assert not (tmp_path / "instances").exists()


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
def test_relative_protocol_like_name_is_passed_as_absolute_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = image(tmp_path / "json:local.qcow2")
    monkeypatch.chdir(tmp_path)
    assert _check_qcow(Path(selected.name))["virtual-size"] == 1024**2


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
@pytest.mark.parametrize("payload", ["[]", "{", '{"format":"qcow2","format-specific":null}',
                                     '{"format":"qcow2","format-specific":{"data":null}}',
                                     '{"format":"qcow2","full-backing-filename":"data.raw"}'])
def test_invalid_native_inspection_is_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str,
) -> None:
    selected = image(tmp_path / "standalone.qcow2")
    monkeypatch.setattr(qcow, "run", lambda argv: payload)
    with pytest.raises(QSXError):
        _check_qcow(selected)


@pytest.mark.skipif(not shutil.which("qemu-img"), reason="requires local QEMU image tools")
@pytest.mark.parametrize("compression", ["zlib", "zstd"])
def test_populated_compressed_image_verifies_and_creates_private_instance(
    tmp_path: Path, compression: str,
) -> None:
    raw = tmp_path / "synthetic.raw"
    raw.write_bytes(b"synthetic bytes!" * 65536)
    selected = tmp_path / "compressed.qcow2"
    subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", "-c", "-o",
                    "compression_type=" + compression, str(raw), str(selected)],
                   check=True, capture_output=True)
    original_hash = sha256(selected)
    proof = manifest(selected)
    verify_image(proof)
    instance = create_instance("Compressed", proof, tmp_path / "instances")
    disk = Path(instance["instance_manifest"]).parent / "disk.qcow2"
    assert _check_qcow(disk)["virtual-size"] == 2 * 1024**3
    subprocess.run(["qemu-img", "compare", "-f", "raw", "-F", "qcow2", str(raw), str(disk)],
                   check=True, capture_output=True)
    assert sha256(selected) == original_hash
