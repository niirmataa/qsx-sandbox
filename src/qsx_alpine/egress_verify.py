"""Fixed-path, read-only deployment verifier for the narrow sudo helper.

Only --seal writes: it is an explicit installation action unavailable through
qsx-egress-verify. The normal helper accepts no arguments and changes no state.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from .egress import ProxyPolicy, load_proxy_policy
from .errors import QSXError

PACKAGE = Path(__file__).resolve().parent
CONFIG = Path('/etc/qsx-egress/proxy.toml')
FIREWALL = Path('/etc/qsx-egress/firewall.nft')
UNIT = Path('/etc/systemd/system/qsx-egress-proxy.service')
HELPER = Path('/usr/local/sbin/qsx-egress-verify')
MANIFEST = Path('/etc/qsx-egress/deployment.json')
PYTHON = Path('/opt/qsx-egress/bin/python')
UNIT_NAME = 'qsx-egress-proxy.service'
TABLE = 'qsx_codex_egress'
EXPECTED_HOSTS = frozenset({'auth.openai.com', 'chatgpt.com'})


def trusted_path(path: Path) -> None:
    """Require root ownership and no group/world writes throughout a fixed path."""
    if not path.is_absolute():
        raise QSXError('deployment path is not absolute')
    for item in (path, *path.parents):
        info = item.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise QSXError('deployment contains an untrusted path')


def installed_files() -> list[Path]:
    if PACKAGE != Path('/opt/qsx-egress/lib/python3.12/site-packages/qsx_alpine') and PACKAGE != Path(
        '/opt/qsx-egress/lib/python3.13/site-packages/qsx_alpine'
    ):
        raise QSXError('verifier must run from the dedicated root-owned installation')
    paths = [CONFIG, FIREWALL, UNIT, HELPER, PYTHON.resolve(), *sorted(PACKAGE.glob('*.py'))]
    # Protect the interpreter symlink itself and every directory traversed before
    # its final target. A trusted system interpreter alone is insufficient.
    trusted_path(PYTHON.parent)
    if PYTHON.lstat().st_uid != 0:
        raise QSXError('untrusted interpreter link')
    for path in paths:
        trusted_path(path)
        if not path.is_file():
            raise QSXError('deployment input is not a regular file')
    return paths


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def seal() -> None:
    if os.geteuid() != 0:
        raise QSXError('sealing requires an explicit root installation action')
    hashes = {str(path): file_hash(path) for path in installed_files()}
    with MANIFEST.open('x') as stream:
        json.dump({'schema_version': 1, 'files': hashes}, stream, sort_keys=True, indent=2)
        stream.write('\n')
    MANIFEST.chmod(0o644)


def verify_seal() -> str:
    trusted_path(MANIFEST)
    manifest = json.loads(MANIFEST.read_text())
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
            or set(manifest) != {'schema_version', 'files'}):
        raise QSXError('invalid deployment manifest')
    actual = {str(path): file_hash(path) for path in installed_files()}
    if manifest['files'] != actual:
        raise QSXError('deployment differs from the root-sealed manifest')
    return file_hash(MANIFEST)


def nft_tokens(source: str) -> list[str]:
    """Ignore formatting, quoting and statement delimiters, not rule semantics."""
    lexer = shlex.shlex(source, posix=True, punctuation_chars='{};')
    lexer.whitespace_split = True
    tokens = [token for token in lexer if token != ';']
    # nft's printer may omit optional `meta` before these interface keys.
    return [token for index, token in enumerate(tokens)
            if not (token == 'meta' and index + 1 < len(tokens)
                    and tokens[index + 1] in {'iifname', 'oifname', 'ibrname', 'obrname'})]


def expected_tables(source: str) -> dict[str, list[str]]:
    tokens = nft_tokens(source)
    tables: dict[str, list[str]] = {}
    offset = 0
    while offset < len(tokens):
        start = offset
        if (tokens[offset:offset + 1] != ['table'] or len(tokens) < offset + 4
                or tokens[offset + 2:offset + 4] != [TABLE, '{']):
            raise QSXError('invalid fixed firewall file')
        family = tokens[offset + 1]
        if family not in {'inet', 'bridge'} or family in tables:
            raise QSXError('unexpected firewall table')
        offset += 4
        depth = 1
        while depth and offset < len(tokens):
            if tokens[offset] == '{':
                depth += 1
            elif tokens[offset] == '}':
                depth -= 1
            offset += 1
        if depth:
            raise QSXError('unterminated firewall table')
        tables[family] = tokens[start:offset]
    if set(tables) != {'inet', 'bridge'}:
        raise QSXError('both dedicated firewall families are required')
    return tables


def command(argv: list[str]) -> str:
    result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=15,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise QSXError('deployment observation command failed')
    if len(result.stdout) > 1048576:
        raise QSXError('deployment observation exceeded size limit')
    return result.stdout


def verify_tables() -> None:
    expected = expected_tables(FIREWALL.read_text())
    for family in ('inet', 'bridge'):
        observed = command(['/usr/sbin/nft', '--stateless', '--numeric-priority',
                            'list', 'table', family, TABLE])
        if nft_tokens(observed) != expected[family]:
            raise QSXError(f'{family} firewall table differs from approved deployment')


def listener_inodes(source: str, wanted: str) -> tuple[set[str], set[str]]:
    correct: set[str] = set()
    other: set[str] = set()
    for line in source.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 10 or columns[3] != '0A':
            continue
        (correct if columns[1] == wanted else other).add(columns[9])
    return correct, other


def verify_service() -> int:
    props = dict(line.split('=', 1) for line in command([
        '/usr/bin/systemctl', 'show', UNIT_NAME, '--property=ActiveState,SubState,MainPID,FragmentPath'
    ]).splitlines() if '=' in line)
    if (props.get('ActiveState') != 'active' or props.get('SubState') != 'running'
            or props.get('FragmentPath') != str(UNIT)):
        raise QSXError('dedicated proxy service is not running from the approved unit')
    try:
        pid = int(props.get('MainPID', '0'))
    except ValueError as exc:
        raise QSXError('invalid proxy process identity') from exc
    if pid <= 1:
        raise QSXError('missing proxy process')
    process = Path('/proc') / str(pid)
    argv = (process / 'cmdline').read_bytes().split(b'\0')
    expected = [str(PYTHON).encode(), b'-I', b'-m', b'qsx_alpine.egress',
                b'--config', str(CONFIG).encode(), b'']
    if argv != expected or (process / 'exe').resolve() != PYTHON.resolve():
        raise QSXError('proxy process does not match the approved executable')
    status = (process / 'status').read_text().splitlines()
    identities = next((line.split()[1:] for line in status if line.startswith('Uid:')), [])
    if len(identities) != 4 or any(value == '0' for value in identities):
        raise QSXError('proxy must run without root identity')
    owned = set()
    for descriptor in (process / 'fd').iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        if target.startswith('socket:[') and target.endswith(']'):
            owned.add(target[8:-1])
    # 192.168.250.1:3128 in Linux /proc/net/tcp's little-endian IPv4 format.
    wanted, other = listener_inodes((process / 'net/tcp').read_text(), '01FAA8C0:0C38')
    _, ipv6 = listener_inodes((process / 'net/tcp6').read_text(), '')
    if len(wanted & owned) != 1 or (other | ipv6) & owned:
        raise QSXError('proxy listener must be exactly 192.168.250.1:3128')
    return pid


def verify() -> dict[str, Any]:
    if os.geteuid() != 0:
        raise QSXError('read-only verification requires the dedicated sudo helper')
    digest = verify_seal()
    policy = load_proxy_policy(CONFIG)
    expected = ProxyPolicy('192.168.250.1', '192.168.250.2', EXPECTED_HOSTS)
    if policy != expected:
        raise QSXError('proxy policy differs from the selected deployment')
    verify_tables()
    verify_service()
    # Read back again after observing the live process/rules to catch changes
    # during this snapshot; runtime additionally checks the libvirt definition.
    if verify_seal() != digest:
        raise QSXError('deployment changed during verification')
    return {'schema_version': 1, 'status': 'PASS', 'network_name': 'qsx-codex-egress',
            'bridge': 'qsx-egress0', 'proxy_url': 'http://192.168.250.1:3128',
            'client_address': '192.168.250.2', 'allowed_hosts': sorted(EXPECTED_HOSTS),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'deployment_sha256': digest}


def main() -> None:
    try:
        if sys.argv[1:] == ['--seal']:
            seal()
            print(json.dumps({'status': 'SEALED_NOT_ACTIVATED'}))
        elif sys.argv[1:]:
            raise QSXError('verification accepts no arguments')
        else:
            print(json.dumps(verify(), sort_keys=True))
    except (QSXError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Errors contain fixed descriptions only; do not emit command output.
        print(json.dumps({'status': 'FAIL', 'reason': str(exc)}))
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
