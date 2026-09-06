from __future__ import annotations

import json
import os
import pty
import re
import select
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from qsx_alpine.backend import LibvirtBackend, domain_xml
from qsx_alpine.config import load_policy, resolve_resources
from qsx_alpine.console import console_lock, guest_command, run_serial
from qsx_alpine.errors import QSXError
from qsx_alpine.runtime import create_instance, load_instance, run_agent, run_guest, status_instance
from qsx_alpine.templates import _digest
from qsx_alpine.util import atomic_json, sha256


def test_domain_escapes_path_xml(tmp_path: Path) -> None:
    path = tmp_path / "a'&\"disk.qcow2"
    domain = ET.fromstring(domain_xml("Tiny", path))
    assert domain.find("./devices/disk/source").attrib["file"] == str(path)  # type: ignore[union-attr]


def test_console_lock_excludes_same_instance_but_not_second() -> None:
    with console_lock("test:///runtime-test", "One"):
        with pytest.raises(QSXError, match="another"), console_lock("test:///runtime-test", "One"):
            pass
        with console_lock("test:///runtime-test", "Two"):
            pass


def test_guest_command_quoting_and_nonroot_guard(tmp_path: Path) -> None:
    destination = tmp_path / "must-not-run"
    command = guest_command(["touch", str(destination)], "/workspace/project", "a" * 32)
    parsed = shlex.split(command.decode())
    assert parsed[:2] == ["sh", "-c"]
    assert "id -un" in parsed[2] and "id -u" in parsed[2]
    if subprocess.check_output(["id", "-un"], text=True).strip() != "agent":
        result = subprocess.run(parsed, text=True, capture_output=True, check=True)
        assert "QSX_END_" + "a" * 32 + ":126" in result.stdout
        assert not destination.exists()
    literal = "a; $(touch /tmp/should-not-execute)"
    quoted = shlex.split(
        guest_command(["printf", "%s", literal], "/workspace/project", "b" * 32).decode()
    )[2]
    assert shlex.quote(literal) in quoted


@pytest.mark.parametrize(
    "argv,cwd",
    [
        ([], "/workspace/project"),
        (["printf", "line\nline"], "/workspace/project"),
        (["id"], "/root"),
        (["id"], "/workspace/project/../../root"),
        (["printf", "x" * 4000], "/workspace/project"),
    ],
)
def test_guest_serial_rejects_unsafe_framing(argv: list[str], cwd: str) -> None:
    with pytest.raises(QSXError):
        guest_command(argv, cwd, "a" * 32)


def test_real_local_pty_transport_framing_contract() -> None:
    """An actual PTY carrying a synthetic peer is transport evidence, not guest boot."""
    master, slave = pty.openpty()
    errors: list[BaseException] = []

    def peer() -> None:
        try:
            data = b""
            while b"\n" not in data:
                readable, _, _ = select.select([master], [], [], 3)
                assert readable
                data += os.read(master, 65536)
            script = shlex.split(data.decode())[2]
            match = re.search(r"QSX_BEGIN_ ([a-f0-9]{32})", script)
            assert match
            nonce = match.group(1).encode()
            os.write(master, data + b"\r\nQSX_BEGIN_" + nonce[:10])
            os.write(master, nonce[10:] + b"\r\nhello guest\r\nQSX_END_")
            os.write(master, nonce + b":7\r\n")
        except (AssertionError, OSError, IndexError, ValueError) as exc:
            errors.append(exc)

    worker = threading.Thread(target=peer, daemon=True)
    worker.start()
    try:
        result = run_serial(os.ttyname(slave), ["id"], "/workspace/project", 5)
        assert result["stdout"] == "hello guest" and result["returncode"] == 7
        assert result["evidence_status"] == "GUEST_REPORTED"
    finally:
        worker.join(timeout=5)
        os.close(master)
        os.close(slave)
    assert not errors


def test_serial_timeout_is_unknown_execution_not_success() -> None:
    master, slave = pty.openpty()
    try:
        with pytest.raises(QSXError, match="execution state is unknown"):
            run_serial(os.ttyname(slave), ["id"], "/workspace/project", 1)
    finally:
        os.close(master)
        os.close(slave)


def test_network_cut_live_and_persistent_contract() -> None:
    class Peer(LibvirtBackend):
        def __init__(self) -> None:
            super().__init__("test:///runtime")
            self.calls: list[tuple[str, ...]] = []
            self.interfaces = {"--live": True, "--config": True}
            self.live_down = False

        def _virsh(self, *args: str) -> str:
            self.calls.append(args)
            if args[0] == "domstate":
                return "running"
            if args[0] == "dumpxml":
                scope = "--config" if "--inactive" in args else "--live"
                interface = (
                    (
                        "<interface type='network'><mac address='52:54:00:11:22:33'/>"
                        "<target dev='vnet0'/></interface>"
                    )
                    if self.interfaces[scope]
                    else ""
                )
                if scope == "--live" and self.live_down:
                    interface = interface.replace(
                        "</interface>", "<link state='down'/></interface>"
                    )
                return f"<domain><devices>{interface}</devices></domain>"
            if args[0] == "domif-setlink":
                assert args == ("domif-setlink", "Tiny", "52:54:00:11:22:33", "down")
                self.live_down = True
            if args[0] == "detach-device":
                assert (
                    "domif-setlink",
                    "Tiny",
                    "52:54:00:11:22:33",
                    "down",
                ) in self.calls
                assert Path(args[2]).is_file()
                self.interfaces[args[-1]] = False
            return ""

    peer = Peer()
    result = peer.network_cut("Tiny")
    assert result["network_profile"] == "offline"
    assert result["removed_device_definitions"] == 1
    assert result["disabled_live_interfaces"] == 1
    assert peer.live_down and not peer.interfaces["--config"]


def test_interactive_launch_real_local_pty_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from qsx_alpine.console import interactive_serial

    guest_master, guest_slave = pty.openpty()
    local_master, local_slave = pty.openpty()
    errors: list[BaseException] = []

    def peer() -> None:
        try:
            for output in (b"", b"interactive-ready"):
                data = b""
                while b"\n" not in data:
                    readable, _, _ = select.select([guest_master], [], [], 3)
                    assert readable
                    data += os.read(guest_master, 65536)
                script = shlex.split(data.decode())[2]
                match = re.search(r"QSX_BEGIN_ ([a-f0-9]{32})", script)
                assert match
                nonce = match.group(1).encode()
                os.write(
                    guest_master,
                    b"\nQSX_BEGIN_" + nonce + b"\n" + output + b"\nQSX_END_" + nonce + b":0\n",
                )
        except (AssertionError, OSError, IndexError, ValueError) as exc:
            errors.append(exc)

    input_stream = os.fdopen(os.dup(local_slave), "r")
    output_stream = os.fdopen(os.dup(local_slave), "w")
    worker = threading.Thread(target=peer, daemon=True)
    worker.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", input_stream)
            patch.setattr(sys, "stdout", output_stream)
            assert (
                interactive_serial(
                    os.ttyname(guest_slave),
                    ["printf", "interactive-ready"],
                    "/workspace/project",
                )
                == 0
            )
        readable, _, _ = select.select([local_master], [], [], 1)
        assert readable and b"interactive-ready" in os.read(local_master, 65536)
    finally:
        worker.join(timeout=5)
        input_stream.close()
        output_stream.close()
        for fd in (guest_master, guest_slave, local_master, local_slave):
            os.close(fd)
    assert not errors


def fixture_image(tmp_path: Path) -> Path:
    """Hash-valid nonbootable synthetic image, strictly offline storage test data."""
    image = tmp_path / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", image, "1G"], check=True)
    log = tmp_path / "base.boot.log"
    log.write_text("SYNTHETIC_READY_FIXTURE (not VM boot evidence)\n")
    manifest = {
        "schema_version": 1,
        "image": image.name,
        "image_sha256": sha256(image),
        "size_bytes": image.stat().st_size,
        "boot_log": log.name,
        "boot_log_sha256": sha256(log),
        "readiness_marker": "SYNTHETIC_READY_FIXTURE",
        "resources": {"memory_mib": 512, "vcpus": 1, "disk_gib": 2},
        "agent_argv": ["python3", "-q"],
        "agent_profile_sha256": "a" * 64,
        "recipe_sha256": "b" * 64,
    }
    manifest["manifest_sha256"] = _digest(manifest)
    path = tmp_path / "base.qcow2.manifest.json"
    atomic_json(path, manifest)
    return path


def test_operator_policy_limits_cannot_be_granted_by_recipe(tmp_path: Path) -> None:
    policy = load_policy()
    assert policy["limits"] == {"memory_mib": 4096, "vcpus": 4, "disk_gib": 64}
    with pytest.raises(QSXError, match="policy"):
        resolve_resources({"memory_mib": 8192}, None, policy)
    custom = tmp_path / "operator.toml"
    custom.write_text("schema_version=1\n[limits]\nmemory_mib=8192\n")
    assert resolve_resources({"memory_mib": 8192}, None, load_policy(custom))["memory_mib"] == 8192
    custom.write_text('network_profile="openai"\n')
    with pytest.raises(QSXError, match="transport"):
        load_policy(custom)


def test_real_private_instances_preserve_source_and_independence(tmp_path: Path) -> None:
    image_manifest = fixture_image(tmp_path)
    base_hash = sha256(tmp_path / "base.qcow2")
    source = tmp_path / "source"
    source.mkdir()
    (source / "script.sh").write_text("#!/bin/sh\necho SOURCE_MUST_NOT_RUN\n")
    (source / "script.sh").chmod(0o751)
    (source / "subdir").mkdir(mode=0o750)
    (source / "subdir/link").symlink_to("../script.sh")
    (source / ".env").write_text("SYNTHETIC_SECRET_FIXTURE=redact\n")
    subprocess.run(["git", "init", source], check=True, capture_output=True)
    (source / ".git/config").write_text("[core]\nrepositoryformatversion=0\n")
    source_before = {
        str(p.relative_to(source)): sha256(p) for p in source.rglob("*") if p.is_file()
    }
    instances = [
        create_instance(name, image_manifest, tmp_path / "instances", source)
        for name in ("One", "Two")
    ]
    one, two = [load_instance(item["instance_manifest"]) for item in instances]
    assert one.metadata["instance_id"] != two.metadata["instance_id"]
    second_hash = sha256(two.disk)
    with one.disk.open("ab") as stream:
        stream.write(b"private synthetic write")
    assert sha256(two.disk) == second_hash and sha256(tmp_path / "base.qcow2") == base_hash
    assert source_before == {
        str(p.relative_to(source)): sha256(p) for p in source.rglob("*") if p.is_file()
    }
    info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", two.disk]))
    assert info["virtual-size"] == 2 * 1024**3 and not info.get("backing-filename")
    extracted = tmp_path / "seed"
    subprocess.run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(two.directory / "import.iso"),
            "-extract",
            "/",
            str(extracted),
        ],
        check=True,
        capture_output=True,
    )
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    subprocess.run(
        [
            "tar",
            "--same-permissions",
            "-xf",
            str(extracted / "project.tar"),
            "-C",
            str(unpacked),
            "--strip-components=1",
        ],
        check=True,
    )
    assert not (unpacked / ".git").exists() and not (unpacked / ".env").exists()
    assert (unpacked / "script.sh").stat().st_mode & 0o777 == 0o751
    assert (unpacked / "subdir").stat().st_mode & 0o777 == 0o750
    assert (unpacked / "subdir/link").readlink() == Path("../script.sh")
    assert two.metadata["instance_id"] in (extracted / "meta-data").read_text()
    assert "resize_rootfs: true" in (extracted / "user-data").read_text()
    assert status_instance(two.directory)["state"] == "not defined"
    assert two.metadata["measurements"]["guest_boot"] == "NOT_RUN"
    with pytest.raises(QSXError, match="finalize"):
        run_guest(two.directory, ["id"])
    with pytest.raises(QSXError, match="finalize"):
        run_agent(two.directory)
    with pytest.raises(QSXError, match="overwrite"):
        create_instance("Two", image_manifest, tmp_path / "instances", source)


def test_instance_refuses_escape_source_link_and_resource_grant(tmp_path: Path) -> None:
    image_manifest = fixture_image(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "bad").symlink_to("../../outside")
    with pytest.raises(QSXError, match="symlink"):
        create_instance("Bad", image_manifest, tmp_path / "instances", source)
    with pytest.raises(QSXError, match="policy"):
        create_instance("Huge", image_manifest, tmp_path / "instances", resources={"vcpus": 32})
    with pytest.raises(QSXError, match="boundaries"):
        create_instance("Overlap", image_manifest, source / "instance", source)


def test_libvirt_subprocess_console_uses_authorized_stream_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual subprocess + PTY with a synthetic virsh peer; no real hypervisor."""
    fake = tmp_path / "virsh"
    fake.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os,re,shlex,sys
if "domstate" in sys.argv:
    print("running")
    sys.exit(0)
assert "console" in sys.argv and "--safe" in sys.argv
print("Connected to domain Tiny\\nEscape character is ^]", flush=True)
data=b""
while True:
    value=os.read(0,65536)
    if not value or b"\\x1d" in value:
        break
    data+=value
    if b"\\n" in data:
        command,data=data.split(b"\\n",1)
        script=shlex.split(command.decode())[2]
        nonce=re.search(r"QSX_BEGIN_ ([a-f0-9]{32})",script).group(1)
        print("\\nQSX_BEGIN_"+nonce+"\\nstream-ready\\nQSX_END_"+nonce+":0",flush=True)
"""
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = LibvirtBackend().run_guest("Tiny", ["id"], "/workspace/project", 5)
    assert result["stdout"] == "stream-ready" and result["returncode"] == 0


def test_import_finalization_resumes_after_stopped_guest_detach_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qsx_alpine.runtime import finalize_import

    data = create_instance("Retry", fixture_image(tmp_path), tmp_path / "instances")
    state = {"active": True, "runs": 0, "detach": 0}

    class Peer:
        def run_guest(self, *args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            state["runs"] += 1
            return {
                "stdout": "synthetic readiness",
                "returncode": 0,
                "evidence_status": "GUEST_REPORTED",
            }

        def is_active(self, name: str) -> bool:
            del name
            return bool(state["active"])

        def shutdown(self, name: str) -> None:
            del name
            state["active"] = False

        def detach_import_medium(self, name: str, medium: Path) -> None:
            del name, medium
            state["detach"] += 1
            if state["detach"] == 1:
                raise QSXError("synthetic detach interruption")

        def status(self, name: str) -> str:
            del name
            return "shut off"

    monkeypatch.setattr("qsx_alpine.runtime.LibvirtBackend", Peer)
    with pytest.raises(QSXError, match="interruption"):
        finalize_import(data["instance_manifest"])
    assert not state["active"]
    result = finalize_import(data["instance_manifest"])
    assert result["import_finalized"] and state["runs"] == 1
    assert load_instance(data["instance_manifest"]).metadata["import_finalized"]
