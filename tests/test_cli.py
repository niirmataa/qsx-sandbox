from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from qsx_alpine.cli import main


def profile(tmp_path: Path) -> Path:
    path = tmp_path / "agent.toml"
    path.write_text('schema_version=1\nname="test-shell"\npackages=[]\nargv=["sh"]\n'
                    '[codex]\nenabled=false\n')
    return path


def test_new_task_recipe_roundtrip_without_controller_edit(tmp_path: Path, capsys: object) -> None:
    chosen = profile(tmp_path)
    recipe = tmp_path / "new-task.toml"
    assert main(["template-new", str(recipe), "--name", "custom-task", "--agent-profile",
                 str(chosen), "--package", "python3"]) == 0
    assert main(["template-check", str(recipe)]) == 0
    text = recipe.read_text()
    assert "python3" in text and "agent_profile" in text


def test_bad_profile_cannot_set_operator_policy(tmp_path: Path) -> None:
    chosen = profile(tmp_path)
    chosen.write_text(chosen.read_text() + '\n[policy]\nallow_host_mounts=true\n')
    recipe = tmp_path / "rejected.toml"
    assert main(["template-new", str(recipe), "--name", "bad-profile", "--agent-profile",
                 str(chosen)]) == 2


def test_doctor_does_not_claim_kvm_or_operator_login(capsys: object) -> None:
    from typing import cast

    from _pytest.capture import CaptureFixture

    capture = cast(CaptureFixture[str], capsys)
    assert main(["doctor"]) == 0
    result = json.loads(capture.readouterr().out)
    assert result["kvm_execution"] == "NOT_RUN"
    assert result["operator_login"] == "AWAITING_OPERATOR_LOGIN"


def test_run_timeout_after_instance_and_guest_flags_are_separate() -> None:
    with patch("qsx_alpine.runtime.run_guest", return_value={"returncode": 7, "stdout": ""}) as run:
        assert main(["run", "my-instance", "--timeout", "5", "--", "python3", "-V"]) == 7
        run.assert_called_once_with(Path("my-instance"), ["python3", "-V"], 5)


def test_long_source_build_timeout_is_forwarded_to_builder() -> None:
    with patch("qsx_alpine.templates.build_image", return_value={}) as build:
        assert main(["image-build", "lock.json", "--cache", "cache", "--output", "research.qcow2",
                     "--timeout", "43200", "--policy", "operator.toml"]) == 0
        build.assert_called_once_with(Path("lock.json"), Path("cache"), Path("research.qcow2"),
                                      "kvm", timeout=43200, policy=Path("operator.toml"))
