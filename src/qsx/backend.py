from __future__ import annotations

import shutil
import time
from pathlib import Path
from xml.etree import ElementTree

from .errors import QSXError
from .util import run


class LibvirtBackend:
    """Production adapter: virsh calls are explicit and never fall back to a fake driver."""

    def __init__(self, uri: str = "qemu:///system") -> None:
        self.uri = uri

    def _virsh(self, *args: str) -> str:
        if not shutil.which("virsh"):
            raise QSXError("libvirt client is unavailable")
        return run(["virsh", "--connect", self.uri, *args])

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
        if self.is_active(name):
            raise QSXError("import medium can only be detached while the domain is SHUTOFF")
        self._virsh("detach-disk", name, str(medium), "--config")


def domain_xml(
    name: str, disk: Path, import_medium: Path | None = None, memory_mib: int = 4096, vcpus: int = 2
) -> str:
    if not disk.is_absolute():
        raise QSXError("disk path must be absolute")
    # No host filesystem, socket, USB, or network interface is attached here.
    xml = f"""<domain type='kvm'>
  <name>{name}</name><memory unit='MiB'>{memory_mib}</memory><vcpu>{vcpus}</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <features><acpi/><apic/></features>
  <devices><disk type='file' device='disk'><driver name='qemu' type='qcow2'/>
    <source file='{disk}'/><target dev='vda' bus='virtio'/></disk>
    <console type='pty'/></devices>
</domain>"""
    root = ElementTree.fromstring(xml)
    if import_medium:
        if not import_medium.is_absolute():
            raise QSXError("import medium path must be absolute")
        devices = root.find("devices")
        assert devices is not None
        cdrom = ElementTree.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
        ElementTree.SubElement(cdrom, "driver", {"name": "qemu", "type": "raw"})
        ElementTree.SubElement(cdrom, "source", {"file": str(import_medium)})
        ElementTree.SubElement(cdrom, "target", {"dev": "sda", "bus": "sata"})
        ElementTree.SubElement(cdrom, "readonly")
    return ElementTree.tostring(root, encoding="unicode")
