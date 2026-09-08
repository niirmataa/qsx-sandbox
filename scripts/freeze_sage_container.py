#!/usr/bin/env python3
"""Freeze publisher Sage OCI blobs without a host container daemon or extraction.

The recipe records exact authenticated registry URLs. This helper obtains only a
short-lived anonymous pull token, fills the standard artifact cache, and writes a
deterministic OCI + Docker-compatible archive for independent preservation.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import io
import json
import re
import shutil
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REGISTRY = "https://registry-1.docker.io/v2/sagemath/sagemath/"
TOKEN_URL = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:sagemath/sagemath:pull"
IMAGE_DIGEST = "fb96c7fb10d672f3be0d088399db3aeac52650416d1bb71f74f26429ea35778f"
IMAGE_TAG = "qsx/sagemath:10.8-fb96c7fb10d6"


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("refusing redirect away from HTTPS")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def registry_open(url: str, byte_range: str | None = None):
    """Refresh anonymous credentials per request and once more after HTTP 401."""
    opener = urllib.request.build_opener(SecureRedirect())
    for attempt in range(2):
        with opener.open(TOKEN_URL, timeout=120) as response:
            token = json.load(response)["token"]
        headers = {"Authorization": "Bearer " + token,
                   "Accept": "application/vnd.oci.image.manifest.v1+json",
                   "User-Agent": "QSX-Sage-input-freeze/1"}
        if byte_range is not None:
            headers["Range"] = byte_range
        try:
            return opener.open(urllib.request.Request(url, headers=headers), timeout=120)
        except urllib.error.HTTPError as error:
            if error.code != 401 or attempt:
                raise
    raise AssertionError("unreachable anonymous token retry")


def download_ranges(url: str, target: Path, expected: str, size: int,
                    transfers: Path, connections: int, chunk_size: int = 16 * 1024 * 1024) -> dict:
    """Resume complete checked chunks; retain failed partial bytes and evidence."""
    directory = transfers / expected
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        if target.exists():
            if target.is_symlink() or digest(target) != expected:
                raise ValueError("invalid cached input")
            return {"source": "verified_cache", "downloaded_bytes": 0}
        spans = [(start, min(start + chunk_size - 1, size - 1))
                 for start in range(0, size, chunk_size)]

        def fetch(span):
            start, end = span
            part = directory / f"{start:012}-{end:012}.part"
            record = part.with_suffix(".json")
            wanted = end - start + 1
            if part.exists() or record.exists():
                if (part.is_symlink() or record.is_symlink() or not part.is_file()
                        or part.stat().st_size != wanted
                        or (record.exists() and (not record.is_file()
                            or json.loads(record.read_text())["sha256"] != digest(part)))):
                    raise ValueError("invalid preserved download chunk: " + part.name)
                # A complete part may precede its atomic journal after interruption.
                # The mandatory final publisher digest still verifies all its bytes.
                return part, 0
            temporary = part.with_name(part.name + ".download-" + uuid.uuid4().hex)
            with registry_open(url, f"bytes={start}-{end}") as response:
                if (response.status != 206 or response.headers.get("Content-Range")
                        != f"bytes {start}-{end}/{size}"):
                    raise ValueError("registry did not honor the exact byte range")
                with temporary.open("xb") as stream:
                    shutil.copyfileobj(response, stream, 1024 * 1024)
            if temporary.stat().st_size != wanted:
                raise ValueError("truncated registry byte range")
            metadata = {"start": start, "end": end, "size_bytes": wanted,
                        "sha256": digest(temporary)}
            temporary.rename(part)
            journal = record.with_name(record.name + ".download-" + uuid.uuid4().hex)
            journal.write_text(json.dumps(metadata) + "\n")
            journal.rename(record)
            print("verified range " + part.name, flush=True)
            return part, wanted

        with concurrent.futures.ThreadPoolExecutor(max_workers=connections) as pool:
            pieces = list(pool.map(fetch, spans))
        assembled = directory / ("assembled-" + uuid.uuid4().hex + ".artifact")
        with assembled.open("xb") as stream:
            for part, _ in pieces:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, stream, 1024 * 1024)
        if assembled.stat().st_size != size or digest(assembled) != expected:
            raise ValueError("assembled registry input SHA-256 mismatch")
        assembled.rename(target)
        return {"source": "verified_ranges", "downloaded_bytes": sum(n for _, n in pieces),
                "reused_bytes": size - sum(n for _, n in pieces), "connections": connections}


def archive_metadata(manifest: dict, manifest_size: int) -> dict[str, bytes]:
    def packed(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    return {
        "oci-layout": packed({"imageLayoutVersion": "1.0.0"}),
        "index.json": packed({"schemaVersion": 2, "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + IMAGE_DIGEST, "size": manifest_size,
            "platform": {"os": "linux", "architecture": "amd64"},
            "annotations": {"org.opencontainers.image.ref.name": IMAGE_TAG},
        }]}),
        "manifest.json": packed([{
            "Config": "blobs/sha256/" + manifest["config"]["digest"].split(":")[1],
            "RepoTags": [IMAGE_TAG],
            "Layers": ["blobs/sha256/" + layer["digest"].split(":")[1]
                       for layer in manifest["layers"]],
        }]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", type=Path, default=Path("templates/sage-lean4-container.toml"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="new evidence directory, outside the source repository")
    parser.add_argument("--connections", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    started = time.monotonic()
    recipe = tomllib.loads(args.recipe.read_text())
    artifacts = [item for item in recipe["artifacts"] if item["url"].startswith(REGISTRY)]
    if not artifacts:
        raise ValueError("recipe has no pinned Sage registry artifacts")
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    sizes = {}
    artifacts.sort(key=lambda item: item["sha256"] != IMAGE_DIGEST)
    for item in artifacts:
        expected = item["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("invalid SHA-256")
        expected_url = REGISTRY + ("manifests/sha256:" if expected == IMAGE_DIGEST
                                   else "blobs/sha256:") + expected
        if item["url"] != expected_url:
            raise ValueError("unexpected registry URL")
        target = args.cache / "blobs" / (expected + ".artifact")
        target.parent.mkdir(parents=True, exist_ok=True)
        acquisition = {"source": "verified_cache", "downloaded_bytes": 0}
        if not target.exists():
            print("download " + item["name"], flush=True)
            size = sizes.get(expected, 0)
            if size >= 32 * 1024 * 1024:
                acquisition = download_ranges(item["url"], target, expected, size,
                                              args.cache / "transfers", args.connections)
            else:
                temporary = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
                # Failed partial files remain diagnostic evidence, never cache hits.
                with registry_open(item["url"]) as response, temporary.open("xb") as stream:
                    shutil.copyfileobj(response, stream, 1024 * 1024)
                if digest(temporary) != expected:
                    raise ValueError("registry input digest mismatch: " + item["name"])
                temporary.rename(target)
                acquisition = {"source": "fresh_stream", "downloaded_bytes": target.stat().st_size}
        if target.is_symlink() or not target.is_file() or digest(target) != expected:
            raise ValueError("invalid existing cached input: " + str(target))
        if expected == IMAGE_DIGEST:
            manifest = json.loads(target.read_bytes())
            sizes = {item["digest"].split(":")[1]: item["size"]
                     for item in [manifest["config"], *manifest["layers"]]}
        elif expected not in sizes or target.stat().st_size != sizes[expected]:
            raise ValueError("recipe blob does not match pinned OCI descriptor")
        records.append({**item, "size_bytes": target.stat().st_size, "acquisition": acquisition})
        print("verified " + item["name"], flush=True)
    manifest_path = args.cache / "blobs" / (IMAGE_DIGEST + ".artifact")
    manifest = json.loads(manifest_path.read_bytes())
    referenced = {IMAGE_DIGEST, manifest["config"]["digest"].split(":")[1]}
    referenced.update(layer["digest"].split(":")[1] for layer in manifest["layers"])
    if referenced != {item["sha256"] for item in artifacts}:
        raise ValueError("recipe inputs do not exactly match the pinned image manifest")
    archive = args.output / "sagemath-10.8-linux-amd64.oci.tar"
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as output:
        for name, content in archive_metadata(manifest, manifest_path.stat().st_size).items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            output.addfile(member, io.BytesIO(content))
        for expected in sorted(referenced):
            path = args.cache / "blobs" / (expected + ".artifact")
            member = tarfile.TarInfo("blobs/sha256/" + expected)
            member.size = path.stat().st_size
            member.mode = 0o644
            with path.open("rb") as stream:
                output.addfile(member, stream)
    report = {"status": "PASS", "check": "publisher_oci_input_freeze", "image": "sagemath/sagemath@sha256:" + IMAGE_DIGEST,
              "artifact_records": records, "archive": archive.name,
              "archive_sha256": digest(archive), "archive_size_bytes": archive.stat().st_size,
              "duration_seconds": round(time.monotonic() - started, 3),
              "verification": "HTTPS publisher registry + pinned manifest/config/layer SHA-256; "
                              "no independent publisher signature was verified",
              "runtime_validation": "NOT_RUN"}
    (args.output / "FREEZE.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "SHA256SUMS").write_text(report["archive_sha256"] + "  " + archive.name + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
