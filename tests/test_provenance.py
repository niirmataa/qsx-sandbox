"""Actual OpenPGP verification plus explicit unavailable-verifier refusals."""
import shutil
from pathlib import Path

import pytest

from qsx_alpine.errors import QSXError
from qsx_alpine.provenance import verify_gpg_signature, verify_inputs, verify_sigstore_blob

# Public, synthetic fixture created with Sequoia sq, without a user keyring.
# The private key was discarded. A signing subkey exercises primary-key binding.
PUBLIC_KEY = """-----BEGIN PGP PUBLIC KEY BLOCK-----

xjMEap27ZBYJKwYBBAHaRw8BAQdA6ZYnkRvaq+UQOXO+tUMH+abp8kCFMZaP8W5I
zJ3KhoPCwAsEHxYKAH0Fgmqdu2QDCwkHCRCjGMY9JsS5kEcUAAAAAAAeACBzYWx0
QG5vdGF0aW9ucy5zZXF1b2lhLXBncC5vcmcRq0rDfTOmyrI+jVQg6DmZkX3JgJxF
pCbxlbNZT4f+sgMVCggCmwECHgEWIQR7IFgU6eiuplOV/aWjGMY9JsS5kAAAOAoB
AO+KMGrlrOKs6AFNdat0qXliqVPMcLsuhkZOepDFX+KoAQD1gCsGg52sK3uISgL1
pK/uIQpa9zQQx4mxS//6YGpRBM0dUVNYIFRlc3QgPHRlc3RAbG9jYWwuaW52YWxp
ZD7CwA4EExYKAIAFgmqdu2QDCwkHCRCjGMY9JsS5kEcUAAAAAAAeACBzYWx0QG5v
dGF0aW9ucy5zZXF1b2lhLXBncC5vcmehXEo275HPBE41HX+0nisM60nR7asA/Oxn
pA1Snpem0QMVCggCmQECmwECHgEWIQR7IFgU6eiuplOV/aWjGMY9JsS5kAAA6jYA
/1FyBx1OdzYhM8oIk20c9GMADj2l/gJ2PXNWF3swqMBWAQCwpzli9GBHgf/zYKPM
6gPBe8bXyoM2+PZVpmUQJtmfBc4zBGqdu2QWCSsGAQQB2kcPAQEHQB3ntCL30GIv
3t5FyRzmIUyqo6KXGMG1hixltQqAEQ+DwsC/BBgWCgExBYJqnbtkCRCjGMY9JsS5
kEcUAAAAAAAeACBzYWx0QG5vdGF0aW9ucy5zZXF1b2lhLXBncC5vcmfwyEU9OQ3v
nt9AKBqaFPq8DmHGBz6KlWy7620r3ZOtLAKbAr6gBBkWCgBvBYJqnbtkCRBgYVSj
xXKy8kcUAAAAAAAeACBzYWx0QG5vdGF0aW9ucy5zZXF1b2lhLXBncC5vcmdd/fHQ
2esi8bzl5juvitJtCa//9kybRddnfiBRUcSobRYhBDQFvN/+fNX5SZCyZGBhVKPF
crLyAAB0fwEAgz6mCKqGaQP42tma7+3aw1tTEoW+lSOsFQvxC8oJyKMA+weXb0/Q
SRzCjsO8nnVPpIjKaEXlMZ81xQPTG0YdO+4NFiEEeyBYFOnorqZTlf2loxjGPSbE
uZAAAKfFAQC06v3fpiDKzRMXsmGhAxsEMP34ZvLTCBvv5/Wv+RFNygD9GQ1p+v4b
XMVIEBqQv8vh1kQl8o7MNxLjniTttOhe8Ao=
=j875
-----END PGP PUBLIC KEY BLOCK-----
"""
SIGNATURE = """-----BEGIN PGP SIGNATURE-----

wr0EABYKAG8Fgmqdu2QJEGBhVKPFcrLyRxQAAAAAAB4AIHNhbHRAbm90YXRpb25z
LnNlcXVvaWEtcGdwLm9yZzGProLxNR7Kro6/22HF+nmJ4Xee38jplF2I7hegFkwL
FiEENAW83/581flJkLJkYGFUo8VysvIAAF24AQDaf1App8DGtTuyGKQiNKV9iR4X
UwnSP5OKbVNX+LV5QwD/fgLBL/tcqUZ6x5tVg+xAFUEGw3yg2ox1pN1eOanwVgs=
=0hWz
-----END PGP SIGNATURE-----
"""


@pytest.fixture
def signed_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    if shutil.which("gpg") is None:
        pytest.skip("BLOCKED: actual GPG integration requires gpg")
    fingerprint = "7B205814E9E8AEA65395FDA5A318C63D26C4B990"
    key = tmp_path / "public.asc"
    key.write_text(PUBLIC_KEY)
    artifact = tmp_path / "synthetic-image"
    artifact.write_bytes(b"QSX synthetic image fixture\n")
    signature = tmp_path / "image.asc"
    signature.write_text(SIGNATURE)
    return artifact, signature, key, fingerprint


def test_real_signature_accepts_only_pinned_key(signed_fixture: tuple) -> None:
    artifact, signature, key, fingerprint = signed_fixture
    result = verify_gpg_signature(artifact, signature, key, fingerprint)
    assert result["status"] == "VERIFIED"
    assert result["primary_fingerprint"] == fingerprint
    with pytest.raises(QSXError, match="pinned primary fingerprint"):
        verify_gpg_signature(artifact, signature, key, "0" * 40)


def test_real_signature_rejects_modified_payload(signed_fixture: tuple) -> None:
    artifact, signature, key, fingerprint = signed_fixture
    artifact.write_bytes(b"changed synthetic fixture\n")
    with pytest.raises(QSXError, match="signature verification failed"):
        verify_gpg_signature(artifact, signature, key, fingerprint)


def test_missing_verifier_is_blocked(signed_fixture: tuple, tmp_path: Path) -> None:
    artifact, signature, key, fingerprint = signed_fixture
    with pytest.raises(QSXError, match="BLOCKED: signature verifier unavailable"):
        verify_gpg_signature(artifact, signature, key, fingerprint,
                             gpg_binary=str(tmp_path / "no-such-verifier"))
    with pytest.raises(QSXError, match="BLOCKED: signature verifier unavailable"):
        verify_sigstore_blob(artifact, signature,
                             cosign_binary=str(tmp_path / "no-such-verifier"))


def test_offline_codex_refuses_before_any_input_read(tmp_path: Path) -> None:
    with pytest.raises(QSXError, match="belongs to online freeze"):
        verify_inputs(tmp_path / "absent-base", tmp_path / "absent-codex", tmp_path,
                      offline=True)
