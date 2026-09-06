"""Publisher signature checks for the explicitly supported template inputs.

The Alpine trust anchor is the full fingerprint published on its cloud page.
Codex signs individual ELF binaries, not the distribution package. Cosign owns
the Fulcio/Rekor verification; decoding a certificate is never verification.
Network access belongs to freeze. Offline builds use the locked bundle hashes.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .errors import QSXError

ALPINE_FINGERPRINT = "F26ADFADBAE702EF7AF637459DA7EF23BFFCDF22"
ALPINE_KEY_URL = "https://alpinelinux.org/keys/tomalok.asc"
ALPINE_TRUST_SOURCE = "https://alpinelinux.org/cloud/"
ALPINE_SIGNATURE_URL = (
    "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/cloud/"
    "generic_alpine-3.24.1-x86_64-bios-cloudinit-r0.qcow2.asc"
)
CODEX_RELEASE = "rust-v0.153.4"
CODEX_PACKAGE_SHA256 = "a822187e1a2420c61c5926721bfbd878701ed95547c9bb0d4de4498a16ba1821"
CODEX_RELEASE_URL = f"https://github.com/openai/codex/releases/download/{CODEX_RELEASE}/"
CODEX_IDENTITY = (
    "https://github.com/openai/codex/.github/workflows/rust-release.yml"
    f"@refs/tags/{CODEX_RELEASE}"
)
CODEX_ISSUER = "https://token.actions.githubusercontent.com"
CODEX_SIGNATURE_SHA256 = {
    "codex": "0dc1cab06eade46e05659b84289751a9425f7bb7886c5a10ddc3d6f5735cfaa2",
    "codex-code-mode-host": "a31c6bc2aa530b98986207d67e61061096ba384f623b49ed9992bb628145900f",
    "bwrap": "4e0e55799310ed3655041bc06246b3ab4b7917a04fc821b8b010d73c23fd53f0",
}


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _run(args: list[str], *, timeout: int = 180,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace",
                                timeout=timeout, check=False, env=env)
    except FileNotFoundError as exc:
        raise QSXError(f"BLOCKED: signature verifier unavailable: {args[0]}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QSXError(f"signature verifier could not complete: {args[0]}: {exc}") from exc
    if result.returncode:
        raise QSXError(f"signature verification failed: {(result.stderr or result.stdout)[-2000:]}")
    return result


def verify_gpg_signature(
    artifact: Path, signature: Path, public_key: Path,
    expected_fingerprint: str = ALPINE_FINGERPRINT,
    *, gpg_binary: str = "gpg",
) -> dict[str, Any]:
    """Verify against one pinned primary key, using only a disposable keyring."""
    expected = expected_fingerprint.upper()
    if not re.fullmatch(r"[A-F0-9]{40}|[A-F0-9]{64}", expected):
        raise QSXError("expected GPG fingerprint must be a complete fingerprint")
    paths = [Path(path).resolve(strict=True) for path in (artifact, signature, public_key)]
    with tempfile.TemporaryDirectory(prefix="qsx-provenance-gpg-") as scratch:
        Path(scratch).chmod(0o700)
        common = [gpg_binary, "--no-options", "--homedir", scratch, "--batch",
                  "--no-autostart", "--no-auto-key-retrieve"]
        keyring = Path(scratch) / "publisher.gpg"
        _run([*common, "--dearmor", "--output", str(keyring), str(paths[2])])
        common.extend(["--no-default-keyring", "--keyring", str(keyring)])
        listing = _run([*common, "--with-colons", "--fingerprint", "--list-keys"])
        primary_fingerprints: list[str] = []
        primary_pending = False
        for line in listing.stdout.splitlines():
            fields = line.split(":")
            if fields[0] == "pub":
                primary_pending = True
            elif fields[0] == "sub":
                primary_pending = False
            elif fields[0] == "fpr" and primary_pending:
                primary_fingerprints.append(fields[9])
                primary_pending = False
        if primary_fingerprints != [expected]:
            raise QSXError("publisher GPG key does not match the pinned primary fingerprint")
        result = _run([*common, "--status-fd", "1", "--verify", str(paths[1]), str(paths[0])])
        valid = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
                # Field 2 is the signing key; field 11, if present, is its primary key.
                primary = fields[11] if len(fields) > 11 else fields[2]
                if primary == expected:
                    valid.append(fields[2])
        if len(valid) != 1:
            raise QSXError("GPG did not confirm exactly one valid signature from the pinned key")
    return {"status": "VERIFIED", "scheme": "OpenPGP", "primary_fingerprint": expected,
            "signing_fingerprint": valid[0], "artifact_sha256": _sha256(paths[0])}


def verify_sigstore_blob(
    artifact: Path, bundle: Path, *, cosign_binary: str = "cosign",
) -> dict[str, Any]:
    """Verify the legacy Codex bundle online with a maintained cosign version."""
    # Inherited SIGSTORE_* / TUF_* overrides could replace publisher trust.
    # Pass transport settings only; TUF uses a private disposable cache.
    transport = ("PATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY",
                 "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy")
    environment = {key: os.environ[key] for key in transport if key in os.environ}
    with tempfile.TemporaryDirectory(prefix="qsx-provenance-tuf-") as scratch:
        environment["TUF_ROOT"] = str(Path(scratch) / "tuf")
        version_output = _run([cosign_binary, "version"], env=environment).stdout
        match = re.search(r"GitVersion:\s*v?(\d+)\.(\d+)\.(\d+)([^\s]*)", version_output)
        if not match or match[4] or tuple(map(int, match.group(1, 2, 3))) < (3, 1, 3):
            raise QSXError("BLOCKED: cosign >= 3.1.3 stable is required for legacy Sigstore bundles")
        result = _run([
            cosign_binary, "verify-blob", "--new-bundle-format=false",
            "--bundle", str(Path(bundle).resolve(strict=True)),
            "--certificate-identity", CODEX_IDENTITY,
            "--certificate-oidc-issuer", CODEX_ISSUER,
            str(Path(artifact).resolve(strict=True)),
        ], env=environment)
    return {"status": "VERIFIED", "scheme": "Sigstore", "identity": CODEX_IDENTITY,
            "issuer": CODEX_ISSUER, "cosign_version": ".".join(match.group(1, 2, 3)),
            "artifact_sha256": _sha256(Path(artifact)), "verifier_returncode": result.returncode}


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        if not newurl.startswith("https://"):
            raise QSXError("signature download refused a non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _proof_file(url: str, target: Path, offline: bool, digest: str | None = None) -> Path:
    if target.is_symlink():
        raise QSXError("signature cache may not contain symlinks")
    if not target.exists():
        if offline:
            raise QSXError(f"BLOCKED: cached publisher proof missing: {target.name}")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "QSX-provenance/1"})
            with urllib.request.build_opener(_HTTPSRedirect()).open(request, timeout=60) as response:
                data = response.read(512 * 1024 + 1)
            if len(data) > 512 * 1024:
                raise QSXError("publisher proof exceeds 512 KiB")
            with target.open("xb") as output:
                output.write(data)
        except (OSError, ValueError) as exc:
            raise QSXError(f"publisher proof download failed: {url}: {exc}") from exc
    if not target.is_file() or target.stat().st_size > 512 * 1024:
        raise QSXError("invalid publisher proof cache file")
    if digest is not None and _sha256(target) != digest:
        raise QSXError(f"publisher proof digest mismatch: {target.name}")
    return target


def verify_inputs(
    base_path: Path, codex_path: Path | None, destination: Path, offline: bool = False,
) -> dict[str, Any]:
    """Verify fixed supported releases and retain proofs under destination/provenance.

    With Codex enabled this is an online freeze operation. Cosign's legacy
    --offline flag can still contact TUF, so we refuse it rather than claiming
    an offline verification. Offline build must validate its frozen manifest.
    """
    if codex_path is not None and offline:
        raise QSXError("BLOCKED: Codex publisher verification belongs to online freeze; "
                       "offline build must validate the retained bundle hashes")
    if codex_path is not None and _sha256(Path(codex_path)) != CODEX_PACKAGE_SHA256:
        raise QSXError("Codex package does not match the supported pinned release")
    destination = Path(destination).resolve(strict=True)
    proofs = destination / "provenance"
    if proofs.is_symlink():
        raise QSXError("provenance directory may not be a symlink")
    proofs.mkdir(mode=0o700, exist_ok=True)
    key = _proof_file(ALPINE_KEY_URL, proofs / "alpine-cloud-key.asc", offline)
    signature = _proof_file(ALPINE_SIGNATURE_URL, proofs / "alpine-cloud-image.asc", offline)
    alpine = verify_gpg_signature(Path(base_path), signature, key)
    alpine.update({"key_url": ALPINE_KEY_URL, "trust_source": ALPINE_TRUST_SOURCE,
                   "signature_url": ALPINE_SIGNATURE_URL})
    codex: dict[str, Any] = {"enabled": False}
    if codex_path is not None:
        signed_components: list[dict[str, Any]] = []
        with tarfile.open(codex_path, "r:gz") as archive:
            members = archive.getmembers()
            with tempfile.TemporaryDirectory(prefix="qsx-provenance-elf-") as scratch:
                for binary, digest in CODEX_SIGNATURE_SHA256.items():
                    candidates = [m for m in members if Path(m.name).name == binary]
                    if len(candidates) != 1 or not candidates[0].isfile():
                        raise QSXError(f"Codex package must contain one regular {binary} binary")
                    member = candidates[0]
                    if member.size > 512 * 1024 * 1024:
                        raise QSXError("Codex component exceeds 512 MiB")
                    raw = Path(scratch) / binary
                    source = archive.extractfile(member)
                    if source is None:
                        raise QSXError("cannot read Codex component")
                    with source, raw.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    name = f"{binary}-x86_64-unknown-linux-musl.sigstore"
                    bundle = _proof_file(CODEX_RELEASE_URL + name, proofs / name, False, digest)
                    verified = verify_sigstore_blob(raw, bundle)
                    verified["package_member"] = member.name
                    verified["bundle_url"] = CODEX_RELEASE_URL + name
                    signed_components.append(verified)
        codex = {"enabled": True, "release": CODEX_RELEASE,
                 "package_sha256": CODEX_PACKAGE_SHA256,
                 "package_signature": "NOT_PUBLISHED", "signed_components": signed_components,
                 "scope": "only the listed raw ELF components have verified publisher signatures; "
                          "the complete package is pinned by release SHA-256"}
    artifacts = [{"path": str(path.relative_to(destination)), "sha256": _sha256(path),
                  "size_bytes": path.stat().st_size}
                 for path in sorted(proofs.iterdir()) if path.is_file()]
    return {"schema_version": 1, "status": "VERIFIED", "alpine": alpine, "codex": codex,
            "artifacts": artifacts,
            "offline_build_policy": "verify frozen bundle integrity; no online verifier calls"}
