from __future__ import annotations

import importlib.util
import json
import subprocess
import types
from pathlib import Path

import pytest

from qsx_alpine.templates import load_recipe

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "templates/sage-lean4-container.toml"


def guest_source(basename: str) -> str:
    recipe = load_recipe(RECIPE)
    return next(item["content"] for item in recipe["files"]
                if Path(item["path"]).name == basename)


def service(tmp_path: Path) -> types.ModuleType:
    path = tmp_path / "guest_service.py"
    path.write_text(guest_source("qsx-science-service.py"))
    spec = importlib.util.spec_from_file_location("qsx_science_service_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    module.WORKSPACE = workspace
    return module


def test_guest_tool_commands_preserve_boundary(tmp_path: Path) -> None:
    module = service(tmp_path)
    request = {"tool": "sage", "argv": ["-c", "print('literal; $(no shell)')"],
               "cwd": str(module.WORKSPACE)}
    argv = module.command(request, 1000, 1000)
    assert argv[-2:] == request["argv"]
    assert argv[argv.index(module.IMAGE_ID) + 1:] == request["argv"]
    for required in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges:true", "--user=1000:1000"):
        assert required in argv
    assert not any("docker.sock" in item for item in argv)
    mounts = [item for item in argv if item.startswith("--mount=")]
    assert mounts == [
        "--mount=type=bind,source=/workspace/project,target=/workspace/project",
        "--mount=type=bind,source=/opt/lean-4.33.1,target=/opt/lean-4.33.1,readonly",
    ]


@pytest.mark.parametrize("change", [
    {"tool": "sh"}, {"argv": "--privileged"}, {"argv": ["nul\0argument"]},
    {"cwd": "/"}, {"cwd": "relative"}, {"docker_args": ["--privileged"]},
])
def test_guest_service_rejects_boundary_expansion(tmp_path: Path, change: dict) -> None:
    module = service(tmp_path)
    request = {"tool": "lean", "argv": [], "cwd": str(module.WORKSPACE), **change}
    with pytest.raises(ValueError):
        module.command(request, 1000, 1000)


def test_guest_service_rejects_symlink_escape_and_root(tmp_path: Path) -> None:
    module = service(tmp_path)
    escaped = module.WORKSPACE / "escaped"
    escaped.symlink_to(tmp_path)
    request = {"tool": "lake", "argv": [], "cwd": str(escaped)}
    with pytest.raises(ValueError, match="guest workspace"):
        module.command(request, 1000, 1000)
    request["cwd"] = str(module.WORKSPACE)
    with pytest.raises(ValueError, match="root tool identity"):
        module.command(request, 0, 0)


def test_all_guest_scripts_parse_without_execution(tmp_path: Path) -> None:
    for item in load_recipe(RECIPE)["files"]:
        content = item["content"]
        if content.startswith("#!/usr/bin/env python3"):
            compile(content, item["path"], "exec")
        elif content.startswith(("#!/bin/sh", "#!/bin/bash", "#!/sbin/openrc-run")):
            path = tmp_path / Path(item["path"]).name
            path.write_text(content)
            shell = "bash" if content.startswith("#!/bin/bash") else "sh"
            subprocess.run([shell, "-n", str(path)], check=True)


def test_sage_archive_preserves_pinned_descriptor_graph() -> None:
    spec = importlib.util.spec_from_file_location("freeze_sage", REPO / "scripts/freeze_sage_container.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config_digest = "a" * 64
    layer_digest = "b" * 64
    manifest = {"config": {"digest": "sha256:" + config_digest},
                "layers": [{"digest": "sha256:" + layer_digest}]}
    metadata = module.archive_metadata(manifest, 1234)
    index = json.loads(metadata["index.json"])
    docker = json.loads(metadata["manifest.json"])[0]
    assert index["manifests"][0]["digest"] == "sha256:" + module.IMAGE_DIGEST
    assert index["manifests"][0]["size"] == 1234
    assert docker["Config"] == "blobs/sha256/" + config_digest
    assert docker["Layers"] == ["blobs/sha256/" + layer_digest]
    assert json.loads(metadata["oci-layout"]) == {"imageLayoutVersion": "1.0.0"}


def freeze_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("freeze_sage_test", REPO / "scripts/freeze_sage_container.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_refreshes_public_token_for_each_request_and_after_401(monkeypatch) -> None:
    import io
    import urllib.error

    module = freeze_module()
    seen_tokens = []
    tokens_issued = 0

    class Opener:
        def open(self, request, timeout):
            nonlocal tokens_issued
            if request == module.TOKEN_URL:
                tokens_issued += 1
                return io.BytesIO(json.dumps({"token": str(tokens_issued)}).encode())
            seen_tokens.append(request.get_header("Authorization"))
            if len(seen_tokens) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, None)
            return io.BytesIO(b"verified input bytes")

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    with module.registry_open(module.REGISTRY + "blobs/sha256:" + "a" * 64) as response:
        assert response.read() == b"verified input bytes"
    with module.registry_open(module.REGISTRY + "blobs/sha256:" + "b" * 64) as response:
        assert response.read() == b"verified input bytes"
    assert seen_tokens == ["Bearer 1", "Bearer 2", "Bearer 3"]


def test_large_input_resumes_verified_chunks_and_preserves_failed_bytes(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import io

    module = freeze_module()
    payload = b"01234567abcdefghABCDEFGH"
    expected = hashlib.sha256(payload).hexdigest()
    target = tmp_path / (expected + ".artifact")
    transfers = tmp_path / "transfers"
    requests = []
    fail_middle = True

    class Response(io.BytesIO):
        def __init__(self, start, end, fail):
            super().__init__(payload[start:end + 1])
            self.status = 206
            self.headers = {"Content-Range": f"bytes {start}-{end}/{len(payload)}"}
            self.fail = fail

        def read(self, size=-1):
            if self.fail and self.tell():
                raise OSError("synthetic disconnected download")
            return super().read(3 if self.fail else size)

    def opened(url, byte_range):
        start, end = map(int, byte_range.removeprefix("bytes=").split("-"))
        requests.append((start, end))
        return Response(start, end, fail_middle and start == 8)

    monkeypatch.setattr(module, "registry_open", opened)
    with pytest.raises(OSError, match="disconnected"):
        module.download_ranges("https://registry.example/blob", target, expected, len(payload),
                               transfers, connections=1, chunk_size=8)
    assert not target.exists()
    assert next((transfers / expected).glob("*.download-*")).read_bytes() == b"abc"
    requests.clear()
    fail_middle = False
    report = module.download_ranges("https://registry.example/blob", target, expected, len(payload),
                                    transfers, connections=1, chunk_size=8)
    assert target.read_bytes() == payload
    assert requests == [(8, 15)]
    assert report["downloaded_bytes"] == 8
    assert report["reused_bytes"] == 16
    assert next((transfers / expected).glob("*.download-*")).read_bytes() == b"abc"


def test_download_rejects_wrong_whole_digest_and_retains_assembly(tmp_path: Path, monkeypatch) -> None:
    import io

    module = freeze_module()
    expected = "a" * 64
    target = tmp_path / "output.artifact"

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(b"test")
            self.status = 206
            self.headers = {"Content-Range": "bytes 0-3/4"}

    monkeypatch.setattr(module, "registry_open", lambda *args: Response())
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.download_ranges("https://registry.example/blob", target, expected, 4,
                               tmp_path / "transfers", connections=1, chunk_size=8)
    assert not target.exists()
    assert next((tmp_path / "transfers" / expected).glob("assembled-*.artifact")).read_bytes() == b"test"
