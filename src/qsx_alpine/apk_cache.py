"""Freeze signed Alpine packages without installing anything on the controller.

Only the explicit first freeze accesses the network. A cache hit verifies the
complete locked file set and signatures; it never refreshes repository indexes.
The native APK executable and its keys are extracted anew from a pinned official
minirootfs. Package scripts are never executed by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import QSXError
from .provenance import verify_gpg_signature
from .util import atomic_json, sha256

ALPINE_RELEASE = "3.24.1"
ARCHITECTURE = "x86_64"
MINIROOTFS_NAME = f"alpine-minirootfs-{ALPINE_RELEASE}-{ARCHITECTURE}.tar.gz"
MINIROOTFS_URL = (
    f"https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/{ARCHITECTURE}/"
    f"{MINIROOTFS_NAME}"
)
# Official adjacent .sha512 retrieved and checked on 2026-09-06.
MINIROOTFS_SHA512 = (
    "de367c4fd4b5a2ab17f30fe62b135b98acd76c22014abdf51285588bde0edb7275265a84"
    "400418a5dab8cbdec36b3a41ca1312ca3af8e54438036455f26d72c1"
)
MINIROOTFS_SIGNER = "0482D84022F52DF1C4E7CD43293ACD0907D9495A"
MINIROOTFS_KEY_URL = "https://alpinelinux.org/keys/ncopa.asc"
REPOSITORIES = [
    "https://dl-cdn.alpinelinux.org/alpine/v3.24/main",
    "https://dl-cdn.alpinelinux.org/alpine/v3.24/community",
]
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+_.-]*(?:=[A-Za-z0-9][A-Za-z0-9+_.:~-]*)?\Z")
_FROZEN_DIRS = ("inputs", "packages", "indexes", "keys")


def _requests(packages: list[str]) -> list[str]:
    if not packages or len(packages) > 256:
        raise QSXError("APK freeze requires between 1 and 256 package names")
    if any(not isinstance(item, str) or not _PACKAGE.fullmatch(item) for item in packages):
        raise QSXError("APK packages must be names or exact name=version pins")
    return sorted(set(packages))


def _sha512(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha512").hexdigest()


def _download_minrootfs(path: Path) -> None:
    try:
        with urllib.request.urlopen(MINIROOTFS_URL, timeout=60) as source, path.open("xb") as out:
            total = 0
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > 64 * 1024 * 1024:
                    raise QSXError("Alpine minirootfs exceeds the 64 MiB bootstrap limit")
                out.write(block)
    except (OSError, urllib.error.URLError) as exc:
        raise QSXError(f"could not download the pinned Alpine minirootfs: {exc}") from exc
    if _sha512(path) != MINIROOTFS_SHA512:
        raise QSXError("Alpine minirootfs SHA-512 mismatch; refusing to execute its APK runtime")


def _download_bootstrap_signature(root: Path) -> None:
    for url, path in (
        (MINIROOTFS_URL + ".asc", root / "inputs" / (MINIROOTFS_NAME + ".asc")),
        (MINIROOTFS_KEY_URL, root / "inputs" / "ncopa.asc"),
    ):
        with urllib.request.urlopen(url, timeout=60) as source:
            data = source.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            raise QSXError("Alpine bootstrap signature or public key exceeds size limit")
        with path.open("xb") as output:
            output.write(data)


def _extract_runtime(archive: Path, runtime: Path) -> None:
    if _sha512(archive) != MINIROOTFS_SHA512:
        raise QSXError("cached Alpine minirootfs SHA-512 mismatch")
    required = {
        "sbin/apk", "lib/ld-musl-x86_64.so.1", "lib/libc.musl-x86_64.so.1",
        "etc/ssl/certs/ca-certificates.crt",
    }
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar:
            name = member.name.removeprefix("./")
            if (
                name in required
                or name.startswith("etc/apk/keys/")
                or (name.startswith("usr/lib/lib") and "/" not in name[len("usr/lib/"):])
            ):
                members.append(member)
        # The full minirootfs includes absolute BusyBox links. They are not
        # needed here; the selected runtime still passes Python's data filter.
        tar.extractall(runtime, members=members, filter="data")
    if not (runtime / "sbin/apk").is_file() or not list((runtime / "etc/apk/keys").glob("*")):
        raise QSXError("pinned Alpine archive lacks the required APK runtime or trust keys")


def _check_apk_result(result: subprocess.CompletedProcess[str]) -> str:
    output = result.stdout + result.stderr
    # apk-tools 3.0.6 fetch can report solver ERROR while exiting with code 0.
    if result.returncode or re.search(r"(?m)^(?:ERROR|WARNING):", output):
        detail = output.strip()[-6000:]
        raise QSXError(f"Alpine APK operation failed ({result.returncode}): {detail}")
    return result.stdout.strip()


@dataclass
class _Apk:
    runtime: Path
    resolver: Path
    repositories: Path
    cache: Path
    bootstrap_signature: dict[str, Any] = field(default_factory=dict)

    def run(self, *args: str, network: bool = False) -> str:
        command = [
            str(self.runtime / "lib/ld-musl-x86_64.so.1"), "--library-path",
            f"{self.runtime}/lib:{self.runtime}/usr/lib", str(self.runtime / "sbin/apk"),
            "--root", str(self.resolver), "--keys-dir", str(self.runtime / "etc/apk/keys"),
            "--repositories-file", str(self.repositories), "--cache-dir", str(self.cache),
            "--arch", ARCHITECTURE, "--network=yes" if network else "--network=no",
            "--logfile=no", "--progress=no", "--timeout", "60", *args,
        ]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("APK_")}
        environment["SSL_CERT_FILE"] = str(self.runtime / "etc/ssl/certs/ca-certificates.crt")
        try:
            result = subprocess.run(
                command, cwd=self.resolver, env=environment, capture_output=True,
                text=True, timeout=900, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QSXError(f"cannot run the private Alpine APK resolver: {exc}") from exc
        return _check_apk_result(result)


@contextmanager
def _runtime(root: Path, archive: Path) -> Iterator[_Apk]:
    with tempfile.TemporaryDirectory(prefix=".apk-verify-", dir=root) as temporary:
        work = Path(temporary)
        runtime = work / "runtime"
        runtime.mkdir(mode=0o700)
        bootstrap_signature = verify_gpg_signature(
            archive, root / "inputs" / (MINIROOTFS_NAME + ".asc"),
            root / "inputs" / "ncopa.asc", MINIROOTFS_SIGNER,
        )
        _extract_runtime(archive, runtime)
        resolver = work / "resolver"
        for subdir in ("lib/apk/db", "etc/apk", "var/cache/apk"):
            (resolver / subdir).mkdir(parents=True, mode=0o700)
        (resolver / "lib/apk/db/installed").touch(mode=0o600)
        (resolver / "etc/apk/world").touch(mode=0o600)
        repositories = work / "repositories"
        repositories.write_text("\n".join(REPOSITORIES) + "\n", encoding="utf-8")
        cache = work / "cache"
        cache.mkdir(mode=0o700)
        yield _Apk(runtime, resolver, repositories, cache, bootstrap_signature)


def _artifact(root: Path, path: Path, kind: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(), "sha256": sha256(path),
        "size_bytes": path.stat().st_size, "kind": kind,
    }


def _package_metadata(root: Path, path: Path) -> dict[str, Any]:
    """Read only signed v2 package metadata; never extract payloads or scripts."""
    fields: dict[str, list[str]] = {}
    try:
        with tarfile.open(path, mode="r:gz", ignore_zeros=True) as archive:
            for number, member in enumerate(archive):
                if number > 32:
                    break
                if member.name != ".PKGINFO":
                    continue
                if not member.isfile() or member.size > 1024 * 1024:
                    raise QSXError(f"invalid Alpine package metadata: {path.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise QSXError(f"cannot read Alpine package metadata: {path.name}")
                for line in stream.read().decode("utf-8").splitlines():
                    if " = " in line and not line.startswith("#"):
                        key, value = line.split(" = ", 1)
                        fields.setdefault(key, []).append(value)
                break
    except (tarfile.TarError, UnicodeError) as exc:
        raise QSXError(f"unsupported or corrupt Alpine package metadata: {path.name}") from exc
    for key in ("pkgname", "pkgver", "arch"):
        if len(fields.get(key, [])) != 1:
            raise QSXError(f"missing or ambiguous {key} in {path.name}")
    name, version, architecture = (fields[key][0] for key in ("pkgname", "pkgver", "arch"))
    if path.name != f"{name}-{version}.apk" or architecture not in (ARCHITECTURE, "noarch"):
        raise QSXError(f"Alpine package filename or architecture mismatch: {path.name}")
    return {
        **_artifact(root, path, "package"), "name": name, "version": version,
        "architecture": architecture, "depends": fields.get("depend", []),
        "provides": fields.get("provides", []),
    }


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise QSXError("APK cache lock contains a non-string artifact path")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != relative:
        raise QSXError("APK cache lock contains an unsafe artifact path")
    if len(path.parts) != 2 or path.parts[0] not in _FROZEN_DIRS:
        raise QSXError("APK cache lock contains an unexpected artifact location")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise QSXError(f"APK cache artifacts must not be symlinks: {relative}")
    if not current.is_file():
        raise QSXError(f"missing APK cache artifact: {relative}")
    return current


def _verify_files(root: Path, manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise QSXError("APK cache lock has no artifact list")
    expected: set[str] = set()
    for entry in artifacts:
        if not isinstance(entry, dict):
            raise QSXError("invalid APK cache artifact entry")
        path = _safe_file(root, entry.get("path"))
        relative = path.relative_to(root).as_posix()
        if relative in expected:
            raise QSXError("APK cache lock contains duplicate artifacts")
        expected.add(relative)
        if path.stat().st_size != entry.get("size_bytes") or sha256(path) != entry.get("sha256"):
            raise QSXError(f"APK cache integrity mismatch: {relative}")
    actual: set[str] = set()
    for directory in _FROZEN_DIRS:
        folder = root / directory
        if folder.is_symlink() or not folder.is_dir():
            raise QSXError(f"missing or unsafe APK cache directory: {directory}")
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file():
                raise QSXError(f"unexpected object in APK cache: {directory}/{path.name}")
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise QSXError("APK cache file set differs from its lock (extra or missing input)")


def _read_lock(root: Path, requested: list[str]) -> dict[str, Any]:
    lock = root / "lock.json"
    if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > 8 * 1024 * 1024:
        raise QSXError("APK cache is incomplete or its lock is invalid; use a new destination")
    manifest = json.loads(lock.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise QSXError("unsupported APK cache lock schema")
    if manifest.get("requested_packages") != requested:
        raise QSXError("APK cache belongs to a different package list; use a new destination")
    if (
        manifest.get("alpine_release") != ALPINE_RELEASE
        or manifest.get("architecture") != ARCHITECTURE
        or manifest.get("repositories") != REPOSITORIES
    ):
        raise QSXError("APK cache was frozen with different Alpine inputs")
    bootstrap = manifest.get("minirootfs")
    if not isinstance(bootstrap, dict) or any(
        bootstrap.get(key) != value for key, value in {
            "url": MINIROOTFS_URL, "sha512": MINIROOTFS_SHA512,
            "path": f"inputs/{MINIROOTFS_NAME}", "checksum_url": MINIROOTFS_URL + ".sha512",
            "signature_path": f"inputs/{MINIROOTFS_NAME}.asc",
            "signature_url": MINIROOTFS_URL + ".asc",
            "public_key_path": "inputs/ncopa.asc", "public_key_url": MINIROOTFS_KEY_URL,
            "public_key_fingerprint": MINIROOTFS_SIGNER,
        }.items()
    ):
        raise QSXError("APK cache does not identify the pinned official bootstrap")
    _verify_files(root, manifest)
    actual_bootstrap = _artifact(root, root / "inputs" / MINIROOTFS_NAME, "bootstrap")
    if any(bootstrap.get(key) != value for key, value in actual_bootstrap.items()):
        raise QSXError("APK bootstrap metadata differs from the frozen artifact")
    return manifest


def _verify_packages(root: Path, apk: _Apk, manifest: dict[str, Any]) -> None:
    records = manifest.get("packages")
    if not isinstance(records, list) or not records or not all(isinstance(x, dict) for x in records):
        raise QSXError("APK cache has no valid package records")
    if any(not isinstance(item.get("path"), str) for item in records):
        raise QSXError("APK package records contain invalid paths")
    if manifest["minirootfs"].get("signature_verification") != apk.bootstrap_signature:
        raise QSXError("Alpine bootstrap signature provenance differs from the frozen lock")
    if manifest.get("apk_version") != apk.run("--version"):
        raise QSXError("APK runtime version differs from the frozen lock")
    package_files = sorted((root / "packages").iterdir())
    if len(package_files) != len(records) or {x.get("path") for x in records} != {
        x.relative_to(root).as_posix() for x in package_files
    }:
        raise QSXError("APK package records differ from the frozen package file set")
    indexes = sorted((root / "indexes").iterdir())
    if len(indexes) != len(REPOSITORIES):
        raise QSXError("APK freeze did not preserve both signed repository indexes")
    apk.run("verify", *(str(path) for path in [*indexes, *package_files]))
    observed = [_package_metadata(root, path) for path in package_files]
    if observed != records:
        raise QSXError("APK package metadata differs from the frozen lock")
    resolved = {item["name"]: item["version"] for item in observed}
    if len(resolved) != len(observed):
        raise QSXError("APK cache contains multiple versions of a package")
    for request in manifest["requested_packages"]:
        name, separator, expected_version = request.partition("=")
        if name not in resolved or (separator and resolved[name] != expected_version):
            raise QSXError(f"APK cache lacks its requested package: {request}")
    keys = sorted((apk.runtime / "etc/apk/keys").iterdir())
    if {p.name for p in keys} != {p.name for p in (root / "keys").iterdir()} or any(
        sha256(key) != sha256(root / "keys" / key.name) for key in keys
    ):
        raise QSXError("APK cache public trust keys differ from the pinned bootstrap")


def fetch_packages(
    destination: Path, packages: list[str], offline: bool = False,
) -> dict[str, Any]:
    """Create or verify a frozen APK cache; never refresh an existing cache.

    ``destination`` must be new for a first freeze. A complete cache can be
    reopened in either mode, but a changed request, missing file or failed hash
    is an error. The returned lock uses paths relative to ``destination``.
    Repository resolution requires network only on the initial online call.
    """
    requested = _requests(packages)
    root = Path(destination).absolute()
    if root.is_symlink():
        raise QSXError("APK cache destination must not be a symlink")
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise QSXError("native Alpine APK freeze requires a Linux x86_64 controller")
    try:
        if root.exists():
            if not root.is_dir():
                raise QSXError("APK cache destination is not a directory")
            manifest = _read_lock(root, requested)
            with _runtime(root, root / "inputs" / MINIROOTFS_NAME) as apk:
                _verify_packages(root, apk, manifest)
            return manifest
        if offline:
            raise QSXError("offline APK freeze has no complete cache at this destination")
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        for directory in _FROZEN_DIRS:
            (root / directory).mkdir(mode=0o700)
        archive = root / "inputs" / MINIROOTFS_NAME
        _download_minrootfs(archive)
        _download_bootstrap_signature(root)
        with _runtime(root, archive) as apk:
            version = apk.run("--version")
            # Explicit update is required: fetch -U alone with an empty cache
            # can silently fail in apk-tools 3.0.6 (see _check_apk_result).
            apk.run("--update-cache", "update", network=True)
            apk.run("fetch", "--recursive", "--output", str(root / "packages"),
                    *requested, network=True)
            package_files = sorted((root / "packages").glob("*.apk"))
            if not package_files:
                raise QSXError("APK fetch returned no packages")
            for path in apk.cache.iterdir():
                if path.is_file() and path.name.startswith("APKINDEX."):
                    shutil.copyfile(path, root / "indexes" / path.name)
            for path in (apk.runtime / "etc/apk/keys").iterdir():
                if path.is_file():
                    shutil.copyfile(path, root / "keys" / path.name)
            apk.run("verify", *(str(path) for path in package_files))
            records = [_package_metadata(root, path) for path in package_files]
            resolved = {item["name"]: item["version"] for item in records}
            for request in requested:
                name, separator, expected_version = request.partition("=")
                if name not in resolved or (separator and resolved[name] != expected_version):
                    raise QSXError(f"APK fetch did not resolve the requested package: {request}")
            artifacts = [
                _artifact(root, path, {"inputs": "bootstrap", "packages": "package",
                                      "indexes": "repository_index", "keys": "trust_key"}[directory])
                for directory in _FROZEN_DIRS for path in sorted((root / directory).iterdir())
            ]
            manifest = {
                "schema_version": 1, "alpine_release": ALPINE_RELEASE,
                "architecture": ARCHITECTURE, "requested_packages": requested,
                "repositories": list(REPOSITORIES), "apk_version": version,
                "minirootfs": {
                    **_artifact(root, archive, "bootstrap"), "url": MINIROOTFS_URL,
                    "sha512": MINIROOTFS_SHA512, "checksum_url": MINIROOTFS_URL + ".sha512",
                    "signature_path": f"inputs/{MINIROOTFS_NAME}.asc",
                    "signature_url": MINIROOTFS_URL + ".asc",
                    "public_key_path": "inputs/ncopa.asc", "public_key_url": MINIROOTFS_KEY_URL,
                    "public_key_fingerprint": MINIROOTFS_SIGNER,
                    "signature_verification": apk.bootstrap_signature,
                },
                "packages": records, "artifacts": artifacts,
                "trust": "APK signatures verified using public keys from the SHA-512-pinned minirootfs",
            }
            _verify_files(root, manifest)
            _verify_packages(root, apk, manifest)
        atomic_json(root / "lock.json", manifest)
        return manifest
    except QSXError:
        raise
    except (OSError, ValueError, TypeError, KeyError, tarfile.TarError) as exc:
        raise QSXError(f"cannot freeze or verify Alpine APK cache: {exc}") from exc
