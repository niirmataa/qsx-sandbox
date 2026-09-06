from __future__ import annotations

import os
import pty
import select
import shlex
import shutil
import subprocess
import tempfile
import time
import tty
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from .config import SAFE_NAME
from .console import console_lock, interactive_serial, run_serial
from .errors import QSXError


class LibvirtBackend:
    """Production adapter: explicit virsh calls; never a fallback to a fake driver."""

    def __init__(self, uri: str = "qemu:///system") -> None:
        self.uri = uri

    def _virsh(self, *args: str) -> str:
        if not shutil.which("virsh"):
            raise QSXError("libvirt client is unavailable")
        try:
            result = subprocess.run(
                ["virsh", "--connect", self.uri, *args],
                text=True,
                capture_output=True,
                env={**os.environ, "LC_ALL": "C"},
                check=False,
            )
        except OSError as exc:
            raise QSXError(f"cannot execute libvirt client: {exc}") from exc
        if result.returncode:
            raise QSXError(f"virsh failed ({result.returncode}): {result.stderr.strip()[:16384]}")
        return result.stdout.strip()

    def is_active(self, name: str) -> bool:
        try:
            return self._virsh("domstate", name).strip().lower() not in {"shut off", "shutoff"}
        except QSXError as exc:
            raise QSXError(
                f"cannot establish domain state; refusing offline operation: {exc}"
            ) from exc

    def define(self, xml: Path) -> None:
        if xml.stem != "domain" or not xml.is_file():
            raise QSXError("domain definition must be an existing controller domain.xml")
        names = self._virsh("list", "--all", "--name").splitlines()
        expected = ElementTree.parse(xml).getroot().findtext("name")
        if expected in names:
            raise QSXError(f"domain already exists: {expected}; refusing to overwrite it")
        self._virsh("define", str(xml))

    def start(self, name: str) -> None:
        if not Path("/dev/kvm").exists():
            raise QSXError("KVM unavailable; production backend does not fall back to TCG")
        root = self._domain(name, inactive=True)
        if root.findall("./devices/interface") or root.findall("./devices/hostdev"):
            raise QSXError(
                "offline startup refuses network interfaces and host devices; "
                "run network cut and review the domain definition"
            )
        self._virsh("start", name)

    def status(self, name: str) -> str:
        return self._virsh("domstate", name).strip()

    def shutdown(self, name: str, timeout: int = 120) -> None:
        self._virsh("shutdown", name)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status(name).lower() in {"shut off", "shutoff"}:
                return
            time.sleep(1)
        raise QSXError("domain did not shut down before timeout; it was not forced off")

    def detach_import_medium(self, name: str, medium: Path) -> None:
        self.detach_medium(name, medium, missing_ok=True)

    def detach_medium(self, name: str, medium: Path, *, missing_ok: bool = False) -> None:
        if self.is_active(name):
            raise QSXError("medium can only be detached while the domain is SHUTOFF")
        root = self._domain(name, inactive=True)
        matches = []
        for disk in root.findall("./devices/disk"):
            source = disk.find("source")
            if source is not None and source.get("file") == str(medium):
                matches.append(disk)
        if not matches and missing_ok:
            return
        if len(matches) != 1 or matches[0].get("device") != "cdrom":
            raise QSXError("requested medium is not one unique CD-ROM in the domain")
        target = matches[0].find("target")
        if target is None or not target.get("dev"):
            raise QSXError("requested medium has no libvirt disk target")
        self._virsh("detach-disk", name, str(target.get("dev")), "--config")

    def attach_readonly_medium(self, name: str, medium: Path, target: str = "sdb") -> None:
        if self.is_active(name):
            raise QSXError("medium can only be attached while the domain is SHUTOFF")
        if not medium.is_absolute() or not medium.is_file() or target not in {"sdb", "sdc"}:
            raise QSXError("context medium must be a local absolute file at target sdb or sdc")
        self._virsh(
            "attach-disk",
            name,
            str(medium),
            target,
            "--type",
            "cdrom",
            "--mode",
            "readonly",
            "--targetbus",
            "sata",
            "--config",
        )

    def console(
        self,
        name: str,
        initial_command: str | None = None,
        cwd: str = "/workspace/project",
    ) -> None:
        """Attach the operator's terminal; Ctrl+] disconnects without stopping the VM."""
        if not self.is_active(name):
            raise QSXError("console requires a running guest")
        if not shutil.which("virsh"):
            raise QSXError("libvirt client is unavailable")
        with console_lock(self.uri, name):
            if initial_command is not None:
                try:
                    argv = shlex.split(initial_command)
                except ValueError as exc:
                    raise QSXError(f"invalid interactive command: {exc}") from exc
                with self._console_stream(name) as terminal:
                    code = interactive_serial(terminal, argv, cwd)
                if code:
                    raise QSXError(f"guest interactive command failed ({code})")
                return
            result = subprocess.run(["virsh", "--connect", self.uri, "console", name], check=False)
            if result.returncode:
                raise QSXError(f"virsh console failed ({result.returncode})")

    def run_guest(
        self, name: str, argv: list[str], cwd: str, timeout: int = 120
    ) -> dict[str, object]:
        if not self.is_active(name):
            raise QSXError("guest execution requires a running guest")
        with console_lock(self.uri, name), self._console_stream(name) as terminal:
            return run_serial(terminal, argv, cwd, timeout)

    @contextmanager
    def _console_stream(self, name: str) -> Iterator[int]:
        """Use libvirt's authorized console stream, never open a QEMU-owned PTY.

        The client has only a local operator-owned PTY. --safe asks libvirt to
        enforce exclusive guest console access, including non-QSX clients.
        """
        if not shutil.which("virsh"):
            raise QSXError("libvirt client is unavailable")
        master, slave = pty.openpty()
        process: subprocess.Popen[bytes] | None = None
        try:
            tty.setraw(slave)
            os.set_blocking(master, False)
            process = subprocess.Popen(
                ["virsh", "--connect", self.uri, "console", name, "--safe"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env={**os.environ, "LC_ALL": "C"},
                start_new_session=True,
            )
            pending = bytearray()
            deadline = time.monotonic() + 15
            while b"Escape character is" not in pending:
                if process.poll() is not None:
                    raise QSXError("libvirt could not acquire the exclusive guest console")
                if time.monotonic() >= deadline:
                    raise QSXError("libvirt console acquisition timed out")
                readable, _, _ = select.select([master], [], [], 0.2)
                if readable:
                    pending.extend(os.read(master, 65536))
                    if len(pending) > 65536:
                        raise QSXError("unexpected console acquisition output")
            yield master
        except OSError as exc:
            raise QSXError(f"local libvirt console transport failed: {exc}") from exc
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.write(master, b"\x1d")
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    # Terminate only the local virsh client; never the guest or
                    # its foreground command, whose state may be unknown.
                    process.terminate()
                    process.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def _domain(self, name: str, *, inactive: bool = False) -> ElementTree.Element:
        if not SAFE_NAME.fullmatch(name):
            raise QSXError("unsafe domain name")
        try:
            args = ("dumpxml", name, "--inactive") if inactive else ("dumpxml", name)
            return ElementTree.fromstring(self._virsh(*args))
        except ElementTree.ParseError as exc:
            raise QSXError("libvirt returned invalid domain XML") from exc

    def network_cut(self, name: str) -> dict[str, object]:
        """Cut live links and remove persistent NICs outside the guest.

        Link down is immediate even for established sessions and needs no guest
        hot-unplug cooperation. Only this domain is changed; no host firewall.
        A failed verification is an error, never a claim that egress was cut.
        """
        active = self.is_active(name)
        disabled = 0
        if active:
            root = self._domain(name)
            if root.findall("./devices/hostdev"):
                raise QSXError(
                    "unmanaged host device may provide network access; cannot certify cut"
                )
            for interface in root.findall("./devices/interface"):
                address = interface.find("mac")
                target = interface.find("target")
                identifier = (address.get("address") if address is not None else None) or (
                    target.get("dev") if target is not None else None
                )
                if not identifier:
                    raise QSXError("cannot identify a network interface for enforced cut")
                # virsh domif-setlink has no --live option: the running domain
                # is the default; --config separately affects next startup.
                self._virsh("domif-setlink", name, identifier, "down")
                disabled += 1
            for interface in self._domain(name).findall("./devices/interface"):
                link = interface.find("link")
                if link is None or link.get("state") != "down":
                    raise QSXError("network cut verification failed for a live interface")
        persistent = self._domain(name, inactive=True)
        if persistent.findall("./devices/hostdev"):
            raise QSXError("unmanaged host device may provide network access after restart")
        interfaces = persistent.findall("./devices/interface")
        for interface in interfaces:
            with tempfile.TemporaryDirectory(prefix="qsx-detach-") as temporary:
                device = Path(temporary) / "interface.xml"
                device.write_text(ElementTree.tostring(interface, encoding="unicode"))
                self._virsh("detach-device", name, str(device), "--config")
        if self._domain(name, inactive=True).findall("./devices/interface"):
            raise QSXError("network cut verification failed for persistent interfaces")
        return {
            "network_profile": "offline",
            "disabled_live_interfaces": disabled,
            "removed_device_definitions": len(interfaces),
            "evidence_status": "CONTROLLER_OBSERVED",
            "active": active,
        }


def domain_xml(
    name: str, disk: Path, import_medium: Path | None = None, memory_mib: int = 4096, vcpus: int = 2
) -> str:
    if not disk.is_absolute() or not SAFE_NAME.fullmatch(name):
        raise QSXError("disk must be absolute and environment name safe")
    if type(memory_mib) is not int or not 256 <= memory_mib <= 1048576:
        raise QSXError("memory_mib is outside supported limits")
    if type(vcpus) is not int or not 1 <= vcpus <= 256:
        raise QSXError("vcpus is outside supported limits")
    root = ElementTree.Element("domain", {"type": "kvm"})
    ElementTree.SubElement(root, "name").text = name
    ElementTree.SubElement(root, "memory", {"unit": "MiB"}).text = str(memory_mib)
    ElementTree.SubElement(root, "vcpu").text = str(vcpus)
    operating_system = ElementTree.SubElement(root, "os")
    ElementTree.SubElement(
        operating_system, "type", {"arch": "x86_64", "machine": "q35"}
    ).text = "hvm"
    features = ElementTree.SubElement(root, "features")
    ElementTree.SubElement(features, "acpi")
    ElementTree.SubElement(features, "apic")
    devices = ElementTree.SubElement(root, "devices")
    drive = ElementTree.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ElementTree.SubElement(drive, "driver", {"name": "qemu", "type": "qcow2"})
    ElementTree.SubElement(drive, "source", {"file": str(disk)})
    ElementTree.SubElement(drive, "target", {"dev": "vda", "bus": "virtio"})
    serial = ElementTree.SubElement(devices, "serial", {"type": "pty"})
    ElementTree.SubElement(serial, "target", {"type": "isa-serial", "port": "0"})
    console = ElementTree.SubElement(devices, "console", {"type": "pty"})
    ElementTree.SubElement(console, "target", {"type": "serial", "port": "0"})
    if import_medium:
        if not import_medium.is_absolute():
            raise QSXError("import medium path must be absolute")
        cdrom = ElementTree.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
        ElementTree.SubElement(cdrom, "driver", {"name": "qemu", "type": "raw"})
        ElementTree.SubElement(cdrom, "source", {"file": str(import_medium)})
        ElementTree.SubElement(cdrom, "target", {"dev": "sda", "bus": "sata"})
        ElementTree.SubElement(cdrom, "readonly")
    return ElementTree.tostring(root, encoding="unicode")
