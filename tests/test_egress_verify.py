from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from qsx_alpine import egress_verify as verify
from qsx_alpine.errors import QSXError

ROOT = Path(__file__).resolve().parents[1]


def test_both_firewall_tables_are_required_and_rule_changes_detected() -> None:
    source = (ROOT / 'deploy/egress/firewall.nft').read_text()
    tables = verify.expected_tables(source)
    assert set(tables) == {'inet', 'bridge'}
    assert tables['inet'].count('drop') == 4
    assert tables['bridge'].count('drop') == 4
    altered = source.replace('tcp dport 3128 accept', 'tcp dport 22 accept', 1)
    assert verify.expected_tables(altered)['inet'] != tables['inet']
    with pytest.raises(QSXError):
        verify.expected_tables(source.split('table bridge')[0])
    with pytest.raises(QSXError):
        verify.expected_tables(source.replace('qsx_codex_egress', 'other', 1))
    with pytest.raises(QSXError):
        verify.expected_tables(source + '\nflush ruleset\n')


def test_firewall_readback_checks_every_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    source = (ROOT / 'deploy/egress/firewall.nft').read_text()
    tables = verify.expected_tables(source)
    monkeypatch.setattr(verify, 'FIREWALL', ROOT / 'deploy/egress/firewall.nft')
    calls: list[list[str]] = []

    def observe(argv: list[str]) -> str:
        calls.append(argv)
        return ' '.join(tables[argv[-2]])

    monkeypatch.setattr(verify, 'command', observe)
    verify.verify_tables()
    assert len(calls) == 2
    assert all(args[:3] == ['/usr/sbin/nft', '--stateless', '--numeric-priority'] for args in calls)
    monkeypatch.setattr(verify, 'command', lambda args: ' '.join(tables[args[-2]]).replace(
        '3128 accept', '22 accept'))
    with pytest.raises(QSXError, match='differs'):
        verify.verify_tables()


def test_canonicalization_ignores_only_printing_variation() -> None:
    assert verify.nft_tokens('iifname "qsx-egress0" counter drop') == verify.nft_tokens(
        'meta iifname qsx-egress0 counter drop; # observation')
    assert verify.nft_tokens('iifname "qsx-egress0" counter drop') != verify.nft_tokens(
        'iifname "qsx-egress0" counter accept')
    assert verify.nft_tokens('priority -20; policy accept;') != verify.nft_tokens(
        'priority 20; policy accept;')


def test_manifest_covers_exact_files_and_detects_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = tmp_path / 'egress.py'
    code.write_text('test fixture')
    manifest = tmp_path / 'deployment.json'
    monkeypatch.setattr(verify, 'MANIFEST', manifest)
    monkeypatch.setattr(verify, 'installed_files', lambda: [code])
    monkeypatch.setattr(verify, 'trusted_path', lambda path: None)
    monkeypatch.setattr(verify.os, 'geteuid', lambda: 0)
    verify.seal()
    assert verify.verify_seal() == verify.file_hash(manifest)
    with pytest.raises(FileExistsError):
        verify.seal()
    code.write_text('changed after seal')
    with pytest.raises(QSXError, match='differs'):
        verify.verify_seal()
    saved = json.loads(manifest.read_text())
    saved['files']['/unexpected'] = 'a' * 64
    manifest.write_text(json.dumps(saved))
    with pytest.raises(QSXError, match='differs'):
        verify.verify_seal()


@pytest.mark.parametrize('owner,mode', [(1000, 0o100644), (0, 0o100664), (0, 0o120777)])
def test_root_verifier_rejects_untrusted_paths(
    monkeypatch: pytest.MonkeyPatch, owner: int, mode: int,
) -> None:
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_uid=owner, st_mode=mode))
    with pytest.raises(QSXError, match='untrusted'):
        verify.trusted_path(Path('/etc/qsx-egress/proxy.toml'))


def test_verifier_does_not_run_from_workspace() -> None:
    with pytest.raises(QSXError, match='dedicated root-owned installation'):
        verify.installed_files()


def test_listener_observation_matches_port_address_and_listen_state() -> None:
    header = ' sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode'
    lines = [header,
             '0: 01FAA8C0:0C38 00000000:0000 0A 0 0 0 0 0 123',
             '1: 00000000:0C38 00000000:0000 0A 0 0 0 0 0 124',
             '2: 01FAA8C0:0C38 00000000:0000 01 0 0 0 0 0 125',
             '3: 01FAA8C0:0016 00000000:0000 0A 0 0 0 0 0 126']
    exact, other = verify.listener_inodes('\n'.join(lines), '01FAA8C0:0C38')
    assert exact == {'123'} and other == {'124', '126'}


def test_wrapper_has_no_argument_or_environment_injection_surface() -> None:
    wrapper = (ROOT / 'deploy/egress/qsx-egress-verify').read_text()
    assert '[ "$#" -eq 0 ] || exit 64' in wrapper
    assert 'exec /opt/qsx-egress/bin/python -I -m qsx_alpine.egress_verify' in wrapper
    assert '$@' not in wrapper and 'PYTHONPATH' not in wrapper
    rule = (ROOT / 'deploy/egress/sudoers.example').read_text()
    assert 'NOPASSWD: /usr/local/sbin/qsx-egress-verify ""' in rule
    assert '*' not in rule


def test_helper_cli_rejects_any_argument_without_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify.sys, 'argv', ['qsx-egress-verify', '/tmp/evil'])
    monkeypatch.setattr(verify, 'verify', lambda: pytest.fail('must reject first'))
    with pytest.raises(SystemExit) as failed:
        verify.main()
    assert failed.value.code == 1


def test_unprivileged_verification_does_not_access_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(verify, 'verify_seal', lambda: pytest.fail('must reject first'))
    with pytest.raises(QSXError, match='dedicated sudo helper'):
        verify.verify()
