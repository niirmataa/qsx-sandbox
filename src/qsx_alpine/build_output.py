from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from .errors import QSXError

DEFAULT_LOG_LIMIT_BYTES = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_MAX_MARKER_BYTES = 4096


def _stop_and_reap(process: subprocess.Popen[bytes]) -> None:
    # Only this dedicated session is signalled, never the caller's process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def run_build(
    argv: list[str],
    log: Path,
    timeout: int,
    max_log_bytes: int = DEFAULT_LOG_LIMIT_BYTES,
) -> int:
    """Collect a build child's combined output within a byte budget and deadline.

    A failed collection keeps the new log and stops and reaps the child. The log
    contains the exact output prefix; exceeding the budget is always an error.
    """
    if type(timeout) is not int or timeout <= 0:
        raise QSXError("build timeout must be a positive integer")
    if type(max_log_bytes) is not int or max_log_bytes <= 0:
        raise QSXError("build log limit must be a positive integer")

    deadline = time.monotonic() + timeout
    process: subprocess.Popen[bytes] | None = None
    try:
        with log.open("xb") as stream, selectors.DefaultSelector() as selector:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
            )
            assert process.stdout is not None
            with process.stdout:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                written = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise QSXError(f"build guest timed out; evidence retained at {log}")
                    if not selector.select(min(remaining, 1.0)):
                        continue
                    try:
                        chunk = os.read(
                            process.stdout.fileno(),
                            min(_READ_CHUNK_BYTES, max_log_bytes - written + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    available = max_log_bytes - written
                    stream.write(chunk[:available])
                    # Publish even small chunks while the guest is still running.
                    stream.flush()
                    written += min(len(chunk), available)
                    if len(chunk) > available:
                        raise QSXError(
                            f"build guest exceeded log limit ({max_log_bytes} bytes); "
                            f"evidence retained at {log}"
                        )
                try:
                    return process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise QSXError(
                        f"build guest timed out; evidence retained at {log}"
                    ) from exc
    except OSError as exc:
        raise QSXError(f"cannot collect build guest output: {exc}; evidence path: {log}") from exc
    finally:
        if process is not None and process.returncode is None:
            _stop_and_reap(process)


def log_contains(path: Path, marker: str) -> bool:
    """Find a small UTF-8 marker without decoding or loading the whole log."""
    if not isinstance(marker, str) or not marker or len(marker) > _MAX_MARKER_BYTES:
        return False
    try:
        needle = marker.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(needle) > _MAX_MARKER_BYTES:
        return False
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            window = overlap + chunk
            if needle in window:
                return True
            overlap = window[-(len(needle) - 1) :] if len(needle) > 1 else b""
    return False
