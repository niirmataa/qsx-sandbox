from __future__ import annotations

import shutil
from pathlib import Path

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
        self._virsh("define", str(xml))

    def start(self, name: str) -> None:
        if not Path("/dev/kvm").exists():
            raise QSXError("KVM unavailable; production backend does not fall back to TCG")
        self._virsh("start", name)


def domain_xml(name: str, disk: Path, memory_mib: int = 4096, vcpus: int = 2) -> str:
    if not disk.is_absolute():
        raise QSXError("disk path must be absolute")
    # No host filesystem, socket, USB, or network interface is attached here.
    return f"""<domain type='kvm'>
  <name>{name}</name><memory unit='MiB'>{memory_mib}</memory><vcpu>{vcpus}</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <features><acpi/><apic/></features>
  <devices><disk type='file' device='disk'><driver name='qemu' type='qcow2'/>
    <source file='{disk}'/><target dev='vda' bus='virtio'/></disk>
    <console type='pty'/></devices>
</domain>"""
