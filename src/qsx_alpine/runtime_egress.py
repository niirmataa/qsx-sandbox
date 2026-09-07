"""Explicit instance egress policy, host readback and private guest seed.

No function deploys host services, firewall rules, routes or privileges.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from .errors import QSXError
from .util import run

if TYPE_CHECKING:
    from .backend import LibvirtBackend

PROFILE = "openai-chatgpt-proxy"
NETWORK = "qsx-codex-egress"
BRIDGE = "qsx-egress0"
PROXY = "http://192.168.250.1:3128"
CLIENT = "192.168.250.2"
CIDR = CLIENT + "/30"
HOSTS = ["auth.openai.com", "chatgpt.com"]
HELPER = Path("/usr/local/sbin/qsx-egress-verify")
MAC = re.compile(r"52:54:00(?::[0-9a-f]{2}){3}")
EXPECTED = {
    "schema_version": 1, "network_profile": PROFILE, "network_name": NETWORK,
    "bridge": BRIDGE, "proxy_url": PROXY, "guest_cidr": CIDR, "allowed_hosts": HOSTS,
}


def policy_digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def load_egress_policy(path: Path, instance_id: str) -> dict[str, Any]:
    """Separate explicit grant for one instance; not an image/resource policy."""
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise QSXError(f"cannot read instance egress policy: {exc}") from exc
    expected = {**EXPECTED, "instance_id": instance_id}
    if raw != expected or type(raw.get("schema_version")) is not int:
        raise QSXError(
            "egress policy must explicitly select this instance_id and the reviewed "
            "openai-chatgpt-proxy transport with its exact addresses and allowed_hosts"
        )
    return raw


def _root_owned_helper() -> None:
    for path in (HELPER, *HELPER.parents):
        try:
            info = path.lstat()
        except OSError as exc:
            raise QSXError("approved root-owned read-only egress verifier is not installed") from exc
        if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)
                or (path == HELPER and not stat.S_ISREG(info.st_mode))):
            raise QSXError("egress verifier and its parents must be root-owned and protected")
    if not os.access(HELPER, os.X_OK):
        raise QSXError("approved egress verifier is not executable")


def verify_host() -> dict[str, Any]:
    """Run only the separately installed, argument-free read-only root helper."""
    _root_owned_helper()
    try:
        completed = subprocess.run(
            ["/usr/bin/sudo", "-n", str(HELPER)], capture_output=True, text=True,
            timeout=30, check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QSXError("cannot obtain current enforced egress deployment readback") from exc
    if completed.returncode:
        reason = completed.stderr.strip()[:4096]
        try:
            failure = json.loads(completed.stdout)
            if isinstance(failure, dict) and isinstance(failure.get("reason"), str):
                reason = failure["reason"][:4096]
        except ValueError:
            pass
        raise QSXError("approved egress verifier refused the deployment: " + reason)
    try:
        data = json.loads(completed.stdout)
    except ValueError as exc:
        raise QSXError("egress verifier returned invalid JSON") from exc
    expected = {"schema_version": 1, "status": "PASS", "network_name": NETWORK,
                "bridge": BRIDGE, "proxy_url": PROXY, "client_address": CLIENT,
                "allowed_hosts": HOSTS}
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in expected.items()):
        raise QSXError("egress verifier readback does not match the selected transport")
    if (data.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("deployment_sha256", "")))):
        raise QSXError("egress verifier returned stale or incomplete deployment evidence")
    return data


def check_domain(root: ET.Element, mac: str | None = None) -> None:
    if root.findall("./devices/hostdev") or root.findall(
        "./{http://libvirt.org/schemas/domain/qemu/1.0}commandline"
    ):
        raise QSXError("unmanaged host devices or QEMU commandline prevent enforced networking")
    interfaces = root.findall("./devices/interface")
    if mac is None:
        if interfaces:
            raise QSXError("offline startup refuses network interfaces; run network-cut")
        return
    if not MAC.fullmatch(mac) or len(interfaces) != 1:
        raise QSXError("egress requires exactly the granted network interface")
    interface = interfaces[0]
    source, address, model, link = (
        interface.find(tag) for tag in ("source", "mac", "model", "link")
    )
    if (interface.attrib != {"type": "network"} or source is None
            or source.get("network") != NETWORK
            or set(source.attrib) - {"network", "bridge", "portid"}
            or source.get("bridge", BRIDGE) != BRIDGE
            or address is None or address.get("address") != mac
            or model is None or model.get("type") != "virtio"
            or (link is not None and link.get("state") != "up")):
        raise QSXError("domain interface differs from the explicit egress grant")
    allowed = {"mac", "source", "model", "link", "target", "alias", "address"}
    if {child.tag for child in interface} - allowed:
        raise QSXError("unreviewed interface settings prevent enforced egress")


def _check_network_xml(xml: str) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise QSXError("invalid egress network XML") from exc
    if (root.tag != "network" or set(root.attrib) - {"connections"}
            or {c.tag for c in root} - {"name", "uuid", "bridge", "mac", "dns", "ip"}):
        raise QSXError("egress network must not have forwarding or additional services")
    bridge, dns, address = (root.find(tag) for tag in ("bridge", "dns", "ip"))
    if (root.findtext("name") != NETWORK or len(root.findall("ip")) != 1
            or len(root.findall("bridge")) != 1 or len(root.findall("dns")) != 1
            or bridge is None or bridge.attrib != {"name": BRIDGE, "stp": "off", "delay": "0"}
            or dns is None or dns.attrib != {"enable": "no"} or list(dns)
            or address is None
            or address.attrib != {"address": "192.168.250.1", "netmask": "255.255.255.252"}
            or list(address)):
        raise QSXError("egress network addresses, bridge or DNS differ from reviewed deployment")


def verify_network(backend: LibvirtBackend, name: str) -> dict[str, Any]:
    if backend.uri != "qemu:///system":
        raise QSXError("egress requires the reviewed system libvirt connection")
    info = {
        key.strip(): value.strip()
        for line in backend._virsh("net-info", NETWORK).splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    if (info.get("Active") != "yes" or info.get("Persistent") != "yes"
            or info.get("Autostart") not in {"no", "disable"} or info.get("Bridge") != BRIDGE):
        raise QSXError("egress network must be active, persistent and without autostart")
    _check_network_xml(backend._virsh("net-dumpxml", NETWORK))
    _check_network_xml(backend._virsh("net-dumpxml", NETWORK, "--inactive"))
    for other in backend._virsh("list", "--all", "--name").splitlines():
        if not other or other == name:
            continue
        domains = [backend._domain(other, inactive=True)]
        if backend.is_active(other):
            domains.append(backend._domain(other))
        for domain in domains:
            for source in domain.findall("./devices/interface/source"):
                if source.get("network") == NETWORK or source.get("bridge") == BRIDGE:
                    raise QSXError("egress network is already assigned to another instance")
    return verify_host()


def interface_xml(mac: str) -> str:
    if not MAC.fullmatch(mac):
        raise QSXError("invalid granted interface MAC")
    interface = ET.Element("interface", {"type": "network"})
    ET.SubElement(interface, "mac", {"address": mac})
    ET.SubElement(interface, "source", {"network": NETWORK})
    ET.SubElement(interface, "model", {"type": "virtio"})
    ET.SubElement(interface, "link", {"state": "up"})
    return ET.tostring(interface, encoding="unicode")


def guest_configuration(instance_id: str, nonce: str, mac: str) -> tuple[str, str]:
    if (not re.fullmatch(r"qsx-[0-9a-f-]{36}", instance_id)
            or not re.fullmatch(r"[0-9a-f]{32}", nonce) or not MAC.fullmatch(mac)):
        raise QSXError("invalid private egress configuration identity")
    marker = json.dumps({"instance_id": instance_id, "nonce": nonce, "mac": mac,
                         "proxy_url": PROXY, "guest_cidr": CIDR}, sort_keys=True) + "\n"
    network = (
        "#!/bin/sh\nset -eu\n"
        "for device in /sys/class/net/*; do\n"
        f'  [ "$(cat "$device/address")" = "{mac}" ] || continue\n'
        '  interface=${device##*/}\n'
        '  ip link set "$interface" down\n'
        '  ip -4 addr flush dev "$interface"\n'
        '  sysctl -w "net.ipv6.conf.$interface.disable_ipv6=1" >/dev/null\n'
        f'  ip addr add {CIDR} dev "$interface"\n'
        '  ip link set "$interface" up\n'
        "done\n"
    )
    environment = (
        f"export HTTP_PROXY={PROXY} HTTPS_PROXY={PROXY}\n"
        f"export http_proxy={PROXY} https_proxy={PROXY}\n"
        "unset ALL_PROXY all_proxy\nexport NO_PROXY= no_proxy=\n"
    )
    files = [
        ("/etc/network/interfaces", "auto lo\niface lo inet loopback\n", "0644"),
        ("/etc/cloud/cloud.cfg.d/98-qsx-egress-network.cfg", "network: {config: disabled}\n", "0644"),
        ("/etc/local.d/qsx-egress.start", network, "0755"),
        ("/etc/profile.d/qsx-egress.sh", environment, "0644"),
    ]
    lines = ["#!/bin/sh", "set -eu", "umask 077", 'test "$(id -u)" = 0',
             f'test "$(cat /var/lib/qsx-instance-id)" = "{instance_id}"',
             "mkdir -p /etc/local.d /etc/profile.d /etc/cloud/cloud.cfg.d"]
    for filename, content, mode in files:
        encoded = base64.b64encode(content.encode()).decode()
        lines += [f"printf '%s' '{encoded}' | base64 -d > {filename}", f"chmod {mode} {filename}"]
    encoded = base64.b64encode(marker.encode()).decode()
    lines += ["rc-update add local default",
              f"printf '%s' '{encoded}' | base64 -d > /var/lib/qsx-egress-configuration.json",
              "chmod 0644 /var/lib/qsx-egress-configuration.json", "sync"]
    return "\n".join(lines) + "\n", hashlib.sha256(marker.encode()).hexdigest()


def make_seed(destination: Path, instance_id: str, nonce: str, mac: str) -> str:
    script, marker_hash = guest_configuration(instance_id, nonce, mac)
    if destination.exists():
        raise QSXError("private egress medium already exists; refusing overwrite")
    with tempfile.TemporaryDirectory(prefix=".egress-seed-", dir=destination.parent) as temporary:
        root = Path(temporary)
        (root / "configure.sh").write_text(script)
        (root / "meta-data").write_text(f"instance-id: {instance_id}-egress-{nonce}\n")
        (root / "network-config").write_text("version: 1\nconfig: []\n")
        (root / "user-data").write_text(
            "#cloud-config\nusers: []\ndisable_root: true\nssh_deletekeys: false\nssh_genkeytypes: []\n"
            "runcmd:\n  - [mkdir, -p, /mnt/qsx-egress]\n"
            "  - [mount, -o, ro, 'LABEL=cidata', /mnt/qsx-egress]\n"
            "  - [sh, /mnt/qsx-egress/configure.sh]\n"
            "  - [umount, /mnt/qsx-egress]\n"
        )
        run(["xorriso", "-as", "mkisofs", "-quiet", "-R", "-V", "cidata", "-o",
             str(destination), str(root)])
    destination.chmod(0o600)
    return marker_hash
