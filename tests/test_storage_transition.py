from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/transition_demo_storage.py'
spec = importlib.util.spec_from_file_location('storage_transition_test', SCRIPT)
assert spec is not None and spec.loader is not None
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


@pytest.fixture(autouse=True)
def fixture_group_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    # This operator-specific script must not require a real god group in CI.
    monkeypatch.setattr(storage.grp, 'getgrnam',
                        lambda name: SimpleNamespace(gr_gid=os.getegid()))


def test_storage_plan_sudo_is_narrow_and_never_runs_builder_as_root() -> None:
    commands = storage.initial_commands(storage.DEFAULT_QSX)
    privileged = [argv for argv in commands if argv[0] == 'sudo']
    assert len(privileged) == 6
    assert all(argv[1] == '-n' for argv in privileged)
    assert privileged[1][-2:] == [str(storage.AUTOSTART), str(storage.BACKUP / 'default.xml')]
    assert '--no-clobber' in privileged[1]
    assert privileged[4][2:] == ['/usr/bin/systemctl', 'start', 'libvirtd.socket', 'libvirtd.service']
    assert all('qsx-alpine' not in argv[2] for argv in privileged)
    assert not any(argument in {'enable', '-R', '--recursive', '777', 'chmod', 'net-start'}
                   for argv in commands for argument in argv)
    assert all(not argument.startswith('/home/') for argv in privileged for argument in argv)


@pytest.mark.parametrize('wrong', [
    'user::rwx\ngroup::r-x\nother::---\n',
    'user::rwx\nuser:64055:rwx\ngroup::---\nmask::rwx\nother::---\n',
    'user::rwx\nuser:123:r--\ngroup::---\nmask::r--\nother::---\n',
    'user::rwx\ngroup::---\nother::---\ndefault:user::rwx\n',
])
def test_storage_acl_rejects_unreviewed_or_broad_permissions(wrong: str) -> None:
    with pytest.raises(storage.TransitionError):
        storage.validate_acl(wrong, 'rwx', '--x')


def test_storage_acl_admits_only_exact_service_entry() -> None:
    storage.validate_acl('user::rwx\ngroup::---\nother::---\n', 'rwx', None)
    storage.validate_acl('user::rwx\nuser:64055:--x\ngroup::---\nmask::--x\nother::---\n',
                         'rwx', '--x')
    storage.validate_acl('user::rw-\nuser:64055:r--\ngroup::---\nmask::r--\nother::---\n',
                         'rw-', 'r--')


def test_storage_manifest_cannot_escape_root_or_follow_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(storage, 'DESTINATION', root)
    with pytest.raises(storage.TransitionError, match='direct'):
        storage.inspect_instance(tmp_path / 'instance.json')
    instance = root / 'name'
    instance.mkdir(mode=0o700)
    outside = tmp_path / 'outside'
    outside.write_text('{}')
    (instance / 'instance.json').symlink_to(outside)
    with pytest.raises(storage.TransitionError, match='ordinary path'):
        storage.inspect_instance(instance / 'instance.json')


def test_reusable_grant_lists_only_current_verified_iso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, instance = tmp_path / 'private', tmp_path / 'private/name'
    instance.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(storage, 'DESTINATION', root)
    (instance / 'disk.qcow2').write_bytes(b'synthetic inactive disk fixture')
    medium = instance / ('egress-' + 'a' * 32 + '.iso')
    medium.write_bytes(b'synthetic medium fixture')
    manifest = instance / 'instance.json'
    data = {'name': 'name', 'instance_manifest': str(manifest), 'schema_version': 1,
            'import_finalized': True, 'egress_pending': {'medium': medium.name,
                                                       'medium_sha256': storage.digest(medium)}}
    manifest.write_text(json.dumps(data))
    _, media = storage.inspect_instance(manifest)
    assert media == [medium]
    cmds = storage.acl_commands(instance, media)
    assert cmds[-1] == ['/usr/bin/setfacl', '-m', 'u:64055:r--', '--', str(medium)]
    assert not any('import.iso' in arg for command in cmds for arg in command)
    medium.write_bytes(b'tampered')
    with pytest.raises(storage.TransitionError, match='differs'):
        storage.inspect_instance(manifest)
    data['egress_pending']['medium'] = '../outside.iso'
    manifest.write_text(json.dumps(data))
    with pytest.raises(storage.TransitionError, match='unreviewed'):
        storage.inspect_instance(manifest)


@pytest.mark.parametrize('fail_second,tamper_backup', [(True, False), (False, False), (False, True)])
def test_demo_execution_pointer_changes_only_after_both_creates_and_acl_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_second: bool, tamper_backup: bool,
) -> None:
    work, evidence, destination = tmp_path / 'work', tmp_path / 'evidence', tmp_path / 'storage'
    for path in (work, evidence, destination, work / 'source', work / 'images'):
        path.mkdir(mode=0o700)
    monkeypatch.setattr(storage, 'WORK', work)
    monkeypatch.setattr(storage, 'EVIDENCE', evidence)
    monkeypatch.setattr(storage, 'DESTINATION', destination)
    monkeypatch.setattr(storage, 'JOURNAL', evidence / 'journal.json')
    (work / 'source/check.py').write_text('synthetic test')
    (work / 'images/task.qcow2').write_bytes(b'base')
    state = {'instances': ['old-a', 'old-b'], 'source_sha256': storage.digest(work / 'source/check.py'),
             'base_image': str(work / 'images/task.qcow2'),
             'base_sha256': storage.digest(work / 'images/task.qcow2')}
    original_state = json.dumps(state).encode()
    (work / 'demo-state.json').write_bytes(original_state)
    (work / 'demo-report.json').write_text('{"steps": []}')
    resources = {'memory_mib': 1024, 'vcpus': 1, 'disk_gib': 8}
    originals = [{'instance_id': 'old-' + name, 'resources': resources} for name in storage.NAMES]
    monkeypatch.setattr(storage, 'read_initial_state', lambda: (state, originals))
    monkeypatch.setattr(storage, 'prepare_host', lambda record, journal: None)
    checked: list[Path] = []
    monkeypatch.setattr(storage, 'grant_storage', lambda path, qsx, record: checked.append(path))

    class FakeCLI:
        report = evidence / 'report.json'

        def run(self, argv: list[str]) -> str:
            assert argv[1] == 'instance-create'
            assert (work / 'demo-state.json').read_bytes() == original_state
            if fail_second and argv[2] == storage.NAMES[1]:
                raise storage.TransitionError('second create failed fixture')
            directory = destination / argv[2]
            directory.mkdir(mode=0o700)
            manifest = directory / 'instance.json'
            data: dict[str, Any] = {'schema_version': 1, 'name': argv[2],
                                    'instance_manifest': str(manifest), 'instance_id': 'new-' + argv[2],
                                    'domain_defined': False, 'import_finalized': False,
                                    'resources': resources, 'image_sha256': state['base_sha256']}
            manifest.write_text(json.dumps(data))
            (directory / 'disk.qcow2').write_bytes(b'disk')
            (directory / 'import.iso').write_bytes(b'iso')
            return json.dumps({'instance_manifest': str(manifest)})

        def finish(self, result: str, **kwargs: Any) -> None:
            assert result == 'PASS'

    if tamper_backup:
        (evidence / 'storage-transition-original-demo-state.json').write_text('unrelated backup')
    if fail_second or tamper_backup:
        reason = 'second create' if fail_second else 'existing backup'
        with pytest.raises(storage.TransitionError, match=reason):
            storage.apply(Path('/fixture/qsx-alpine'), FakeCLI())
        assert (work / 'demo-state.json').read_bytes() == original_state
        assert len(checked) == (1 if fail_second else 2)
    else:
        storage.apply(Path('/fixture/qsx-alpine'), FakeCLI())
        assert len(checked) == 2
        updated = json.loads((work / 'demo-state.json').read_text())
        assert updated['instances'] == [str(destination / name / 'instance.json') for name in storage.NAMES]
        assert (evidence / 'storage-transition-original-demo-state.json').read_bytes() == original_state
        assert json.loads((evidence / 'journal.json').read_text())['phase'] == 'PUBLISHED'


@pytest.mark.parametrize('details', [
    {'backing-filename': '/home/operator/base.qcow2'},
    {'format-specific': {'type': 'qcow2', 'data': {'data-file': '/outside/disk.raw'}}},
    {'format-specific': {'data': None}},
])
def test_external_disk_storage_is_rejected_at_actual_qemu_json_location(details: dict[str, Any]) -> None:
    with pytest.raises(storage.TransitionError, match='standalone'):
        storage.verify_disk_info({'format': 'qcow2', 'virtual-size': 8 * 1024**3, **details}, 8)


def test_standalone_qemu_disk_metadata_is_accepted() -> None:
    storage.verify_disk_info({'format': 'qcow2', 'virtual-size': 8 * 1024**3,
                              'format-specific': {'type': 'qcow2', 'data': {'compat': '1.1'}}}, 8)


def test_command_timeout_preserves_partial_output_and_unknown_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, 'EVIDENCE', tmp_path)
    record = storage.Recorder('timeout-fixture')

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(['fixture'], 1, output=b'partial stdout', stderr=b'partial err')

    monkeypatch.setattr(storage.subprocess, 'run', timeout)
    with pytest.raises(storage.TransitionError, match='unknown'):
        record.run(['fixture'], timeout=1)
    saved = json.loads(record.report.read_text())
    step = saved['steps'][0]
    assert step['execution_state'] == 'UNKNOWN_DO_NOT_RETRY_BLINDLY'
    log = Path(step['log']).read_text()
    assert 'partial stdout' in log and 'partial err' in log
