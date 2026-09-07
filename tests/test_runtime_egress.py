"""Policy/lifecycle contracts with a synthetic libvirt peer, never VM acceptance."""
from __future__ import annotations

import json
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from qsx_alpine import runtime
from qsx_alpine import runtime_egress as egress
from qsx_alpine.backend import LibvirtBackend
from qsx_alpine.cli import parser
from qsx_alpine.errors import QSXError
from qsx_alpine.util import atomic_json

INSTANCE_ID = "qsx-" + str(uuid.UUID(int=1))
MAC = "52:54:00:11:22:33"
ROOT = Path(__file__).resolve().parents[1]


def policy_file(tmp_path: Path, identity: str = INSTANCE_ID) -> Path:
    data = {**egress.EXPECTED, "instance_id": identity}
    destination = tmp_path / "operator-egress.toml"
    destination.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in data.items()))
    return destination


def fixture_instance(tmp_path: Path) -> Path:
    directory = tmp_path / "instance"
    directory.mkdir()
    (directory / "disk.qcow2").write_bytes(b"synthetic nonbootable fixture")
    (directory / "domain.xml").write_text("<domain><name>Tiny</name><devices/></domain>")
    atomic_json(directory / "instance.json", {
        "schema_version": 1, "name": "Tiny", "instance_id": INSTANCE_ID,
        "workspace": runtime.WORKSPACE, "resources": {"vcpus": 1, "memory_mib": 512, "disk_gib": 2},
        "network_profile": "offline", "network_cut_latched": True,
        "domain_defined": True, "import_finalized": True, "measurements": {},
    })
    return directory


class Peer(LibvirtBackend):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []
        self.active = False
        self.nic = False
        self.mac = MAC
        self.down = False
        self.medium: Path | None = None
        self.second_instance = False
        self.network = (ROOT / "deploy/egress/network.xml").read_text()
        self.autostart = "no"
        self.cut_failure = False
        self.guest_runs = 0
        self.last_guest_argv: list[str] = []
        self.observation_code = 0
        self.detach_failure = False

    def _virsh(self, *args: str) -> str:
        self.calls.append(args)
        command = args[0]
        if command == "domstate":
            return "running" if self.active else "shut off"
        if command == "net-info":
            return ("Active: yes\nPersistent: yes\nBridge: qsx-egress0\n"
                    f"Autostart: {self.autostart}")
        if command == "net-dumpxml":
            return self.network
        if command == "list":
            return "Tiny\nOther" if self.second_instance else "Tiny"
        if command == "dumpxml":
            root = ET.fromstring("<domain><name>Tiny</name><devices/></domain>")
            devices = root.find("devices")
            assert devices is not None
            if self.nic or args[1] == "Other":
                interface = ET.fromstring(egress.interface_xml(self.mac))
                if self.down and "--inactive" not in args:
                    link = interface.find("link")
                    assert link is not None
                    link.set("state", "down")
                devices.append(interface)
            if self.medium is not None:
                disk = ET.SubElement(devices, "disk", {"device": "cdrom", "type": "file"})
                ET.SubElement(disk, "source", {"file": str(self.medium)})
                ET.SubElement(disk, "target", {"dev": "sdb"})
                ET.SubElement(disk, "readonly")
            return ET.tostring(root, encoding="unicode")
        if command == "attach-device":
            assert Path(args[2]).read_text() == egress.interface_xml(self.mac)
            self.nic = True
        elif command == "attach-disk":
            self.medium = Path(args[2])
        elif command == "detach-disk":
            if self.detach_failure:
                self.detach_failure = False
                raise QSXError("synthetic interrupted medium detach")
            self.medium = None
        elif command == "domif-setlink":
            if self.cut_failure:
                raise QSXError("synthetic link failure")
            self.down = True
        elif command == "detach-device":
            self.nic = False
        elif command == "start":
            self.active = True
        elif command == "shutdown":
            self.active = False
        return ""

    def run_guest(self, name: str, argv: list[str], cwd: str, timeout: int = 120) -> dict[str, object]:
        del name, timeout
        assert self.active and cwd == runtime.WORKSPACE
        assert argv[:2] == ["sh", "-c"] and "id -un" in argv[2]
        self.guest_runs += 1
        self.last_guest_argv = argv
        return {"returncode": self.observation_code, "stdout": "synthetic public marker",
                "evidence_status": "GUEST_REPORTED"}


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    readback = {"boot_id": "test-boot", "deployment_sha256": "a" * 64}
    monkeypatch.setattr(egress, "verify_host", lambda: readback.copy())
    original_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: path == Path("/dev/kvm") or original_exists(path))
    return readback


def test_policy_is_explicit_separate_and_bound_to_one_instance(tmp_path: Path) -> None:
    from qsx_alpine.config import load_policy

    path = policy_file(tmp_path)
    assert egress.load_egress_policy(path, INSTANCE_ID)["network_profile"] == egress.PROFILE
    with pytest.raises(QSXError, match="policy"):
        load_policy(path)
    with pytest.raises(QSXError, match="instance_id"):
        egress.load_egress_policy(path, "qsx-" + str(uuid.UUID(int=2)))
    for modification in ('\nextra="silent-option"', '\n[limits]\nvcpus=99'):
        path = policy_file(tmp_path)
        path.write_text(path.read_text() + modification)
        with pytest.raises(QSXError):
            egress.load_egress_policy(path, INSTANCE_ID)


@pytest.mark.parametrize("replacement", [
    ("<dns enable='no'/>", "<dns enable='yes'/>"),
    ("<network>", "<network ipv6='yes'>"),
    ("</network>", "<forward mode='nat'/></network>"),
    ("192.168.250.1", "192.168.250.9"),
    ("</network>", "<ip address='192.168.250.1'><dhcp/></ip></network>"),
])
def test_network_configuration_drift_denies_before_start(
    replacement: tuple[str, str], host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    peer.network = peer.network.replace(*replacement)
    with pytest.raises(QSXError):
        egress.verify_network(peer, "Tiny")
    assert not any(call[0] == "start" for call in peer.calls)


def test_network_exclusivity_and_autostart_are_checked(host: dict[str, Any]) -> None:
    del host
    peer = Peer()
    peer.second_instance = True
    with pytest.raises(QSXError, match="another"):
        egress.verify_network(peer, "Tiny")
    peer.second_instance = False
    peer.autostart = "yes"
    with pytest.raises(QSXError, match="autostart"):
        egress.verify_network(peer, "Tiny")


def test_offline_and_egress_both_reject_qemu_commandline_bypass() -> None:
    root = ET.fromstring("<domain xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>"
                         "<devices/><qemu:commandline><qemu:arg value='-nic'/></qemu:commandline></domain>")
    for mac in (None, MAC):
        with pytest.raises(QSXError, match="commandline"):
            egress.check_domain(root, mac)


def test_private_seed_contains_only_configuration_and_is_imported_offline(tmp_path: Path) -> None:
    medium = tmp_path / "configuration.iso"
    marker_hash = egress.make_seed(medium, INSTANCE_ID, "f" * 32, MAC)
    assert medium.stat().st_mode & 0o777 == 0o600
    extracted = tmp_path / "extracted"
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(medium), "-extract", "/", str(extracted)],
                   capture_output=True, check=True)
    script = (extracted / "configure.sh").read_text()
    subprocess.run(["sh", "-n", str(extracted / "configure.sh")], check=True)
    assert INSTANCE_ID in script and 'test "$(id -u)" = 0' in script
    assert "/etc/profile.d/qsx-egress.sh" in script
    assert "/home/agent/" not in script  # root setup never follows agent-controlled symlinks
    assert "network: {config: disabled}" not in script  # file contents safely encoded
    assert "/var/lib/qsx-egress-configuration.json" in script
    assert egress.guest_configuration(INSTANCE_ID, "f" * 32, MAC)[1] == marker_hash
    assert "config: []" in (extracted / "network-config").read_text()
    assert "auth.json" not in script and "project.tar" not in script
    with pytest.raises(QSXError, match="overwrite"):
        egress.make_seed(medium, INSTANCE_ID, "f" * 32, MAC)


def prepared(tmp_path: Path, peer: Peer, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    directory = fixture_instance(tmp_path)
    policy = policy_file(tmp_path)
    monkeypatch.setattr(runtime, "LibvirtBackend", lambda: peer)
    runtime.prepare_network(directory, policy)
    data = runtime.load_instance(directory).metadata
    peer.mac = data["egress_pending"]["mac"]
    peer.active = True
    return directory, policy


def test_grant_checks_private_import_then_shutdown_detach_and_nic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    result = runtime.grant_network(directory, policy)
    data = runtime.load_instance(directory).metadata
    assert result["status"] == "EXPLICIT_GRANT_VERIFIED"
    assert data["egress_grant"]["boot_id"] == host["boot_id"]
    assert data["network_cut_latched"] is False and not peer.active and peer.nic
    commands = [call[0] for call in peer.calls]
    assert commands.index("shutdown") < commands.index("detach-disk") < commands.index("attach-device")
    assert peer.medium is None and peer.guest_runs == 1
    with pytest.raises(QSXError, match="network-prepare"):
        runtime.grant_network(directory, policy)


def test_grant_resumes_detach_failure_without_reexecuting_guest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    peer.detach_failure = True
    with pytest.raises(QSXError, match="interrupted"):
        runtime.grant_network(directory, policy)
    assert not peer.active and not peer.nic
    runtime.grant_network(directory, policy)
    assert peer.guest_runs == 1 and peer.nic


def test_unready_guest_or_unavailable_host_never_gets_nic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    peer.observation_code = 1
    with pytest.raises(QSXError, match="not ready"):
        runtime.grant_network(directory, policy)
    assert peer.active and not peer.nic
    def denied() -> dict[str, Any]:
        raise QSXError("approved host unavailable")
    monkeypatch.setattr(egress, "verify_host", denied)
    with pytest.raises(QSXError, match="host unavailable"):
        runtime.grant_network(directory, policy)
    assert peer.guest_runs == 1 and not peer.nic


def test_network_cut_latches_before_failure_and_restart_never_regrants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    runtime.grant_network(directory, policy)
    peer.active, peer.cut_failure = True, True
    with pytest.raises(QSXError, match="link failure"):
        runtime.network_cut(directory)
    data = runtime.load_instance(directory).metadata
    assert data["network_profile"] == "offline" and data["network_cut_latched"]
    assert data["egress_grant"]["revoked_at_unix"] > 0
    peer.active = False
    with pytest.raises(QSXError, match="offline"):
        runtime.start_instance(directory)
    assert not any(call[0] == "start" for call in peer.calls)
    peer.active, peer.cut_failure = True, False
    runtime.network_cut(directory)
    assert peer.down and not peer.nic
    peer.active = False
    runtime.start_instance(directory)
    assert peer.active and not peer.nic
    with pytest.raises(QSXError, match="network-prepare"):
        runtime.grant_network(directory, policy)


def test_start_rechecks_grant_boot_deployment_and_current_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    runtime.grant_network(directory, policy)
    host["boot_id"] = "rebooted"
    with pytest.raises(QSXError, match="host boot changed"):
        runtime.start_instance(directory)
    assert not peer.active
    host["boot_id"] = "test-boot"
    host["deployment_sha256"] = "b" * 64
    with pytest.raises(QSXError, match="deployment"):
        runtime.start_instance(directory)
    policy.write_text(policy.read_text().replace("chatgpt.com", "example.com"))
    with pytest.raises(QSXError, match="policy"):
        runtime.start_instance(directory)
    assert not any(call[0] == "start" for call in peer.calls)


def test_cli_never_accepts_implicit_egress_grant() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["network-grant", "/instance"])
    args = parser().parse_args(["network-prepare", "/instance", "--policy", "/policy.toml"])
    assert args.policy == Path("/policy.toml")


def test_helper_ownership_is_required_before_any_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    monkeypatch.setattr(egress, "HELPER", helper)
    called: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: called.append(args))
    with pytest.raises(QSXError, match="root-owned"):
        egress.verify_host()
    assert not called


def test_guest_observation_shell_checks_exact_marker_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    directory, policy = prepared(tmp_path, peer, monkeypatch)
    pending = runtime.load_instance(directory).metadata["egress_pending"]
    runtime.grant_network(directory, policy)
    marker = tmp_path / "marker with space.json"
    marker.write_text(json.dumps({
        "instance_id": INSTANCE_ID, "nonce": pending["nonce"], "mac": pending["mac"],
        "proxy_url": egress.PROXY, "guest_cidr": egress.CIDR,
    }, sort_keys=True) + "\n")
    # Exercise the generated shell's hash comparison on synthetic local data.
    # The separate console contract tests exercise its non-root identity guard.
    script = peer.last_guest_argv[2].split(" && ", 2)[2]
    script = script.replace("/var/lib/qsx-egress-configuration.json", shlex.quote(str(marker)))
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and json.loads(result.stdout)["instance_id"] == INSTANCE_ID
    marker.write_text("wrong marker\n")
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode != 0 and result.stdout == ""


def test_helper_failure_retains_fixed_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "_root_owned_helper", lambda: None)
    completed = subprocess.CompletedProcess([], 1, '{"status":"FAIL","reason":"firewall differs"}', "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(QSXError, match="firewall differs"):
        egress.verify_host()


def test_helper_invocation_is_fixed_and_boot_readback_must_be_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "_root_owned_helper", lambda: None)
    data = {
        "schema_version": 1, "status": "PASS", "network_name": egress.NETWORK,
        "bridge": egress.BRIDGE, "proxy_url": egress.PROXY, "client_address": egress.CLIENT,
        "allowed_hosts": egress.HOSTS, "deployment_sha256": "a" * 64,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }
    calls: list[list[str]] = []
    def execute(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
    monkeypatch.setattr(subprocess, "run", execute)
    assert egress.verify_host()["status"] == "PASS"
    assert calls == [["/usr/bin/sudo", "-n", "/usr/local/sbin/qsx-egress-verify"]]
    data["boot_id"] = "old-host-boot"
    with pytest.raises(QSXError, match="stale"):
        egress.verify_host()


def test_start_checks_private_configuration_integrity_before_guest_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: dict[str, Any],
) -> None:
    del host
    peer = Peer()
    directory, _policy = prepared(tmp_path, peer, monkeypatch)
    peer.active = False
    pending = runtime.load_instance(directory).metadata["egress_pending"]
    medium = directory / pending["medium"]
    with medium.open("ab") as stream:
        stream.write(b"changed private configuration")
    with pytest.raises(QSXError, match="medium changed before startup"):
        runtime.start_instance(directory)
    assert not peer.active and not any(call[0] == "start" for call in peer.calls)
