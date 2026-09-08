#!/usr/bin/env python3
"""Reviewed, initially unactivated Ubuntu storage transition for local demo 001.

Run as god, never as root. Default --plan only reads and prints commands.
--apply performs the displayed sudo actions only after external operator approval.
--grant-storage MANIFEST reuses the exact owner-authorized ACL rule for one later
private instance; it cannot install services, grant /home access or execute guests.
"""
from __future__ import annotations

import argparse
import datetime
import grp
import hashlib
import json
import os
import pwd
import re
import shlex
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parents[1]
WORK = WORKSPACE / 'qsx-local-demo-001'
EVIDENCE = WORKSPACE / 'qsx-local-evidence-001'
DESTINATION = Path('/var/lib/libvirt/images/qsx-alpine-local-001')
AUTOSTART = Path('/etc/libvirt/qemu/networks/autostart/default.xml')
AUTOSTART_TARGET = '/etc/libvirt/qemu/networks/default.xml'
BACKUP = Path('/var/lib/libvirt/qsx-alpine-local-001-autostart-backup')
NAMES = ('demo-a-ecbc2e1f', 'demo-b-ecbc2e1f')
DEFAULT_QSX = WORKSPACE / 'qsx-local-tools/runtime-3d6e4dc/bin/qsx-alpine'
JOURNAL = EVIDENCE / 'storage-transition-state.json'
SCHEMA = 'qsx-ubuntu-storage-transition-v1'


class TransitionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.',
                                     delete=False) as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def check_operator() -> None:
    if (os.geteuid() == 0 or os.getuid() != os.geteuid()
            or pwd.getpwuid(os.geteuid()).pw_name != 'god'
            or os.geteuid() != pwd.getpwnam('god').pw_uid
            or pwd.getpwnam('libvirt-qemu').pw_uid != 64055
            or grp.getgrnam('god').gr_gid != pwd.getpwnam('god').pw_gid):
        raise TransitionError('run as nonroot operator god; expected libvirt-qemu UID 64055')


def owner_path(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.geteuid() or info.st_gid != grp.getgrnam('god').gr_gid:
        raise TransitionError(f'not an operator-owned ordinary path: {path}')
    for parent in path.parents:
        parent_info = parent.lstat()
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise TransitionError(f'symlink or non-directory ancestor: {parent}')


def read_initial_state() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_path = WORK / 'demo-state.json'
    owner_path(state_path)
    state = json.loads(state_path.read_text())
    originals = [str(WORK / 'instances' / name / 'instance.json') for name in NAMES]
    if (state.get('schema') != 'qsx-alpine-debian-demo-v1' or state.get('phase') != 'PREPARED'
            or state.get('work_dir') != str(WORK) or state.get('instances') != originals
            or state.get('base_image') != str(WORK / 'images/task.qcow2')):
        raise TransitionError('demo state differs from the reviewed initial mapping; inspect it')
    instances = []
    for name, manifest in zip(NAMES, originals, strict=True):
        path = Path(manifest)
        owner_path(path)
        value = json.loads(path.read_text())
        if (value.get('name') != name or value.get('instance_manifest') != manifest
                or value.get('domain_defined') is not False or value.get('import_finalized') is not False
                or value.get('network_profile') != 'offline' or value.get('phase') != 'prepared'
                or value.get('resources') != {'memory_mib': 1024, 'vcpus': 1, 'disk_gib': 8}
                or value.get('image_sha256') != state.get('base_sha256')):
            raise TransitionError('original instance is not the reviewed unused offline instance')
        instances.append(value)
    return state, instances


def initial_commands(qsx: Path) -> list[list[str]]:
    commands = [
        ['sudo', '-n', '/usr/bin/install', '-d', '-o', 'root', '-g', 'root', '-m', '0700', str(BACKUP)],
        ['sudo', '-n', '/usr/bin/mv', '--no-clobber', '-T', '--', str(AUTOSTART),
         str(BACKUP / 'default.xml')],
        ['sudo', '-n', '/usr/bin/readlink', '--', str(BACKUP / 'default.xml')],
        ['sudo', '-n', '/usr/bin/stat', '-c', '%u:%g:%a:%Y', '--', str(BACKUP / 'default.xml')],
        ['sudo', '-n', '/usr/bin/systemctl', 'start', 'libvirtd.socket', 'libvirtd.service'],
        ['sudo', '-n', '/usr/bin/install', '-d', '-o', 'god', '-g', 'god', '-m', '0700',
         str(DESTINATION)],
    ]
    for name in NAMES:
        commands.append([str(qsx), 'instance-create', name, '--image',
                         str(WORK / 'images/task.qcow2.manifest.json'), '--source', str(WORK / 'source'),
                         '--state-root', str(DESTINATION), '--policy', str(WORK / 'operator-policy.toml'),
                         '--memory-mib', '1024', '--vcpus', '1', '--disk-gib', '8'])
        commands.extend(acl_commands(DESTINATION / name, [DESTINATION / name / 'import.iso']))
    return commands


def acl_commands(directory: Path, media: list[Path]) -> list[list[str]]:
    return [
        ['/usr/bin/setfacl', '-m', 'u:64055:--x', '--', str(DESTINATION), str(directory)],
        ['/usr/bin/setfacl', '-m', 'u:64055:rw-', '--', str(directory / 'disk.qcow2')],
        *[['/usr/bin/setfacl', '-m', 'u:64055:r--', '--', str(path)] for path in media],
    ]


class Recorder:
    def __init__(self, label: str) -> None:
        EVIDENCE.mkdir(mode=0o700, exist_ok=True)
        owner_path(EVIDENCE, directory=True)
        stamp = datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%fZ')
        self.report = EVIDENCE / f'{label}-{stamp}.json'
        self.data: dict[str, Any] = {
            'schema': SCHEMA, 'started_at_utc': stamp, 'status': 'RUNNING', 'steps': [],
            'host_os_release': Path('/etc/os-release').read_text(),
            'kernel': os.uname().release, 'uid': os.geteuid(),
            'head': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'],
                                             text=True).strip(),
            'tree_status': subprocess.check_output(['git', '-C', str(REPO), 'status', '--short'],
                                                    text=True),
            'script_sha256': digest(Path(__file__)), 'vm_acceptance': 'NOT_RUN',
        }
        save(self.report, self.data)

    def run(self, argv: list[str], *, timeout: int = 1200) -> str:
        number = len(self.data['steps']) + 1
        log = self.report.with_suffix(f'.{number:02d}.log')
        entry: dict[str, Any] = {'argv': argv, 'status': 'NOT_RUN', 'log': str(log)}
        self.data['steps'].append(entry)
        save(self.report, self.data)
        print(shlex.join(argv), flush=True)
        start = time.monotonic()
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout,
                                    env={**os.environ, 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired) as exc:
            def captured(value: bytes | str | None) -> str:
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            partial_out = captured(exc.stdout) if isinstance(exc, subprocess.TimeoutExpired) else ''
            partial_err = captured(exc.stderr) if isinstance(exc, subprocess.TimeoutExpired) else ''
            log.write_text(partial_out + '\nSTDERR\n' + partial_err + '\nERROR\n' + str(exc))
            log.chmod(0o600)
            entry.update(status='BLOCKED', execution_state='UNKNOWN_DO_NOT_RETRY_BLINDLY',
                         error=str(exc), seconds=time.monotonic() - start)
            self.data['status'] = 'BLOCKED'
            save(self.report, self.data)
            raise TransitionError('command state unknown; inspect artifacts and log before retry') from exc
        log.write_text(result.stdout + '\nSTDERR\n' + result.stderr)
        log.chmod(0o600)
        entry.update(status='PASS' if result.returncode == 0 else 'FAIL', returncode=result.returncode,
                     seconds=time.monotonic() - start)
        save(self.report, self.data)
        if result.returncode:
            raise TransitionError(f'command failed ({result.returncode}); see {log}')
        return result.stdout

    def finish(self, status: str, **extra: Any) -> None:
        self.data.update(status=status, **extra)
        save(self.report, self.data)
        print(f'Report: {self.report}', flush=True)


def validate_acl(text: str, owner_permissions: str, service_permissions: str | None) -> None:
    entries = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
    expected = {f'user::{owner_permissions}', 'group::---', 'other::---'}
    if service_permissions is not None:
        expected.update({f'user:64055:{service_permissions}', f'mask::{service_permissions}'})
    if set(entries) != expected or len(entries) != len(expected):
        raise TransitionError('unexpected/default/foreign ACL; refusing to broaden its mask')


def inspect_instance(manifest: Path) -> tuple[dict[str, Any], list[Path]]:
    if (manifest.name != 'instance.json' or manifest.parent.parent != DESTINATION
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,62}', manifest.parent.name)):
        raise TransitionError('grant requires one direct instance.json under the reviewed state root')
    owner_path(DESTINATION, directory=True)
    owner_path(manifest.parent, directory=True)
    owner_path(manifest)
    data = json.loads(manifest.read_text())
    if (data.get('name') != manifest.parent.name or data.get('instance_manifest') != str(manifest)
            or data.get('schema_version') != 1):
        raise TransitionError('instance identity/path does not match manifest')
    disk = manifest.parent / 'disk.qcow2'
    owner_path(disk)
    media = []
    initial = manifest.parent / 'import.iso'
    if not data.get('import_finalized'):
        owner_path(initial)
        media.append(initial)
    pending = data.get('egress_pending')
    if pending and not pending.get('revoked'):
        medium = pending.get('medium', '')
        if not re.fullmatch(r'egress-[0-9a-f]{32}\.iso', medium):
            raise TransitionError('unreviewed egress medium path')
        path = manifest.parent / medium
        owner_path(path)
        if digest(path) != pending.get('medium_sha256'):
            raise TransitionError('egress medium differs from its manifest')
        media.append(path)
    return data, media


def verify_disk_info(info: dict[str, Any], disk_gib: int) -> None:
    specific = info.get('format-specific', {})
    details = specific.get('data', {}) if isinstance(specific, dict) else None
    if (not isinstance(details, dict) or details.get('data-file') or info.get('data-file')
            or info.get('format') != 'qcow2' or info.get('backing-filename')
            or info.get('full-backing-filename') or info.get('virtual-size') != disk_gib * 1024**3):
        raise TransitionError('disk must be standalone with the configured virtual size')


def grant_storage(manifest: Path, qsx: Path, record: Recorder) -> None:
    data, media = inspect_instance(manifest)
    status = json.loads(record.run([str(qsx), 'status', str(manifest)]))
    if status.get('state') not in {'not defined', 'shut off'}:
        raise TransitionError('instance must be undefined or shut off before storage ACL changes')
    paths = [(DESTINATION, 'rwx', '--x'), (manifest.parent, 'rwx', '--x'),
             (manifest.parent / 'disk.qcow2', 'rw-', 'rw-'),
             *[(path, 'rw-', 'r--') for path in media]]
    for path, owner, service in paths:
        before = record.run(['/usr/bin/getfacl', '--absolute-names', '--numeric', '--', str(path)])
        try:
            validate_acl(before, owner, None)
        except TransitionError:
            validate_acl(before, owner, service)
    info = json.loads(record.run(['/usr/bin/qemu-img', 'info', '--output=json',
                                 str(manifest.parent / 'disk.qcow2')]))
    verify_disk_info(info, data['resources']['disk_gib'])
    for argv in acl_commands(manifest.parent, media):
        record.run(argv)
    for path, owner, service in paths:
        validate_acl(record.run(['/usr/bin/getfacl', '--absolute-names', '--numeric', '--', str(path)]),
                     owner, service)
    # JSON and other metadata remain private. No wildcard or default ACL is set.
    validate_acl(record.run(['/usr/bin/getfacl', '--absolute-names', '--numeric', '--', str(manifest)]),
                 'rw-', None)


def autostart_entries() -> list[Path]:
    result = []
    for directory in (Path('/etc/libvirt/qemu/autostart'), AUTOSTART.parent):
        if directory.exists():
            result.extend(directory.iterdir())
    return result


def prepare_host(record: Recorder, journal: dict[str, Any]) -> None:
    if AUTOSTART.is_symlink():
        if (autostart_entries() != [AUTOSTART] or os.readlink(AUTOSTART) != AUTOSTART_TARGET
                or AUTOSTART.lstat().st_uid != 0):
            raise TransitionError('unexpected libvirt autostart configuration; explicit review required')
        info = AUTOSTART.lstat()
        journal['original_autostart'] = {'path': str(AUTOSTART), 'target': os.readlink(AUTOSTART),
                                         'uid': info.st_uid, 'gid': info.st_gid,
                                         'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns}
        save(JOURNAL, journal)
        if BACKUP.is_symlink():
            raise TransitionError('autostart backup root must not be a symbolic link')
        if BACKUP.exists() and not journal.get('backup_directory_created'):
            raise TransitionError('autostart backup directory already exists without this journal')
        record.run(initial_commands(DEFAULT_QSX)[0])
        journal['backup_directory_created'] = True
        save(JOURNAL, journal)
        record.run(initial_commands(DEFAULT_QSX)[1])
    elif not journal.get('original_autostart'):
        raise TransitionError('default autostart differs from reviewed initial state')
    target = record.run(initial_commands(DEFAULT_QSX)[2]).strip()
    if target != AUTOSTART_TARGET or AUTOSTART.exists() or AUTOSTART.is_symlink() or autostart_entries():
        raise TransitionError('default autostart backup/absence verification failed')
    metadata = record.run(initial_commands(DEFAULT_QSX)[3]).strip().split(':')
    original = journal['original_autostart']
    expected_metadata = [str(original['uid']), str(original['gid']),
                         format(original['mode'], 'o'), str(original['mtime_ns'] // 10**9)]
    if metadata != expected_metadata:
        raise TransitionError('preserved autostart symlink metadata changed')
    journal['autostart_preserved_and_disabled'] = True
    save(JOURNAL, journal)
    record.run(initial_commands(DEFAULT_QSX)[4])
    if record.run(['/usr/bin/virsh', '--connect', 'qemu:///system', 'net-list', '--name']).strip():
        raise TransitionError('an unexpected network is active; stop and review')
    defined = record.run(['/usr/bin/virsh', '--connect', 'qemu:///system', 'list', '--all', '--name'])
    if set(defined.splitlines()) & set(NAMES):
        raise TransitionError('an initial demo name is already defined; do not create another copy')
    if DESTINATION.is_symlink():
        raise TransitionError('destination root must not be a symbolic link')
    if DESTINATION.exists():
        if not journal.get('storage_root_created'):
            raise TransitionError('destination already exists without this transition journal')
    else:
        record.run(initial_commands(DEFAULT_QSX)[5])
        journal['storage_root_created'] = True
        save(JOURNAL, journal)
    owner_path(DESTINATION, directory=True)


def apply(qsx: Path, record: Recorder) -> None:
    state, originals = read_initial_state()
    state_hash = digest(WORK / 'demo-state.json')
    if JOURNAL.exists():
        owner_path(JOURNAL)
        journal = json.loads(JOURNAL.read_text())
        if (journal.get('schema') != SCHEMA or journal.get('original_state_sha256') != state_hash
                or journal.get('destination') != str(DESTINATION)):
            raise TransitionError('transition journal differs; inspect before continuing')
    else:
        journal = {'schema': SCHEMA, 'original_state_sha256': state_hash,
                   'destination': str(DESTINATION), 'created': {}, 'phase': 'PREPARED',
                   'original_report_sha256': digest(WORK / 'demo-report.json')}
        save(JOURNAL, journal)
    if digest(WORK / 'source/check.py') != state['source_sha256']:
        raise TransitionError('synthetic source changed')
    if digest(Path(state['base_image'])) != state['base_sha256']:
        raise TransitionError('base image changed')
    prepare_host(record, journal)
    for old, name in zip(originals, NAMES, strict=True):
        manifest = DESTINATION / name / 'instance.json'
        if name not in journal['created']:
            if manifest.parent.exists():
                raise TransitionError('unrecorded destination exists; inspect previous command state')
            argv = next(args for args in initial_commands(qsx)
                        if len(args) > 2 and args[1:3] == ['instance-create', name])
            result = json.loads(record.run(argv))
            if result.get('instance_manifest') != str(manifest):
                raise TransitionError('instance-create returned an unexpected destination')
            fresh, _ = inspect_instance(manifest)
            if (fresh.get('instance_id') == old['instance_id'] or fresh.get('domain_defined') is not False
                    or fresh.get('image_sha256') != state['base_sha256']
                    or fresh.get('resources') != old['resources']):
                raise TransitionError('new instance is not an independent matching prepared copy')
            journal['created'][name] = {'manifest': str(manifest), 'manifest_sha256': digest(manifest),
                                        'instance_id': fresh['instance_id']}
            save(JOURNAL, journal)
        elif digest(manifest) != journal['created'][name]['manifest_sha256']:
            raise TransitionError('created instance changed before state transition')
        grant_storage(manifest, qsx, record)
    if digest(WORK / 'demo-state.json') != state_hash:
        raise TransitionError('operator changed demo state; refusing replacement')
    # Complete both creates and exact ACL readbacks before changing either report.
    for filename in ('demo-state.json', 'demo-report.json'):
        backup = EVIDENCE / ('storage-transition-original-' + filename)
        expected_hash = (state_hash if filename == 'demo-state.json'
                         else journal['original_report_sha256'])
        if not backup.exists():
            if digest(WORK / filename) != expected_hash:
                raise TransitionError('original report/state changed before backup')
            with backup.open('xb') as stream:
                stream.write((WORK / filename).read_bytes())
            backup.chmod(0o600)
        owner_path(backup)
        if digest(backup) != expected_hash:
            raise TransitionError('existing backup is not the recorded original; do not overwrite it')
    updated = {**state, 'instances': [str(DESTINATION / name / 'instance.json') for name in NAMES],
               'storage_transition': {'original_instances': state['instances'],
                                      'journal': str(JOURNAL), 'report': str(record.report)}}
    report_path = WORK / 'demo-report.json'
    report = json.loads(report_path.read_text())
    report.setdefault('steps', []).append({'step': 'dedicated Ubuntu libvirt storage transition',
                                          'status': 'PASS', 'report': str(record.report),
                                          'detail': 'Two new standalone disks and exact ACLs; VM boot NOT_RUN'})
    # State is the execution pointer. If interrupted between the two atomic writes,
    # the backup/journal records the intended transition and no VM has been started.
    save(report_path, report)
    save(WORK / 'demo-state.json', updated)
    journal['phase'] = 'PUBLISHED'
    save(JOURNAL, journal)
    record.finish('PASS', new_instances=updated['instances'], next_command=[
        str(REPO / '.venv/bin/python'), str(REPO / 'scripts/demo_debian.py'), '--continue',
        '--work-dir', str(WORK), '--qsx', str(qsx)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--grant-storage', type=Path, metavar='INSTANCE_JSON')
    parser.add_argument('--qsx', type=Path, default=DEFAULT_QSX)
    args = parser.parse_args()
    record: Recorder | None = None
    try:
        check_operator()
        if not args.qsx.is_absolute() or not args.qsx.is_file():
            raise TransitionError('--qsx must name an existing absolute installed CLI executable')
        if not args.apply and args.grant_storage is None:
            read_initial_state()
            print(json.dumps({'status': 'NOT_RUN', 'operator_approval_required': True,
                              'destination': str(DESTINATION), 'initial_names': NAMES,
                              'autostart_change': 'preserve and temporarily disable only default.xml',
                              'commands': [shlex.join(argv) for argv in initial_commands(args.qsx)],
                              'publish_after': 'both creates and exact ACL/disk readbacks succeed',
                              'no_changes': ['home ACLs', 'groups', 'AppArmor', 'guest/network start',
                                             'existing images and instances', 'service enable/autostart']},
                             indent=2))
            return
        record = Recorder('storage-transition' if args.apply else 'instance-storage-acl')
        if args.apply:
            apply(args.qsx, record)
        else:
            grant_storage(args.grant_storage, args.qsx, record)
            record.finish('PASS', instance_manifest=str(args.grant_storage))
    except (TransitionError, OSError, ValueError, KeyError) as exc:
        if record is not None:
            record.finish('BLOCKED', reason=str(exc))
        parser.exit(1, f'storage transition: {exc}\n')


if __name__ == '__main__':
    main()
