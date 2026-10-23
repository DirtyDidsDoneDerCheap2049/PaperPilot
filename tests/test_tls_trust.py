"""Security regression checks for the Python trust store used by HTTP clients."""
import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from pathlib import Path

from scripts import check_tls_trust


def test_active_environment_uses_patched_certifi():
    report = check_tls_trust.check_environment()
    assert report["bundle"]["roots"] > 0


@pytest.mark.parametrize("version", ["2026.7.22", "2026.10.18", "unknown"])
def test_old_or_unknown_certifi_cannot_pass_preflight(monkeypatch, version):
    monkeypatch.setattr(check_tls_trust.metadata, "version", lambda name: version)
    with pytest.raises(RuntimeError, match="security baseline"):
        check_tls_trust.check_environment()


def test_old_pip_cannot_pass_preflight(monkeypatch):
    original = check_tls_trust.metadata.version
    monkeypatch.setattr(check_tls_trust.metadata, "version",
                        lambda name: '25.0.1' if name == 'pip' else original(name))
    with pytest.raises(RuntimeError, match='26.2.2 security baseline'):
        check_tls_trust.check_environment()


def test_filter_preserves_other_roots_comments_and_is_idempotent(tmp_path, monkeypatch):
    certs = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())[:2]
    blocked, kept = certs
    prefix = b'# Original CA bundle notice\n'
    data = prefix + b''.join(cert.public_bytes(serialization.Encoding.PEM) for cert in certs)
    monkeypatch.setattr(check_tls_trust, 'BLOCKED_ROOTS', {
        blocked.fingerprint(hashes.SHA256()).hex(): 'Test distrusted CA',
    })
    filtered = check_tls_trust.filter_distrusted_roots(data)
    assert filtered.startswith(prefix)
    assert kept.public_bytes(serialization.Encoding.PEM) in filtered
    assert blocked.public_bytes(serialization.Encoding.PEM) not in filtered
    assert check_tls_trust.filter_distrusted_roots(filtered) == filtered
    path = tmp_path/'filtered.pem'
    path.write_bytes(filtered)
    assert check_tls_trust.check_bundle(path)['roots'] == 1


def test_bundle_content_is_checked_independently_of_version(tmp_path, monkeypatch):
    cert = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())[0]
    pem = tmp_path / "distrusted.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # Block a real CA by fingerprint to exercise the rejection path without
    # committing an obsolete trust store or relying on certificate comments.
    monkeypatch.setattr(check_tls_trust, "BLOCKED_ROOTS", {
        cert.fingerprint(hashes.SHA256()).hex(): "Test distrusted CA",
    })
    with pytest.raises(RuntimeError, match="Test distrusted CA"):
        check_tls_trust.check_bundle(pem)


def test_invalid_bundle_is_not_silently_accepted(tmp_path):
    pem = tmp_path / "invalid.pem"
    pem.write_text("not a certificate", encoding="ascii")
    with pytest.raises(ValueError):
        check_tls_trust.check_bundle(pem)
