"""Explicit TLS CONNECT relay; deployment also requires host network isolation.

This service never changes libvirt, routes, firewall rules or guest configuration.
It is one part of the operator-approved egress transport, not a VM boundary alone.
Only CONNECT to exact configured hostnames on 443 is accepted. Before any remote
socket is opened, ClientHello SNI must equal that hostname and ECH is rejected.
TLS remains end-to-end: the relay does not decrypt credentials or application data.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import re
import signal
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import QSXError

HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
HEADER_LIMIT = 8192
HELLO_LIMIT = 65536


@dataclass(frozen=True)
class ProxyPolicy:
    listen_address: str
    client_address: str
    allowed_hosts: frozenset[str]
    listen_port: int = 3128
    max_connections: int = 32
    handshake_timeout_seconds: int = 10
    idle_timeout_seconds: int = 120
    session_timeout_seconds: int = 3600

    def __post_init__(self) -> None:
        try:
            local = ipaddress.IPv4Address(self.listen_address)
            client = ipaddress.IPv4Address(self.client_address)
        except ipaddress.AddressValueError as exc:
            raise QSXError("proxy addresses must be explicit IPv4 addresses") from exc
        if any(ip.is_unspecified or ip.is_multicast or ip.is_global for ip in (local, client)):
            raise QSXError("proxy requires private or loopback addresses, never wildcard/public")
        if (not isinstance(self.allowed_hosts, frozenset) or not self.allowed_hosts
                or len(self.allowed_hosts) > 16
                or any(not isinstance(host, str) or len(host) > 253 or not HOST.fullmatch(host)
                       or _is_ip(host) for host in self.allowed_hosts)):
            raise QSXError("allowed_hosts must contain exact lowercase DNS names")
        for key, minimum, maximum in (
            ("listen_port", 1024, 65535), ("max_connections", 1, 128),
            ("handshake_timeout_seconds", 1, 60), ("idle_timeout_seconds", 1, 600),
            ("session_timeout_seconds", 1, 86400),
        ):
            value = getattr(self, key)
            if type(value) is not int or not minimum <= value <= maximum:
                raise QSXError(f"invalid proxy policy value: {key}")


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def load_proxy_policy(path: Path) -> ProxyPolicy:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise QSXError("cannot read proxy policy") from exc
    if raw.pop("schema_version", None) != 1 or set(raw) - set(ProxyPolicy.__dataclass_fields__):
        raise QSXError("invalid proxy policy schema or unknown fields")
    hosts = raw.get("allowed_hosts")
    if not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts):
        raise QSXError("allowed_hosts must be a list of exact DNS names")
    raw["allowed_hosts"] = frozenset(hosts)
    try:
        return ProxyPolicy(**raw)
    except TypeError as exc:
        raise QSXError("proxy policy is missing required fields") from exc


def connect_host(header: bytes, allowed_hosts: frozenset[str]) -> str:
    """Deliberately small CONNECT grammar; never forward HTTP headers upstream."""
    if len(header) > HEADER_LIMIT or not header.endswith(b"\r\n\r\n"):
        raise QSXError("invalid_connect")
    try:
        lines = header[:-4].decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise QSXError("invalid_connect") from exc
    fields = lines[0].split(" ")
    if len(fields) != 3 or fields[0] != "CONNECT" or fields[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise QSXError("invalid_connect")
    authority = fields[1]
    hostname, colon, port = authority.rpartition(":")
    if colon != ":" or port != "443" or hostname not in allowed_hosts:
        raise QSXError("destination_denied")
    seen: set[str] = set()
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        key = key.lower()
        if (not separator or not re.fullmatch(r"[a-z0-9-]+", key) or key in seen
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or key in {"content-length", "transfer-encoding", "proxy-authorization"}):
            raise QSXError("invalid_connect")
        if key == "host" and value.strip() != authority:
            raise QSXError("invalid_connect")
        seen.add(key)
    return hostname


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, length: int) -> bytes:
        end = self.offset + length
        if end > len(self.data):
            raise QSXError("invalid_client_hello")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def integer(self, length: int) -> int:
        return int.from_bytes(self.take(length), "big")

    def vector(self, length_bytes: int) -> bytes:
        return self.take(self.integer(length_bytes))

    def done(self) -> bool:
        return self.offset == len(self.data)


def client_hello_sni(handshake: bytes) -> str:
    """Read a complete TLS ClientHello, including fragmented TLS record payloads."""
    hello = _Cursor(handshake)
    if hello.integer(1) != 1:
        raise QSXError("invalid_client_hello")
    body = _Cursor(hello.vector(3))
    if not hello.done() or body.take(2) not in {b"\x03\x01", b"\x03\x02", b"\x03\x03"}:
        raise QSXError("invalid_client_hello")
    body.take(32)
    if len(body.vector(1)) > 32:
        raise QSXError("invalid_client_hello")
    ciphers = body.vector(2)
    if not ciphers or len(ciphers) % 2 or not body.vector(1):
        raise QSXError("invalid_client_hello")
    extensions = _Cursor(body.vector(2))
    if not body.done():
        raise QSXError("invalid_client_hello")
    seen: set[int] = set()
    hostname: str | None = None
    while not extensions.done():
        kind = extensions.integer(2)
        value = extensions.vector(2)
        if kind in seen or kind == 0xFE0D:
            raise QSXError("encrypted_or_duplicate_extension")
        seen.add(kind)
        if kind == 0:
            names = _Cursor(value)
            name_list = _Cursor(names.vector(2))
            if not names.done() or name_list.integer(1) != 0:
                raise QSXError("invalid_sni")
            try:
                hostname = name_list.vector(2).decode("ascii")
            except UnicodeDecodeError as exc:
                raise QSXError("invalid_sni") from exc
            if not name_list.done() or not HOST.fullmatch(hostname) or _is_ip(hostname):
                raise QSXError("invalid_sni")
    if hostname is None:
        raise QSXError("missing_sni")
    return hostname


async def read_client_hello(reader: asyncio.StreamReader) -> tuple[bytes, str]:
    records = bytearray()
    payload = bytearray()
    while True:
        header = await reader.readexactly(5)
        length = int.from_bytes(header[3:], "big")
        if (header[0] != 22 or header[1] != 3 or header[2] not in {1, 2, 3}
                or not 0 < length <= 18432 or len(records) + length + 5 > HELLO_LIMIT):
            raise QSXError("invalid_tls_record")
        chunk = await reader.readexactly(length)
        records.extend(header + chunk)
        payload.extend(chunk)
        if len(payload) >= 4:
            expected = int.from_bytes(payload[1:4], "big") + 4
            if expected > HELLO_LIMIT or payload[0] != 1:
                raise QSXError("invalid_client_hello")
            if len(payload) >= expected:
                # No extra plaintext handshake/application bytes before validation.
                return bytes(records), client_hello_sni(bytes(payload))


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_unspecified


async def connect_public(hostname: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Resolve once, validate every answer, connect the exact validated sockaddr."""
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM,
                                     proto=socket.IPPROTO_TCP)
    if not answers:
        raise QSXError("non_public_dns_answer")
    for family, _, _, _, address in answers:
        resolved_ip = address[0]
        if (family not in {socket.AF_INET, socket.AF_INET6} or not isinstance(resolved_ip, str)
                or not public_address(resolved_ip)):
            raise QSXError("non_public_dns_answer")
    for family, kind, protocol, _, address in answers:
        sock = socket.socket(family, kind, protocol)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(loop.sock_connect(sock, address), timeout=5)
            return await asyncio.open_connection(sock=sock)
        except (OSError, TimeoutError):
            sock.close()
        except BaseException:
            sock.close()
            raise
    raise QSXError("upstream_unavailable")


class ConnectProxy:
    def __init__(self, policy: ProxyPolicy) -> None:
        self.policy = policy
        self.connections: set[asyncio.Task[None]] = set()

    @staticmethod
    def audit(event: str, hostname: str | None = None) -> None:
        # Never log rejected request data, query strings, TLS bytes or peer errors.
        print(json.dumps({"event": event, "allowed_host": hostname}), flush=True)

    async def close(self) -> None:
        active = list(self.connections)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)

    async def _copy(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await asyncio.wait_for(reader.read(65536),
                                            self.policy.idle_timeout_seconds):
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self.policy.idle_timeout_seconds)

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     remote_reader: asyncio.StreamReader,
                     remote_writer: asyncio.StreamWriter) -> None:
        tasks = [asyncio.create_task(self._copy(reader, remote_writer)),
                 asyncio.create_task(self._copy(remote_reader, writer))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        remote_writer: asyncio.StreamWriter | None = None
        established = False
        hostname: str | None = None
        peer = writer.get_extra_info("peername")
        registered = False
        try:
            if not peer or peer[0] != self.policy.client_address:
                raise QSXError("source_denied")
            if len(self.connections) >= self.policy.max_connections:
                raise QSXError("connection_limit")
            self.connections.add(task)
            registered = True
            async with asyncio.timeout(self.policy.handshake_timeout_seconds):
                header = await reader.readuntil(b"\r\n\r\n")
                hostname = connect_host(header, self.policy.allowed_hosts)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                established = True
                hello, sni = await read_client_hello(reader)
                if sni != hostname:
                    raise QSXError("sni_mismatch")
                remote_reader, remote_writer = await connect_public(hostname)
                remote_writer.write(hello)
                await remote_writer.drain()
            self.audit("tunnel_open", hostname)
            async with asyncio.timeout(self.policy.session_timeout_seconds):
                await self._relay(reader, writer, remote_reader, remote_writer)
            self.audit("tunnel_closed", hostname)
        except QSXError as exc:
            self.audit(str(exc), hostname)
            if not established:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                with contextlib.suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.drain(), timeout=1)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            self.audit("transport_closed", hostname)
        finally:
            for stream in (remote_writer, writer):
                if stream is not None:
                    stream.close()
                    with contextlib.suppress(OSError, TimeoutError):
                        await asyncio.wait_for(stream.wait_closed(), timeout=1)
            if registered:
                self.connections.discard(task)


async def serve_proxy(policy: ProxyPolicy) -> None:
    proxy = ConnectProxy(policy)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    server = await asyncio.start_server(proxy.handle, policy.listen_address, policy.listen_port,
                                        limit=HEADER_LIMIT)
    try:
        proxy.audit("ready")
        await stopped.wait()
    finally:
        server.close()
        await proxy.close()
        await server.wait_closed()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="validate only; do not bind a socket")
    args = parser.parse_args()
    try:
        policy = load_proxy_policy(args.config)
        if args.check:
            print(json.dumps({"status": "VALID_CONFIG_NOT_DEPLOYED",
                              "allowed_hosts": sorted(policy.allowed_hosts)}))
        else:
            asyncio.run(serve_proxy(policy))
    except (QSXError, OSError) as exc:
        parser.exit(1, f"egress: {exc}\n")


if __name__ == "__main__":
    main()
