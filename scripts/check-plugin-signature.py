#!/usr/bin/env python3
"""Verify one JetBrains-signed ZIP without rebuilding or re-signing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
SIGNING_MAGIC = b"@PK Sig Block 42"
SIGNER_SHA256 = "2958a0f42221d6062b50f22754e413ae5cc42f60c441467202c6700df2e22f44"
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\r?\n?"
)


class SignatureError(ValueError):
    pass


class DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class CertificateDetails:
    common_name: str | None
    fingerprint: str | None
    error: str | None


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKey(key)
        value[key] = item
    return value


def read_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            value = stream.read(maximum + 1)
    except OSError as error:
        raise SignatureError(f"{label} cannot be read") from error
    if not value or len(value) > maximum:
        raise SignatureError(f"{label} violates the {maximum}-byte size bound")
    return value


def file_hash(path: Path, maximum: int, label: str) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while block := stream.read(64 * 1024):
                total += len(block)
                if total > maximum:
                    raise SignatureError(f"{label} violates the {maximum}-byte size bound")
                digest.update(block)
    except OSError as error:
        raise SignatureError(f"{label} cannot be read") from error
    if total == 0:
        raise SignatureError(f"{label} is empty")
    return digest.hexdigest()


def read_identity(path: Path) -> dict:
    try:
        value = json.loads(
            read_bytes(path, MAX_CONFIG_BYTES, "signing identity").decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, json.JSONDecodeError, DuplicateKey) as error:
        raise SignatureError("signing identity is not strict JSON") from error
    expected = {"schemaVersion", "provider", "timestampPolicy", "certificate"}
    if type(value) is not dict or set(value) != expected:
        raise SignatureError("signing identity has an unknown field or missing field")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise SignatureError("signing identity schemaVersion must be integer 1")
    if value["provider"] != "JetBrains Marketplace ZIP Signer":
        raise SignatureError("signing identity provider is not JetBrains")
    if value["timestampPolicy"] != "unsupported-by-format":
        raise SignatureError("JetBrains ZIP signatures do not support a timestamp policy")
    certificate = value["certificate"]
    if type(certificate) is not dict or set(certificate) != {"status", "commonName", "sha256Fingerprint"}:
        raise SignatureError("certificate identity has an unknown field or missing field")
    if certificate["status"] not in {"active", "pending_activation"}:
        raise SignatureError("certificate identity status is invalid")
    if type(certificate["commonName"]) is not str or not certificate["commonName"].strip():
        raise SignatureError("certificate common name is invalid")
    fingerprint = certificate["sha256Fingerprint"]
    if certificate["status"] == "pending_activation":
        if fingerprint is not None:
            raise SignatureError("pending activation certificate must not claim a fingerprint")
        raise SignatureError("certificate identity is pending activation")
    if type(fingerprint) is not str or FINGERPRINT.fullmatch(fingerprint) is None:
        raise SignatureError("certificate fingerprint is invalid")
    return value


def certificate_pems(path: Path) -> tuple[str, ...]:
    text = read_bytes(path, MAX_CONFIG_BYTES, "certificate chain").decode("ascii", errors="strict")
    matches = tuple(match.group(0).replace("\r\n", "\n").rstrip() + "\n" for match in PEM_CERTIFICATE.finditer(text))
    if not matches:
        raise SignatureError("certificate chain has no PEM certificate")
    if PEM_CERTIFICATE.sub("", text).strip():
        raise SignatureError("certificate chain has data outside PEM certificates")
    return matches


# _ssl._test_decode_cert parses an X.509 PEM without adding a runtime dependency.
# noinspection PyProtectedMember,PyUnresolvedReferences
def decode_certificate(pem: str) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="perf-sentinel-certificate-") as directory:
            certificate = Path(directory) / "certificate.pem"
            certificate.write_text(pem, encoding="ascii")
            return ssl._ssl._test_decode_cert(str(certificate))
    except (OSError, UnicodeError, ValueError, ssl.SSLError) as error:
        raise SignatureError("certificate cannot be decoded") from error


def certificate_details(path: Path, now: datetime | None = None) -> CertificateDetails:
    try:
        pems = certificate_pems(path)
        der = ssl.PEM_cert_to_DER_cert(pems[0])
        decoded_certificates = tuple(decode_certificate(pem) for pem in pems)
        decoded = decoded_certificates[0]
        names = {
            key: value
            for relative_name in decoded.get("subject", ())
            for key, value in relative_name
        }
        common_name = names.get("commonName")
        current = now or datetime.now(UTC)
        for index, certificate in enumerate(decoded_certificates, start=1):
            not_before = datetime.strptime(certificate["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
            not_after = datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
            if current < not_before:
                return CertificateDetails(common_name, None, f"certificate chain member {index} is not yet valid")
            if current > not_after:
                return CertificateDetails(common_name, None, f"certificate chain member {index} is expired")
        return CertificateDetails(common_name, hashlib.sha256(der).hexdigest(), None)
    except (KeyError, OSError, UnicodeError, ValueError, ssl.SSLError, SignatureError) as error:
        return CertificateDetails(None, None, f"certificate cannot be validated: {error}")


def eocd_offset(value: bytes) -> int:
    start = max(0, len(value) - (65535 + 22))
    offset = value.rfind(b"PK\x05\x06", start)
    if offset < 0 or offset + 22 > len(value):
        raise SignatureError("ZIP end record is missing")
    comment_length = struct.unpack_from("<H", value, offset + 20)[0]
    if offset + 22 + comment_length != len(value):
        raise SignatureError("ZIP end record is not canonical")
    return offset


def validate_signature_block(stream, start: int, end: int) -> None:
    length = end - start
    if length < 32:
        raise SignatureError("JetBrains signature block is missing")
    stream.seek(start)
    first_size = stream.read(8)
    stream.seek(end - 24)
    last_size_and_magic = stream.read(24)
    if len(first_size) != 8 or len(last_size_and_magic) != 24 or not last_size_and_magic.endswith(SIGNING_MAGIC):
        raise SignatureError("JetBrains signature block is missing")
    expected_size = length - 8
    if struct.unpack("<Q", first_size)[0] != expected_size or struct.unpack_from("<Q", last_size_and_magic)[0] != expected_size:
        raise SignatureError("JetBrains signature block size is invalid")


def compare_payload(unsigned_path: Path, signed_path: Path) -> None:
    for path, label in ((unsigned_path, "unsigned ZIP"), (signed_path, "signed ZIP")):
        try:
            size = path.stat().st_size
        except OSError as error:
            raise SignatureError(f"{label} cannot be read") from error
        if size <= 0 or size > MAX_ARCHIVE_BYTES:
            raise SignatureError(f"{label} violates the {MAX_ARCHIVE_BYTES}-byte size bound")
    try:
        with zipfile.ZipFile(unsigned_path) as unsigned, zipfile.ZipFile(signed_path) as signed:
            unsigned_start = unsigned.start_dir
            signed_start = signed.start_dir
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as error:
        if isinstance(error, SignatureError):
            raise
        raise SignatureError("plugin ZIP is malformed") from error

    with unsigned_path.open("rb") as unsigned_stream, signed_path.open("rb") as signed_stream:
        remaining = unsigned_start
        while remaining:
            size = min(64 * 1024, remaining)
            if unsigned_stream.read(size) != signed_stream.read(size):
                raise SignatureError("signed ZIP payload differs before the signature block")
            remaining -= size
        validate_signature_block(signed_stream, unsigned_start, signed_start)

    with unsigned_path.open("rb") as stream:
        stream.seek(max(0, unsigned_path.stat().st_size - (65535 + 22)))
        unsigned_tail = stream.read()
    with signed_path.open("rb") as stream:
        stream.seek(max(0, signed_path.stat().st_size - (65535 + 22)))
        signed_tail = stream.read()
    unsigned_eocd_tail = eocd_offset(unsigned_tail)
    signed_eocd_tail = eocd_offset(signed_tail)
    unsigned_declared_start = struct.unpack_from("<I", unsigned_tail, unsigned_eocd_tail + 16)[0]
    signed_declared_start = struct.unpack_from("<I", signed_tail, signed_eocd_tail + 16)[0]
    if unsigned_declared_start != unsigned_start:
        raise SignatureError("unsigned ZIP central directory offset is inconsistent")
    if signed_declared_start != signed_start:
        raise SignatureError("signed ZIP central directory offset is inconsistent")
    if signed_declared_start - unsigned_declared_start != signed_start - unsigned_start:
        raise SignatureError("signed ZIP central directory displacement is inconsistent")
    unsigned_eocd = unsigned_path.stat().st_size - len(unsigned_tail) + unsigned_eocd_tail
    signed_eocd = signed_path.stat().st_size - len(signed_tail) + signed_eocd_tail
    unsigned_central_size = unsigned_eocd - unsigned_start
    signed_central_size = signed_eocd - signed_start
    if unsigned_central_size != signed_central_size:
        raise SignatureError("signed ZIP central directory differs from unsigned ZIP")
    with unsigned_path.open("rb") as unsigned_stream, signed_path.open("rb") as signed_stream:
        unsigned_stream.seek(unsigned_start)
        signed_stream.seek(signed_start)
        remaining = unsigned_central_size
        while remaining:
            size = min(64 * 1024, remaining)
            if unsigned_stream.read(size) != signed_stream.read(size):
                raise SignatureError("signed ZIP central directory differs from unsigned ZIP")
            remaining -= size
    left = unsigned_tail[unsigned_eocd_tail:]
    right = signed_tail[signed_eocd_tail:]
    if len(left) != len(right):
        raise SignatureError("signed ZIP end record differs from unsigned ZIP")
    if left[:16] != right[:16] or left[20:] != right[20:]:
        raise SignatureError("signed ZIP end record differs from unsigned ZIP")


def verify_official(signer: Path, signed: Path, certificate: Path) -> None:
    if file_hash(signer, 64 * 1024 * 1024, "locked JetBrains signer") != SIGNER_SHA256:
        raise SignatureError("JetBrains signer checksum does not match verification metadata")
    try:
        result = subprocess.run(
            ["java", "-jar", str(signer), "verify", "-in", str(signed), "-cert", str(certificate)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SignatureError("official JetBrains signature verification could not run") from error
    if result.returncode != 0:
        raise SignatureError("official JetBrains signature verification failed")


def check(unsigned: Path, signed: Path, certificate: Path, identity_path: Path, signer: Path) -> None:
    identity = read_identity(identity_path)
    details = certificate_details(certificate)
    if details.error:
        raise SignatureError(details.error)
    expected = identity["certificate"]
    if details.fingerprint != expected["sha256Fingerprint"]:
        raise SignatureError("certificate fingerprint does not match the approved identity")
    if details.common_name != expected["commonName"]:
        raise SignatureError("certificate common name does not match the approved identity")
    verify_official(signer, signed, certificate)
    compare_payload(unsigned, signed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unsigned", required=True, type=Path)
    parser.add_argument("--signed", required=True, type=Path)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--signer-jar", required=True, type=Path)
    args = parser.parse_args()
    try:
        check(args.unsigned, args.signed, args.certificate, args.identity, args.signer_jar)
    except SignatureError as error:
        print(f"plugin signature: {error}", file=sys.stderr)
        return 1
    print("plugin signature: valid JetBrains signature and unchanged payload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
