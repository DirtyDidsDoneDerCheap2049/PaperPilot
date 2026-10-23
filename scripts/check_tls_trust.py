"""Check the certifi security baseline and the actual packaged trust store.

See GHSA-6q48-6c7w-9gw2. This checks Python's certifi bundle, not the
Windows or WebView2 certificate store, and never changes either store.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes

MIN_CERTIFI = (2026, 10, 19)
MIN_PIP = (26, 2, 2)
DESKTOP_BUNDLES = ('_internal/certifi/cacert.pem',
                   '_internal/grpc/_cython/_credentials/roots.pem')
BLOCKED_ROOTS = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b":
        "emSign ECC Root CA - G3",
}


def check_bundle(path):
    path = Path(path)
    data = path.read_bytes()
    # Inspect the artifact itself. An SSLContext may contain additional roots
    # injected by the execution environment before this file is loaded.
    certificates = x509.load_pem_x509_certificates(data)
    if not certificates:
        raise RuntimeError(f"Empty CA bundle: {path}")
    fingerprints = {cert.fingerprint(hashes.SHA256()).hex() for cert in certificates}
    blocked = sorted(BLOCKED_ROOTS[value] for value in fingerprints & BLOCKED_ROOTS.keys())
    if blocked:
        raise RuntimeError(f"Distrusted root in {path}: {', '.join(blocked)}")
    return {"path": str(path), "roots": len(certificates),
            "sha256": hashlib.sha256(data).hexdigest()}


def check_environment():
    version = metadata.version("certifi")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_CERTIFI:
        raise RuntimeError(f"certifi {version} predates the 2026.10.19 security baseline")
    import certifi
    pip_version = metadata.version('pip')
    pip_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", pip_version)
    if not pip_match or tuple(map(int, pip_match.groups())) < MIN_PIP:
        raise RuntimeError(f"pip {pip_version} predates the 26.2.2 security baseline")
    from pip._vendor import certifi as pip_certifi
    return {"certifi_version": version, "bundle": check_bundle(certifi.where()),
            "pip_version": pip_version, "pip_bundle": check_bundle(pip_certifi.where())}


def filter_distrusted_roots(data):
    """Remove only blocklisted PEM certificates; preserve all other bytes."""
    if not x509.load_pem_x509_certificates(data):
        raise RuntimeError('Empty CA bundle')

    def keep(match):
        cert = x509.load_pem_x509_certificate(match.group())
        return b'' if cert.fingerprint(hashes.SHA256()).hex() in BLOCKED_ROOTS else match.group()

    return re.sub(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
                  keep, data, flags=re.S)


def check_desktop(folder):
    folder = Path(folder)
    # certifi must be present; gRPC's independent store is checked when bundled.
    paths = [folder / DESKTOP_BUNDLES[0]]
    paths.extend(folder / relative for relative in DESKTOP_BUNDLES[1:]
                 if (folder / relative).exists())
    return [check_bundle(path) for path in paths]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, action="append", default=[],
                        help="Check an explicit PEM bundle instead of the active environment")
    parser.add_argument("--desktop", type=Path,
                        help="Check _internal/certifi/cacert.pem in a desktop folder")
    args = parser.parse_args()
    bundles = list(args.bundle)
    if args.desktop or bundles:
        reports = [check_bundle(path) for path in bundles]
        if args.desktop:
            reports.extend(check_desktop(args.desktop))
        result = {"bundles": reports}
    else:
        result = check_environment()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
