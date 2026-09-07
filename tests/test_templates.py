from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from qsx_alpine.errors import QSXError
from qsx_alpine.templates import (
    BASE,
    CODEX,
    _build_workspace,
    _digest,
    _freeze_artifacts,
    _guest_script,
    _implementation,
    _inside,
    _make_seed,
    _verify_lock,
    build_image,
    create_recipe,
    list_templates,
    load_agent_profile,
    load_recipe,
    verify_image,
)


def recipe(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "test.toml"
    (tmp_path / "agent.toml").write_text(
        'schema_version = 1\nname = "test-agent"\npackages = []\nargv = ["codex"]\n[codex]\nenabled = true\n'
    )
    path.write_text(
        'schema_version = 1\nname = "test"\nagent_profile = "agent.toml"\npackages = []\n' + extra
    )
    return path


def test_recipe_inheritance_append_and_override(tmp_path: Path) -> None:
    parent = recipe(tmp_path, 'readiness = ["git --version"]\n[resources]\nvcpus = 1\n')
    child = tmp_path / "child.toml"
    child.write_text(
        'schema_version = 1\nname = "child"\nextends = "test.toml"\npackages = ["python3"]\nreadiness = ["python3 --version"]\n[resources]\nmemory_mib = 4096\n'
    )
    result = load_recipe(child)
    assert result["name"] == "child"
    assert result["packages"] == sorted(set(load_recipe(parent)["packages"] + ["python3"]))
    assert result["resources"] == {"vcpus": 1, "memory_mib": 4096, "disk_gib": 8}
    assert result["readiness"] == ["git --version", "python3 --version"]
    assert result["base"] == BASE
    assert result["codex"] == CODEX


def test_equivalent_recipe_digest_is_location_independent(tmp_path: Path) -> None:
    first = recipe(tmp_path)
    second = tmp_path / "second.toml"
    second.write_bytes(first.read_bytes())
    assert load_recipe(first)["recipe_sha256"] == load_recipe(second)["recipe_sha256"]
    second.write_text(second.read_text() + 'prepare = ["touch /opt/configured"]\n')
    assert load_recipe(first)["recipe_sha256"] != load_recipe(second)["recipe_sha256"]


@pytest.mark.parametrize(
    "extra",
    [
        'network = "unrestricted"\n',
        'host_command = "touch /tmp/wrong"\n',
        "[resources]\nvcpus = true\n",
        "[resources]\ndisk_gib = 0\n",
        "[resources]\nmemory_mib = 128\n",
        '[resources]\nhost_mount = "/"\n',
        '[base]\nurl = "http://example.com/3.24.1/image"\n',
        '[base]\nversion = "3.23.0"\n',
        '[codex]\nsha256 = "bad"\n',
        '[codex]\nenabled = "yes"\n',
        'extends = "/tmp/nope"\n',
        'prepare = "echo no"\n',
        '[[files]]\npath = "/home/agent/.codex/auth.json"\ncontent = "secret"\n',
        '[[files]]\npath = "/etc/../etc/bad"\ncontent = "bad"\n',
        '[[files]]\npath = "/opt/task"\ncontent = "test"\nmode = "4755"\n',
    ],
)
def test_recipe_rejects_invalid_fields(tmp_path: Path, extra: str) -> None:
    with pytest.raises(QSXError):
        load_recipe(recipe(tmp_path, extra))


def test_cycle_and_malformed_toml(tmp_path: Path) -> None:
    path = recipe(tmp_path, 'extends = "test.toml"\n')
    with pytest.raises(QSXError, match="cycle"):
        load_recipe(path)
    path.write_text("[bad")
    with pytest.raises(QSXError, match="invalid recipe"):
        load_recipe(path)


def test_package_injection_refused(tmp_path: Path) -> None:
    path = recipe(tmp_path)
    path.write_text(
        'schema_version = 1\nname = "test"\nagent_profile = "agent.toml"\npackages = ["bash; touch /tmp/wrong"]\n'
    )
    with pytest.raises(QSXError, match="packages"):
        load_recipe(path)


def test_file_override_reuses_common_recipe(tmp_path: Path) -> None:
    recipe(tmp_path, '[[files]]\npath = "/etc/example.conf"\ncontent = "old"\n')
    child = tmp_path / "child.toml"
    child.write_text(
        'schema_version = 1\nname = "child"\nextends = "test.toml"\n[[files]]\npath = "/etc/example.conf"\ncontent = "new"\n'
    )
    assert load_recipe(child)["files"] == [
        {"path": "/etc/example.conf", "content": "new", "mode": "0644"}
    ]


def test_new_template_needs_no_controller_change(tmp_path: Path) -> None:
    path = tmp_path / "my-new-task.toml"
    result = create_recipe(path, "my-new-task", ["python3", "py3-pytest"])
    assert "python3" in result["packages"]
    assert list_templates(tmp_path)[0]["name"] == "my-new-task"
    with pytest.raises(QSXError, match="overwrite"):
        create_recipe(path, "my-new-task", ["gcc"])


def test_guest_steps_are_data_not_executed_on_controller(tmp_path: Path) -> None:
    sentinel = tmp_path / "host-was-touched"
    path = recipe(
        tmp_path, f'prepare = [{json.dumps("touch " + str(sentinel))}]\nreadiness = ["id -u"]\n'
    )
    value = load_recipe(path)
    script = _guest_script(value, "QSX_TEST_MARKER")
    assert not sentinel.exists()
    assert "touch " + str(sentinel) in script
    assert "apk add --no-network" in script
    assert "--allow-untrusted" not in script
    assert "--dangerously-bypass" not in script
    assert "su -s /bin/sh agent -c /tmp/qsx-readiness.sh" in script
    assert 'sandbox_mode = "workspace-write"' in script
    assert script.index("su -s /bin/sh agent") < script.index("echo 'QSX_TEST_MARKER'")
    assert "rm -rf /var/lib/cloud/*" in script


def test_codex_can_be_omitted_for_operator_recipe(tmp_path: Path) -> None:
    path = recipe(tmp_path)
    profile = tmp_path / "agent.toml"
    profile.write_text(profile.read_text().replace("enabled = true", "enabled = false"))
    value = load_recipe(path)
    assert "codex.tar.gz" not in _guest_script(value, "MARKER")


def test_artifact_path_escape_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-artifact"
    outside.write_text("not an input")
    link = tmp_path / "symlink"
    link.symlink_to(outside)
    for name in ("../outside-artifact", str(outside), "symlink"):
        with pytest.raises(QSXError):
            _inside(tmp_path, name)
    outside.unlink()


def test_corrupt_input_lock_fails_before_execution(tmp_path: Path) -> None:
    with pytest.raises(QSXError, match="lock integrity"):
        _verify_lock({"schema_version": 1, "artifacts": [], "lock_sha256": "wrong"}, tmp_path)
    path = tmp_path / "lock.json"
    path.write_text("{}")
    with pytest.raises(QSXError, match="lock integrity"):
        build_image(path, tmp_path, tmp_path / "out.qcow2", accelerator="tcg")
    assert not (tmp_path / "out.qcow2").exists()


def test_no_implicit_accelerator_fallback(tmp_path: Path) -> None:
    with pytest.raises(QSXError, match="accelerator"):
        build_image(tmp_path / "lock", tmp_path, tmp_path / "out", accelerator="auto")


def test_manifest_corruption_is_not_guest_success(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "manifest_sha256": "wrong"}))
    with pytest.raises(QSXError, match="manifest integrity"):
        verify_image(path)
    manifest = {
        "schema_version": 1,
        "image": "missing.qcow2",
        "image_sha256": "0" * 64,
        "size_bytes": 1,
    }
    manifest["manifest_sha256"] = _digest(manifest)
    path.write_text(json.dumps(manifest))
    with pytest.raises(QSXError, match="schema is incomplete"):
        verify_image(path)


def test_shipped_recipes_are_open_examples() -> None:
    samples = list_templates(Path(__file__).resolve().parents[1] / "templates")
    names = {item["name"] for item in samples}
    assert {"starter", "python-tests", "c-build"} <= names


def test_guest_script_has_valid_shell_syntax(tmp_path: Path) -> None:
    path = tmp_path / "build.sh"
    path.write_text(_guest_script(load_recipe(recipe(tmp_path)), "TEST_SYNTAX_ONLY"))
    result = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("xorriso") is None, reason="real ISO tool unavailable")
def test_real_seed_iso_preserves_long_names_and_guest_script(tmp_path: Path) -> None:
    cache, work = tmp_path / "cache", tmp_path / "work"
    (cache / "apk/packages").mkdir(parents=True)
    work.mkdir()
    package = "a-very-long-synthetic-package-name-1.0.0-r0.apk"
    (cache / "apk/packages" / package).write_bytes(b"synthetic fixture; never installed")
    resolved = load_recipe(recipe(tmp_path))
    resolved["codex"]["enabled"] = False
    lock = {
        "apk_dir": "apk",
        "packages": [{"path": "packages/" + package}],
        "codex_path": None,
        "recipe": resolved,
    }
    medium = _make_seed(lock, cache, work, "SYNTHETIC_SEED_NOT_BOOTED")
    assert medium.is_file()
    extracted = tmp_path / "extracted.sh"
    result = subprocess.run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(medium),
            "-extract",
            "/build.sh",
            str(extracted),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert extracted.read_text() == (work / "seed/build.sh").read_text()
    result = subprocess.run(
        ["xorriso", "-indev", str(medium), "-ls", "/packages/" + package],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "extra",
    [
        "[resources]\nvcpus = 100\n",
        'host_command = "touch /tmp/wrong"\n',
        'network = "unrestricted"\n',
        '[policy]\nsandbox = "disabled"\n',
    ],
)
def test_agent_profile_cannot_grant_policy(tmp_path: Path, extra: str) -> None:
    path = tmp_path / "profile.toml"
    path.write_text('schema_version = 1\nname = "agent"\nargv = ["sh"]\n' + extra)
    with pytest.raises(QSXError, match="profile field"):
        load_agent_profile(path)


def test_profile_changes_are_bound_into_recipe_hash(tmp_path: Path) -> None:
    path = recipe(tmp_path)
    first = load_recipe(path)
    profile = tmp_path / "agent.toml"
    profile.write_text(
        profile.read_text().replace('argv = ["codex"]', 'argv = ["codex", "--help"]')
    )
    second = load_recipe(path)
    assert first["agent_profile_sha256"] != second["agent_profile_sha256"]
    assert first["recipe_sha256"] != second["recipe_sha256"]
    assert second["agent_argv"] == ["codex", "--help"]


def test_profile_is_required_not_implicit_codex(tmp_path: Path) -> None:
    path = tmp_path / "missing-profile.toml"
    path.write_text('schema_version = 1\nname = "task"\n')
    with pytest.raises(QSXError, match="explicitly select"):
        load_recipe(path)


def test_profile_inherited_from_another_directory(tmp_path: Path) -> None:
    parent = tmp_path / "common"
    parent.mkdir()
    recipe(parent)
    child = tmp_path / "child.toml"
    child.write_text('schema_version = 1\nname = "child"\nextends = "common/test.toml"\n')
    assert load_recipe(child)["agent_argv"] == ["codex"]


def test_new_profile_and_task_without_controller_change(tmp_path: Path) -> None:
    profile = tmp_path / "my-python.toml"
    profile.write_text(
        'schema_version = 1\nname = "my-python"\npackages = ["python3"]\nargv = ["python3", "-q"]\nreadiness = ["python3 --version"]\n'
    )
    result = create_recipe(tmp_path / "new-task.toml", "new-task", ["py3-pytest"], profile)
    assert not result["codex"]["enabled"]
    assert {"python3", "py3-pytest"} <= set(result["packages"])
    assert result["agent_argv"] == ["python3", "-q"]
    assert "QSXCTX" not in _guest_script(result, "NO_HISTORY_DEPENDENCY")
    assert "qsx-context" not in _guest_script(result, "NO_HISTORY_DEPENDENCY")


def test_profile_preparation_only_becomes_guest_data(tmp_path: Path) -> None:
    path = recipe(tmp_path)
    marker = tmp_path / "must-not-exist"
    profile = tmp_path / "agent.toml"
    profile.write_text(
        'schema_version = 1\nname = "custom"\nargv = ["sh"]\nprepare = ['
        + json.dumps("touch " + str(marker))
        + "]\n"
    )
    script = _guest_script(load_recipe(path), "ONLY_SYNTHETIC_SCRIPT")
    assert not marker.exists()
    assert "touch " + str(marker) in script


def test_profile_bypass_and_nul_argv_refused(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    for argv in ('["codex", "--yolo"]', "[]"):
        path.write_text('schema_version = 1\nname = "agent"\nargv = ' + argv + "\n")
        with pytest.raises(QSXError):
            load_agent_profile(path)


@pytest.mark.parametrize("explicit_policy", [False, True])
def test_builder_rejects_resource_request_before_tools_or_cache(
    tmp_path: Path, explicit_policy: bool
) -> None:
    resolved = load_recipe(recipe(tmp_path, "[resources]\nmemory_mib = 8192\n"))
    lock = {
        "schema_version": 1,
        "recipe": resolved,
        "implementation_sha256": _implementation(),
        "artifacts": [],
        "base_path": "missing.qcow2",
        "codex_path": None,
        "apk_dir": "missing-apk",
        "packages": [],
        "provenance": {},
    }
    lock["lock_sha256"] = _digest(lock)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    policy = tmp_path / "operator.toml" if explicit_policy else None
    if policy is not None:
        policy.write_text("schema_version = 1\n[limits]\nmemory_mib = 1024\n")
    with pytest.raises(QSXError, match="exceeds operator policy"):
        build_image(path, tmp_path, tmp_path / "out.qcow2", accelerator="tcg", policy=policy)
    assert not (tmp_path / "images").exists()
    assert not (tmp_path / "out.qcow2").exists()


def test_codex_profile_requires_actual_guest_sandbox_smoke() -> None:
    profile = load_agent_profile(Path(__file__).resolve().parents[1] / "agent-profiles/codex.toml")
    checks = "\n".join(profile["readiness"])
    assert "sandbox -- /bin/sh" in checks
    assert "sandbox linux" not in checks
    assert "workspace-write" in checks
    assert "/home/agent/.qsx-outside-probe" in checks
    assert "exit 23" in checks


def artifact_toml(
    name: str = "tool.tar.gz",
    digest: str = "a" * 64,
    url: str = "https://example.test/releases/1.0/tool.tar.gz",
) -> str:
    return f"[[artifacts]]\nname = {json.dumps(name)}\nurl = {json.dumps(url)}\nsha256 = {json.dumps(digest)}\n"


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", ".", "..", "a/b", "a\\b", "name with space", "x" * 129]
)
def test_artifact_basename_refuses_path_interpretation(tmp_path: Path, name: str) -> None:
    with pytest.raises(QSXError, match="basename"):
        load_recipe(recipe(tmp_path, artifact_toml(name)))


@pytest.mark.parametrize(
    "extra",
    [
        artifact_toml(url="http://example.test/file"),
        artifact_toml(url="https://operator:secret@example.test/file"),
        artifact_toml(digest="bad"),
        artifact_toml() + 'host_command = "sh tool.tar.gz"\n',
    ],
)
def test_artifact_inputs_are_pinned_data(tmp_path: Path, extra: str) -> None:
    with pytest.raises(QSXError):
        load_recipe(recipe(tmp_path, extra))


def test_artifact_inheritance_override_and_profile_binding(tmp_path: Path) -> None:
    original = recipe(tmp_path, artifact_toml())
    first = load_recipe(original)
    child = tmp_path / "child.toml"
    child.write_text(
        'schema_version = 1\nname = "child"\nextends = "test.toml"\n'
        + artifact_toml(digest="b" * 64)
    )
    second = load_recipe(child)
    assert second["artifacts"] == [
        {
            "name": "tool.tar.gz",
            "url": "https://example.test/releases/1.0/tool.tar.gz",
            "sha256": "b" * 64,
        }
    ]
    assert first["recipe_sha256"] != second["recipe_sha256"]
    original.write_text(original.read_text() + artifact_toml())
    with pytest.raises(QSXError, match="duplicate artifact"):
        load_recipe(original)
    profile = tmp_path / "profile.toml"
    profile.write_text('schema_version = 1\nname = "custom"\nargv = ["sh"]\n' + artifact_toml())
    before = load_agent_profile(profile)
    profile.write_text(profile.read_text().replace('"' + "a" * 64 + '"', '"' + "b" * 64 + '"'))
    assert before["profile_sha256"] != load_agent_profile(profile)["profile_sha256"]


def test_cached_artifact_is_never_executed_or_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "must-not-exist"
    content = ("#!/bin/sh\ntouch " + str(sentinel) + "\n").encode()
    digest = hashlib.sha256(content).hexdigest()
    cache = tmp_path / "cache"
    (cache / "blobs").mkdir(parents=True)
    stored = cache / "blobs" / (digest + ".artifact")
    stored.write_bytes(content)
    resolved = load_recipe(recipe(tmp_path, artifact_toml("tool.sh", digest)))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline replay must not access network or execute a process")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    records = _freeze_artifacts(resolved, cache)
    assert not sentinel.exists()
    assert records[0]["guest_path"] == "/mnt/qsx-build/artifacts/tool.sh"
    assert stored.read_bytes() == content
    stored.write_bytes(content + b"tampered")
    with pytest.raises(QSXError, match="integrity failure"):
        _freeze_artifacts(resolved, cache)


def test_offline_lock_refuses_missing_additional_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"opaque fixture"
    digest = hashlib.sha256(content).hexdigest()
    cache = tmp_path / "cache"
    (cache / "blobs").mkdir(parents=True)
    stored = cache / "blobs" / (digest + ".artifact")
    stored.write_bytes(content)
    resolved = load_recipe(recipe(tmp_path, artifact_toml(digest=digest)))
    records = _freeze_artifacts(resolved, cache)
    lock = {
        "schema_version": 1,
        "recipe": resolved,
        "implementation_sha256": _implementation(),
        "artifacts": records,
        "base_path": "not-used.qcow2",
        "codex_path": None,
        "apk_dir": "not-used",
        "packages": [],
        "provenance": {},
    }
    lock["lock_sha256"] = _digest(lock)
    _verify_lock(lock, cache)
    stored.unlink()
    with pytest.raises(QSXError, match="missing or corrupt frozen input"):
        _verify_lock(lock, cache)


@pytest.mark.skipif(shutil.which("xorriso") is None, reason="real ISO tool unavailable")
def test_real_seed_keeps_additional_artifact_opaque(tmp_path: Path) -> None:
    content = b"not a real archive; must stay opaque"
    digest = hashlib.sha256(content).hexdigest()
    cache, work = tmp_path / "cache", tmp_path / "work"
    (cache / "blobs").mkdir(parents=True)
    (cache / "apk").mkdir()
    work.mkdir()
    (cache / "blobs" / (digest + ".artifact")).write_bytes(content)
    resolved = load_recipe(recipe(tmp_path, artifact_toml(digest=digest)))
    records = _freeze_artifacts(resolved, cache)
    lock = {
        "apk_dir": "apk",
        "packages": [],
        "codex_path": None,
        "recipe": resolved,
        "artifacts": records,
    }
    medium = _make_seed(lock, cache, work, "OPAQUE_ARTIFACT_SEED_NOT_BOOTED")
    extracted = tmp_path / "opaque.tar.gz"
    result = subprocess.run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(medium),
            "-extract",
            "/artifacts/tool.tar.gz",
            str(extracted),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert extracted.read_bytes() == content


@pytest.mark.parametrize("failure", [QSXError("failed readiness"), KeyboardInterrupt()])
def test_failed_build_keeps_disk_seed_and_log_across_next_attempt(
    tmp_path: Path, failure: BaseException
) -> None:
    with pytest.raises(type(failure)), _build_workspace(tmp_path) as first:
        for name in ("disk.qcow2", "seed.iso", "build.boot.log"):
            (first / name).write_bytes(b"failed attempt evidence")
        raise failure
    with _build_workspace(tmp_path) as second:
        assert first != second
        (second / "build.boot.log").write_bytes(b"successful attempt")
    assert not second.exists()
    for name in ("disk.qcow2", "seed.iso", "build.boot.log"):
        assert (first / name).read_bytes() == b"failed attempt evidence"
