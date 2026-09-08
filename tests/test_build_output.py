from __future__ import annotations

import io
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from qsx_alpine.build_output import log_contains, run_build
from qsx_alpine.errors import QSXError


@pytest.fixture
def children(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[subprocess.Popen[bytes]]]:
    launched: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen

    def launch(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original(*args, **kwargs)
        launched.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    try:
        yield launched
    finally:
        # A failing regression must itself leave no synthetic sleeper behind.
        for child in launched:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def assert_reaped(child: subprocess.Popen[bytes]) -> None:
    assert child.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


@pytest.mark.parametrize("exit_code", [0, 23])
def test_collects_exact_stdout_stderr_and_exit_code(
    tmp_path: Path, children: list[subprocess.Popen[bytes]], exit_code: int
) -> None:
    log = tmp_path / "build.log"
    command = [
        sys.executable,
        "-c",
        (
            "import os, sys; assert sys.stdin.buffer.read() == b''; "
            "os.write(1, b'out\\xff\\n'); os.write(2, b'err\\x80\\n'); "
            f"sys.exit({exit_code})"
        ),
    ]
    assert run_build(command, log, timeout=5, max_log_bytes=10) == exit_code
    assert log.read_bytes() == b"out\xff\nerr\x80\n"
    assert_reaped(children[0])


def test_small_output_is_visible_before_child_is_released(
    tmp_path: Path, children: list[subprocess.Popen[bytes]]
) -> None:
    log = tmp_path / "live.log"
    emitted = tmp_path / "emitted"
    release = tmp_path / "release"
    seen_live = threading.Event()
    collection_done = threading.Event()

    def read_live_log() -> None:
        try:
            deadline = time.monotonic() + 5
            while not emitted.exists():
                if time.monotonic() >= deadline or collection_done.wait(0.01):
                    return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not collection_done.is_set():
                if log.read_bytes() == b"small live marker\n" and children[0].poll() is None:
                    seen_live.set()
                    return
                collection_done.wait(0.01)
        finally:
            release.touch()

    reader = threading.Thread(target=read_live_log)
    reader.start()
    try:
        result = run_build(
            [
                sys.executable,
                "-c",
                (
                    "import os, sys, time\n"
                    "from pathlib import Path\n"
                    "os.write(1, b'small live marker\\n')\n"
                    "Path(sys.argv[1]).touch()\n"
                    "while not Path(sys.argv[2]).exists():\n"
                    "    time.sleep(0.01)\n"
                ),
                str(emitted),
                str(release),
            ],
            log,
            timeout=15,
            max_log_bytes=1024,
        )
    finally:
        collection_done.set()
        release.touch()
        reader.join(timeout=2)
    assert not reader.is_alive()
    assert result == 0
    assert log.read_bytes() == b"small live marker\n"
    assert_reaped(children[0])
    assert seen_live.is_set(), "small output was not visible until the child was released"


def test_output_overflow_retains_bounded_prefix_and_reaps_child(
    tmp_path: Path, children: list[subprocess.Popen[bytes]]
) -> None:
    log = tmp_path / "overflow.log"
    started = time.monotonic()
    with pytest.raises(QSXError, match="exceeded log limit") as failure:
        run_build(
            [
                sys.executable,
                "-c",
                "import os, time; os.write(1, b'x' * 16384); time.sleep(30)",
            ],
            log,
            timeout=5,
            max_log_bytes=1024,
        )
    assert str(log) in str(failure.value)
    assert log.read_bytes() == b"x" * 1024
    assert time.monotonic() - started < 4
    assert children[0].returncode == -signal.SIGKILL
    assert_reaped(children[0])


@pytest.mark.parametrize("close_output", [False, True])
def test_deadline_reaps_even_if_child_closes_output_or_ignores_sigterm(
    tmp_path: Path, children: list[subprocess.Popen[bytes]], close_output: bool
) -> None:
    log = tmp_path / "timeout.log"
    code = (
        "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "os.write(1, b'waiting\\n'); "
    )
    if close_output:
        code += "os.close(1); os.close(2); "
    code += "time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(QSXError, match="timed out") as failure:
        run_build([sys.executable, "-c", code], log, timeout=1, max_log_bytes=1024)
    assert str(log) in str(failure.value)
    assert log.read_bytes() == b"waiting\n"
    assert time.monotonic() - started < 4
    assert children[0].returncode == -signal.SIGKILL
    assert_reaped(children[0])


@pytest.mark.parametrize("error", [KeyboardInterrupt(), OSError("synthetic read failure")])
def test_collection_error_or_interrupt_stops_and_reaps_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    children: list[subprocess.Popen[bytes]],
    error: BaseException,
) -> None:
    def fail_select(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(selectors.DefaultSelector, "select", fail_select)
    log = tmp_path / "interrupted.log"
    expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else QSXError
    with pytest.raises(expected) as failure:
        run_build([sys.executable, "-c", "import time; time.sleep(30)"], log, timeout=5)
    assert log.is_file()
    if isinstance(error, OSError):
        assert str(log) in str(failure.value)
    assert children[0].returncode == -signal.SIGKILL
    assert_reaped(children[0])


@pytest.mark.parametrize("field", ["timeout", "max_log_bytes"])
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "10", None])
def test_invalid_limits_create_neither_log_nor_process(
    tmp_path: Path, children: list[subprocess.Popen[bytes]], field: str, invalid: Any
) -> None:
    log = tmp_path / "invalid.log"
    options = {"timeout": 5, "max_log_bytes": 1024, field: invalid}
    with pytest.raises(QSXError, match="positive integer"):
        run_build([sys.executable, "-c", "raise AssertionError('must not run')"], log, **options)
    assert not log.exists()
    assert not children


def test_existing_log_is_never_overwritten(
    tmp_path: Path, children: list[subprocess.Popen[bytes]]
) -> None:
    log = tmp_path / "existing.log"
    log.write_bytes(b"earlier evidence")
    with pytest.raises(QSXError) as failure:
        run_build([sys.executable, "-c", "pass"], log, timeout=5)
    assert str(log) in str(failure.value)
    assert log.read_bytes() == b"earlier evidence"
    assert not children


def test_start_error_names_preserved_evidence(tmp_path: Path) -> None:
    log = tmp_path / "start-error.log"
    with pytest.raises(QSXError) as failure:
        run_build([str(tmp_path / "nonexistent-executable")], log, timeout=5)
    assert str(log) in str(failure.value)
    assert log.read_bytes() == b""


def test_marker_crosses_chunk_and_utf8_boundaries_in_invalid_utf8_log(tmp_path: Path) -> None:
    log = tmp_path / "marker.log"
    marker = "λREADY✓"
    content = b"\xff" * 65535 + marker.encode("utf-8") + b"\x80trailer"
    log.write_bytes(content)
    assert log_contains(log, marker)
    assert not log_contains(log, "absent")
    assert not log_contains(log, "\ufffd")
    assert log.read_bytes() == content


def test_marker_scans_with_only_bounded_binary_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_sizes: list[int] = []

    class BoundedReader(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 65536
            read_sizes.append(size)
            return super().read(size)

    def open_stream(path: Path, mode: str) -> BoundedReader:
        assert mode == "rb"
        return BoundedReader(b"x" * (3 * 65536) + b"READY")

    def reject_full_read(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("whole-file reads are forbidden")

    monkeypatch.setattr(Path, "open", open_stream)
    monkeypatch.setattr(Path, "read_text", reject_full_read)
    monkeypatch.setattr(Path, "read_bytes", reject_full_read)
    assert log_contains(tmp_path / "streamed.log", "READY")
    assert len(read_sizes) == 4


@pytest.mark.parametrize("marker", ["", None, 7, b"READY", "\ud800", "x" * 4097, "λ" * 2049])
def test_invalid_marker_fails_closed_before_opening_log(tmp_path: Path, marker: Any) -> None:
    assert not log_contains(tmp_path / "does-not-exist", marker)
