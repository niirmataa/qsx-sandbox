from __future__ import annotations

import asyncio
import dataclasses
import socket
import ssl
import subprocess
from pathlib import Path

import pytest

from qsx_alpine import egress
from qsx_alpine.errors import QSXError

HOSTS = frozenset({"auth.openai.com", "chatgpt.com"})


def policy(**changes: object) -> egress.ProxyPolicy:
    return dataclasses.replace(egress.ProxyPolicy("127.0.0.1", "127.0.0.1", HOSTS), **changes)


def hello(host: str | None = "auth.openai.com", *, ech: bool = False) -> bytes:
    extensions = b""
    if host is not None:
        encoded = host.encode()
        names = b"\0" + len(encoded).to_bytes(2) + encoded
        extension = len(names).to_bytes(2) + names
        extensions += b"\0\0" + len(extension).to_bytes(2) + extension
    if ech:
        extensions += b"\xfe\x0d\0\0"
    body = (b"\x03\x03" + bytes(32) + b"\0\0\x02\x13\x01\x01\0"
            + len(extensions).to_bytes(2) + extensions)
    return b"\x01" + len(body).to_bytes(3) + body


def record(payload: bytes) -> bytes:
    return b"\x16\x03\x01" + len(payload).to_bytes(2) + payload


@pytest.mark.parametrize("changes", [
    {"listen_address": "0.0.0.0"}, {"listen_address": "8.8.8.8"},
    {"client_address": "::1"}, {"allowed_hosts": frozenset({"*.openai.com"})},
    {"allowed_hosts": frozenset({"127.0.0.1"})}, {"allowed_hosts": frozenset({"OPENAI.com"})},
    {"max_connections": True}, {"listen_port": 80}, {"handshake_timeout_seconds": 0},
])
def test_proxy_rejects_unsafe_policy(changes: dict[str, object]) -> None:
    with pytest.raises(QSXError):
        policy(**changes)


def test_proposed_policy_parses_without_activation() -> None:
    parsed = egress.load_proxy_policy(Path(__file__).resolve().parents[1]
                                     / "deploy/egress/proxy.toml")
    assert parsed.allowed_hosts == HOSTS
    assert parsed.listen_address == "192.168.250.1"
    assert parsed.client_address == "192.168.250.2"


@pytest.mark.parametrize("header", [
    b"GET https://auth.openai.com/ HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
    b"CONNECT auth.openai.com.evil.example:443 HTTP/1.1\r\n\r\n",
    b"CONNECT auth.openai.com:80 HTTP/1.1\r\n\r\n",
    b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
    b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
    b"CONNECT auth.openai.com.:443 HTTP/1.1\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\nContent-Length: 1\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\nProxy-Authorization: secret\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: x\r\nHost: x\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\r\n folded: x\r\n\r\n",
    b"CONNECT auth.openai.com:443 HTTP/1.1\nHeader: bad\r\n\r\n",
])
def test_connect_rejects_bypass_and_smuggling(header: bytes) -> None:
    with pytest.raises(QSXError):
        egress.connect_host(header, HOSTS)


def test_clienthello_openssl_and_fragmented_records() -> None:
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing,
                                                  server_hostname="auth.openai.com")
    with pytest.raises(ssl.SSLWantReadError):
        client.do_handshake()
    wire = outgoing.read()
    payload = wire[5:]
    assert len(payload) == int.from_bytes(wire[3:5])
    assert egress.client_hello_sni(payload) == "auth.openai.com"

    async def check() -> None:
        reader = asyncio.StreamReader()
        fragmented = record(payload[:2]) + record(payload[2:41]) + record(payload[41:])
        reader.feed_data(fragmented)
        raw, host = await egress.read_client_hello(reader)
        assert raw == fragmented and host == "auth.openai.com"
    asyncio.run(check())


@pytest.mark.parametrize("payload", [
    hello(None), hello("example.com\x00"), hello("127.0.0.1"), hello(ech=True),
    hello()[:-1], hello() + b"extra", b"\x02" + hello()[1:],
])
def test_clienthello_rejects_absent_hidden_or_malformed_sni(payload: bytes) -> None:
    with pytest.raises(QSXError):
        egress.client_hello_sni(payload)


def test_clienthello_truncation_never_escapes_as_indexerror() -> None:
    payload = hello()
    for end in range(len(payload)):
        with pytest.raises(QSXError):
            egress.client_hello_sni(payload[:end])


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "192.168.0.1", "172.16.0.1", "169.254.169.254",
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "255.255.255.255", "::1", "::",
    "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1",
])
def test_dns_answers_must_be_public_unicast(address: str) -> None:
    assert not egress.public_address(address)


def test_dns_mixed_answers_rejected_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        loop = asyncio.get_running_loop()

        async def resolve(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
                    for ip in ("1.1.1.1", "127.0.0.1")]

        async def connect(*args: object, **kwargs: object) -> None:
            pytest.fail("DNS containing a private answer must not open a socket")
        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(loop, "sock_connect", connect)
        with pytest.raises(QSXError, match="non_public_dns_answer"):
            await egress.connect_public("auth.openai.com")
    asyncio.run(check())


def test_real_socket_denials_do_not_reach_upstream_or_log_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    async def check() -> None:
        async def forbidden(*args: object) -> None:
            pytest.fail("rejected client must not contact upstream")
        monkeypatch.setattr(egress, "connect_public", forbidden)
        proxy = egress.ConnectProxy(policy())
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0,
                                            limit=egress.HEADER_LIMIT)
        try:
            port = server.sockets[0].getsockname()[1]
            for request in (
                b"CONNECT secret.example.com:443 HTTP/1.1\r\n\r\n",
                b"CONNECT auth.openai.com:443 HTTP/1.1\r\nProxy-Authorization: secret\r\n\r\n",
            ):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(request)
                assert b"403 Forbidden" in await asyncio.wait_for(reader.read(), timeout=2)
                writer.close()
                await writer.wait_closed()
            for payload in (hello("example.com"), hello(None), hello(ech=True)):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(b"CONNECT auth.openai.com:443 HTTP/1.1\r\n\r\n")
                assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
                writer.write(record(payload))
                assert not await asyncio.wait_for(reader.read(), timeout=2)
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await proxy.close()
            await server.wait_closed()
    asyncio.run(check())
    logs = capsys.readouterr().out
    assert "secret" not in logs and "example.com" not in logs
    assert "sni_mismatch" in logs and "encrypted_or_duplicate_extension" in logs


@pytest.fixture
def certificates(tmp_path: Path) -> tuple[Path, Path]:
    cert, key = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=auth.openai.com", "-addext", "subjectAltName=DNS:auth.openai.com",
        "-keyout", str(key), "-out", str(cert),
    ], check=True, capture_output=True)
    return cert, key


def test_real_tls_tunnel_and_active_service_cut(
    monkeypatch: pytest.MonkeyPatch, certificates: tuple[Path, Path],
) -> None:
    """Actual CONNECT/TLS/echo/cut on loopback, not a libvirt/firewall acceptance."""
    async def check() -> None:
        cert, key = certificates
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        client_context = ssl.create_default_context(cafile=str(cert))
        remote_closed = asyncio.Event()

        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                remote_closed.set()

        remote = await asyncio.start_server(echo, "127.0.0.1", 0, ssl=server_context)
        resolved: list[str] = []

        async def local_peer(hostname: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            resolved.append(hostname)
            return await asyncio.open_connection("127.0.0.1", remote.sockets[0].getsockname()[1])

        monkeypatch.setattr(egress, "connect_public", local_peer)
        proxy = egress.ConnectProxy(policy())
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0,
                                            limit=egress.HEADER_LIMIT)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1",
                                                          server.sockets[0].getsockname()[1])
            writer.write(b"CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n")
            assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
            await writer.start_tls(client_context, server_hostname="auth.openai.com")
            assert resolved == ["auth.openai.com"]
            writer.write(b"synthetic TLS application data")
            await writer.drain()
            assert await reader.readexactly(30) == b"synthetic TLS application data"
            assert proxy.connections
            server.close()
            await proxy.close()
            await server.wait_closed()
            assert not await asyncio.wait_for(reader.read(), timeout=2)
            await asyncio.wait_for(remote_closed.wait(), timeout=2)
            assert not proxy.connections
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            server.close()
            remote.close()
            await server.wait_closed()
            await remote.wait_closed()
            await proxy.close()
    asyncio.run(asyncio.wait_for(check(), timeout=15))


def test_slow_client_deadline_and_source_address(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        proxy = egress.ConnectProxy(policy(handshake_timeout_seconds=1))
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0,
                                            limit=egress.HEADER_LIMIT)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1",
                                                          server.sockets[0].getsockname()[1])
            writer.write(b"CONNECT auth.openai.com:443 HTTP/1.1\r\n\r\n")
            await reader.readuntil(b"\r\n\r\n")
            assert not await asyncio.wait_for(reader.read(), timeout=3)
            writer.close()
            await writer.wait_closed()
            denied = egress.ConnectProxy(policy(client_address="127.0.0.2"))
            monkeypatch.setattr(proxy, "policy", denied.policy)
            reader, writer = await asyncio.open_connection("127.0.0.1",
                                                          server.sockets[0].getsockname()[1])
            assert b"403 Forbidden" in await asyncio.wait_for(reader.read(), timeout=2)
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await proxy.close()
            await server.wait_closed()
    asyncio.run(check())
