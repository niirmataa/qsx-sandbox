from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import select
import shlex
import stat
import tempfile
import termios
import time
import tty
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import SAFE_NAME
from .errors import QSXError

MAX_OUTPUT_BYTES = 8 * 1024 * 1024


@contextmanager
def console_lock(uri: str, name: str) -> Iterator[None]:
    """Coordinate QSX console users; never force-take an operator's console."""
    if not SAFE_NAME.fullmatch(name):
        raise QSXError("unsafe domain name")
    root = Path(tempfile.gettempdir()) / f"qsx-console-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise QSXError("console lock directory must be private and owned by this operator")
    key = hashlib.sha256(f"{uri}\0{name}".encode()).hexdigest()
    fd = os.open(root / key, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise QSXError("another QSX console operation is active for this instance") from exc
        yield
    finally:
        os.close(fd)


def guest_command(argv: list[str], cwd: str, nonce: str) -> bytes:
    """Create shell input. Only the fixed nonroot agent account executes argv."""
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise QSXError("guest command requires nonempty argv without NUL")
    if not argv[0] or any("\n" in arg or "\r" in arg for arg in argv):
        raise QSXError(
            "serial commands must use single-line arguments; use a guest file for scripts"
        )
    path = Path(cwd)
    if (
        not path.is_absolute()
        or not path.is_relative_to("/workspace/project")
        or ".." in path.parts
        or "\n" in cwd
        or "\r" in cwd
        or "\x00" in cwd
    ):
        raise QSXError("guest cwd must be below /workspace/project")
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise QSXError("invalid serial framing nonce")
    # Splitting markers avoids recognizing serial command echo as a reply.
    script = (
        f"printf '\\n%s%s\\n' QSX_BEGIN_ {shlex.quote(nonce)}; "
        'if [ "$(id -un)" != agent ] || [ "$(id -u)" = 0 ]; then '
        "printf '%s\\n' 'QSX requires the nonroot agent console'; qsx_rc=126; "
        f"else (cd -- {shlex.quote(cwd)} && command -- {shlex.join(argv)}); qsx_rc=$?; fi; "
        f"printf '\\n%s%s:%s\\n' QSX_END_ {shlex.quote(nonce)} \"$qsx_rc\""
    )
    command = ("sh -c " + shlex.quote(script) + "\n").encode()
    # A Linux canonical tty input line is commonly bounded at 4096 bytes.
    if len(command) > 3500:
        raise QSXError("serial command is too long; transfer a script into the guest workspace")
    return command


def run_serial(
    terminal: str | int, argv: list[str], cwd: str, timeout: int = 120
) -> dict[str, object]:
    """Use the guest's local serial shell. No guest report proves independent validation.

    This is a shell transport, not an administrator endpoint. The caller must
    exclusively own the guest console; interactive applications must be closed.
    """
    if isinstance(terminal, str) and not re.fullmatch(r"/dev/pts/[0-9]+", terminal):
        raise QSXError("libvirt did not return a local PTY console")
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise QSXError("guest timeout must be an integer in 1..86400 seconds")
    nonce = secrets.token_hex(16)
    command = guest_command(argv, cwd, nonce)
    flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = os.dup(terminal) if isinstance(terminal, int) else os.open(terminal, flags)
    except OSError as exc:
        raise QSXError(f"cannot open local guest console: {exc}") from exc
    previous = None
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise QSXError("local console must be a character device")
        previous = termios.tcgetattr(fd)
        tty.setraw(fd)
        # Discard old console output, then send one framed command; never send a
        # disruptive Ctrl-C to take over somebody else's foreground process.
        termios.tcflush(fd, termios.TCIFLUSH)
        remaining = memoryview(command)
        deadline = time.monotonic() + timeout
        while remaining:
            if time.monotonic() >= deadline:
                raise QSXError("guest console timed out while sending the command")
            _, writable, _ = select.select([], [fd], [], 0.2)
            if writable:
                written = os.write(fd, remaining)
                remaining = remaining[written:]
        buffer = bytearray()
        begin = f"QSX_BEGIN_{nonce}\n".encode()
        end = re.compile(rb"\nQSX_END_" + nonce.encode() + rb":([0-9]{1,3})\n")
        started = False
        while time.monotonic() < deadline:
            readable, _, _ = select.select(
                [fd], [], [], max(0.0, min(0.2, deadline - time.monotonic()))
            )
            if not readable:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise QSXError("guest console closed before command completion")
            buffer.extend(chunk.replace(b"\r", b""))
            if len(buffer) > MAX_OUTPUT_BYTES:
                raise QSXError("guest console output exceeds the 8 MiB collection limit")
            if not started:
                offset = buffer.find(begin)
                if offset == -1:
                    continue
                del buffer[: offset + len(begin)]
                started = True
            match = end.search(buffer)
            if match:
                return {
                    "stdout": buffer[: match.start()].decode("utf-8", errors="replace"),
                    "returncode": int(match.group(1)),
                    "transport": "local-libvirt-serial",
                    "identity": "agent (guest-reported, guarded before execution)",
                    "evidence_status": "GUEST_REPORTED",
                }
        raise QSXError(
            "guest console command timed out; execution state is unknown. "
            "Open the console to inspect it before retrying; no process was forcibly interrupted"
        )
    except OSError as exc:
        raise QSXError(f"guest console I/O failed: {exc}") from exc
    finally:
        if previous is not None:
            termios.tcsetattr(fd, termios.TCSANOW, previous)
        os.close(fd)


def interactive_serial(terminal: str | int, argv: list[str], cwd: str) -> int:
    """Launch argv as agent, then relay the operator's actual terminal locally.

    Ctrl+] detaches only the local console. It leaves the guest process running,
    which may subsequently be resumed with ordinary `qsx console`.
    """
    import sys

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise QSXError("interactive guest commands require an operator terminal")
    acknowledgement = run_serial(terminal, ["true"], cwd, timeout=15)
    if acknowledgement["returncode"] != 0:
        raise QSXError("guest console is not an available nonroot agent shell")
    nonce = secrets.token_hex(16)
    command = guest_command(argv, cwd, nonce)
    fd = (
        os.dup(terminal)
        if isinstance(terminal, int)
        else os.open(terminal, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
    )
    local_fd = sys.stdin.fileno()
    previous_guest = termios.tcgetattr(fd)
    previous_local = termios.tcgetattr(local_fd)
    try:
        tty.setraw(fd)
        termios.tcflush(fd, termios.TCIFLUSH)
        sent = 0
        while sent < len(command):
            _, writable, _ = select.select([], [fd], [], 5)
            if not writable:
                raise QSXError("guest console stalled before interactive launch")
            sent += os.write(fd, command[sent:])
        # Wait for the non-echo marker before enabling operator input. Never
        # send a break/control sequence to take over an existing guest program.
        begin = f"QSX_BEGIN_{nonce}\n".encode()
        end = re.compile(rb"\nQSX_END_" + nonce.encode() + rb":([0-9]{1,3})\n")
        pending = bytearray()
        deadline = time.monotonic() + 15
        while begin not in pending:
            if time.monotonic() >= deadline:
                raise QSXError("guest shell did not acknowledge interactive launch")
            readable, _, _ = select.select([fd], [], [], 0.2)
            if readable:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise QSXError("guest console closed during interactive launch")
                pending.extend(chunk.replace(b"\r", b""))
                if len(pending) > MAX_OUTPUT_BYTES:
                    raise QSXError("guest console produced excessive data before launch")
        offset = pending.index(begin) + len(begin)
        initial_output = bytes(pending[offset:])
        if initial_output:
            os.write(sys.stdout.fileno(), initial_output)
        tty.setraw(local_fd)
        tail = bytearray(initial_output[-256:])
        while True:
            match = end.search(tail)
            if match:
                return int(match.group(1))
            readable, _, _ = select.select([fd, local_fd], [], [], 1)
            if fd in readable:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise QSXError("guest console closed during the interactive command")
                os.write(sys.stdout.fileno(), chunk)
                tail.extend(chunk.replace(b"\r", b""))
                if len(tail) > 512:
                    del tail[:-512]
            if local_fd in readable:
                data = os.read(local_fd, 65536)
                if not data or b"\x1d" in data:
                    return 0
                remaining = memoryview(data)
                while remaining:
                    _, writable, _ = select.select([], [fd], [], 1)
                    if writable:
                        count = os.write(fd, remaining)
                        remaining = remaining[count:]
    except OSError as exc:
        raise QSXError(f"interactive guest console I/O failed: {exc}") from exc
    finally:
        termios.tcsetattr(local_fd, termios.TCSANOW, previous_local)
        termios.tcsetattr(fd, termios.TCSANOW, previous_guest)
        os.close(fd)
