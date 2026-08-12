#!/usr/bin/env python3
"""Create or verify the closed Perf Sentinel JetBrains release manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path


PLUGIN_ID = "io.github.robintra.perfsentinel"
VERSION = re.compile(r"^0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 2048


class ReleaseError(ValueError):
    pass


class DuplicateKey(ValueError):
    pass


def load_signature_checker():
    path = Path(__file__).with_name("check-plugin-signature.py")
    spec = importlib.util.spec_from_file_location("plugin_signature_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("plugin signature checker cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SIGNATURE = load_signature_checker()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKey(key)
        value[key] = item
    return value


def read_json(path: Path, maximum: int, label: str):
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
        if not payload or len(payload) > maximum or payload.startswith(b"\xef\xbb\xbf"):
            raise ReleaseError(f"{label} violates its size or encoding policy")
        return json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey) as error:
        raise ReleaseError(f"{label} is not strict JSON") from error


def digest(path: Path, maximum: int, label: str) -> tuple[str, int]:
    value = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ReleaseError(f"{label} exceeds its size bound")
                value.update(chunk)
    except OSError as error:
        raise ReleaseError(f"{label} cannot be read") from error
    if not size:
        raise ReleaseError(f"{label} is empty")
    return value.hexdigest(), size


def validate_version(version: str) -> None:
    if VERSION.fullmatch(version) is None:
        raise ReleaseError("version must be stable 0.MINOR.PATCH")


def validate_spdx(path: Path) -> None:
    value = read_json(path, MAX_JSON_BYTES, "SBOM")
    required = {"spdxVersion", "dataLicense", "SPDXID", "name", "packages"}
    if (
        type(value) is not dict
        or not required.issubset(value)
        or value["spdxVersion"] != "SPDX-2.3"
        or value["dataLicense"] != "CC0-1.0"
        or value["SPDXID"] != "SPDXRef-DOCUMENT"
        or type(value["name"]) is not str
        or type(value["packages"]) is not list
        or not value["packages"]
        or any(type(item) is not dict or type(item.get("SPDXID")) is not str for item in value["packages"])
    ):
        raise ReleaseError("SBOM is not a non-empty SPDX 2.3 document")


def file_record(path: Path, maximum: int, label: str) -> dict:
    sha256, size = digest(path, maximum, label)
    return {"name": path.name, "sha256": sha256, "size": size}


def certificate_identity(certificate: Path, identity_path: Path) -> dict:
    identity = SIGNATURE.read_identity(identity_path)
    details = SIGNATURE.certificate_details(certificate)
    if details.error:
        raise ReleaseError(details.error)
    expected = identity["certificate"]
    if details.common_name != expected["commonName"] or details.fingerprint != expected["sha256Fingerprint"]:
        raise ReleaseError("certificate does not match the approved identity")
    return {"commonName": details.common_name, "sha256Fingerprint": details.fingerprint}


def expected_names(version: str) -> dict[str, str]:
    return {
        "unsigned": f"perf-sentinel-{version}.zip",
        "zip": f"perf-sentinel-{version}-signed.zip",
        "sbom": f"perf-sentinel-{version}.spdx.json",
        "certificate": f"perf-sentinel-{version}-certificate-chain.pem",
    }


def require_name(path: Path, expected: str, label: str) -> None:
    if path.name != expected:
        raise ReleaseError(f"{label} name must be {expected}")


def create(arguments) -> None:
    validate_version(arguments.version)
    names = expected_names(arguments.version)
    for path, key, label in (
        (arguments.unsigned, "unsigned", "unsigned ZIP"),
        (arguments.signed, "zip", "signed ZIP"),
        (arguments.sbom, "sbom", "SBOM"),
        (arguments.certificate, "certificate", "certificate chain"),
    ):
        require_name(path, names[key], label)
    validate_spdx(arguments.sbom)
    try:
        SIGNATURE.check(
            arguments.unsigned,
            arguments.signed,
            arguments.certificate,
            arguments.identity,
            arguments.signer_jar,
        )
    except SIGNATURE.SignatureError as error:
        raise ReleaseError(str(error)) from error
    value = {
        "schemaVersion": 1,
        "pluginId": PLUGIN_ID,
        "version": arguments.version,
        "channel": "default",
        "certificate": certificate_identity(arguments.certificate, arguments.identity),
        "source": {"unsignedSha256": digest(arguments.unsigned, MAX_ARCHIVE_BYTES, "unsigned ZIP")[0]},
        "published": {
            "zip": file_record(arguments.signed, MAX_ARCHIVE_BYTES, "signed ZIP"),
            "sbom": file_record(arguments.sbom, MAX_JSON_BYTES, "SBOM"),
            "certificate": file_record(arguments.certificate, SIGNATURE.MAX_CONFIG_BYTES, "certificate chain"),
        },
    }
    arguments.manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_record(value, expected_name: str, label: str) -> None:
    if (
        type(value) is not dict
        or set(value) != {"name", "sha256", "size"}
        or value["name"] != expected_name
        or type(value["sha256"]) is not str
        or SHA256.fullmatch(value["sha256"]) is None
        or type(value["size"]) is not int
        or value["size"] <= 0
    ):
        raise ReleaseError(f"release manifest has an invalid {label} record")


def read_manifest(path: Path, version: str) -> dict:
    value = read_json(path, MAX_JSON_BYTES, "release manifest")
    if type(value) is not dict or set(value) != {
        "schemaVersion", "pluginId", "version", "channel", "certificate", "source", "published"
    }:
        raise ReleaseError("release manifest has unknown or missing fields")
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["pluginId"] != PLUGIN_ID
        or value["version"] != version
        or value["channel"] != "default"
        or type(value["source"]) is not dict
        or set(value["source"]) != {"unsignedSha256"}
        or type(value["source"]["unsignedSha256"]) is not str
        or SHA256.fullmatch(value["source"]["unsignedSha256"]) is None
        or type(value["certificate"]) is not dict
        or set(value["certificate"]) != {"commonName", "sha256Fingerprint"}
        or type(value["certificate"]["commonName"]) is not str
        or type(value["certificate"]["sha256Fingerprint"]) is not str
        or SHA256.fullmatch(value["certificate"]["sha256Fingerprint"]) is None
        or type(value["published"]) is not dict
        or set(value["published"]) != {"zip", "sbom", "certificate"}
    ):
        raise ReleaseError("release manifest is not canonical")
    names = expected_names(version)
    for key in ("zip", "sbom", "certificate"):
        validate_record(value["published"][key], names[key], key)
    return value


def require_record(path: Path, record: dict, maximum: int, label: str) -> None:
    sha256, size = digest(path, maximum, label)
    if path.name != record["name"] or sha256 != record["sha256"] or size != record["size"]:
        raise ReleaseError(f"{label} digest or size does not match the release manifest")


def zip_payload(path: Path, label: str) -> tuple:
    digest(path, MAX_ARCHIVE_BYTES, label)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ENTRIES or len({item.filename for item in infos}) != len(infos):
                raise ReleaseError(f"{label} has an invalid entry set")
            total = 0
            records = []
            for info in infos:
                total += info.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise ReleaseError(f"{label} exceeds its expanded size bound")
                value = hashlib.sha256()
                size = 0
                with archive.open(info) as stream:
                    while chunk := stream.read(64 * 1024):
                        size += len(chunk)
                        if size > info.file_size or size > MAX_ARCHIVE_BYTES:
                            raise ReleaseError(f"{label} entry exceeds its declared size")
                        value.update(chunk)
                if size != info.file_size:
                    raise ReleaseError(f"{label} entry size is inconsistent")
                records.append((
                    info.filename, info.date_time, info.compress_type, info.CRC,
                    info.file_size, info.compress_size, info.flag_bits,
                    info.create_system, info.external_attr, info.extra, info.comment,
                    value.hexdigest(),
                ))
            return tuple(records)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        if isinstance(error, ReleaseError):
            raise
        raise ReleaseError(f"{label} is not a valid ZIP") from error


def verify(arguments) -> None:
    validate_version(arguments.version)
    value = read_manifest(arguments.manifest, arguments.version)
    validate_spdx(arguments.sbom)
    identity = certificate_identity(arguments.certificate, arguments.identity)
    if identity != value["certificate"]:
        raise ReleaseError("certificate identity does not match the release manifest")
    records = value["published"]
    require_record(arguments.signed, records["zip"], MAX_ARCHIVE_BYTES, "signed ZIP")
    require_record(arguments.github_zip, records["zip"], MAX_ARCHIVE_BYTES, "GitHub ZIP")
    require_name(arguments.marketplace_zip, records["zip"]["name"], "Marketplace ZIP")
    marketplace_sha256, _ = digest(arguments.marketplace_zip, MAX_ARCHIVE_BYTES, "Marketplace ZIP")
    if arguments.marketplace_distributed and marketplace_sha256 == records["zip"]["sha256"]:
        raise ReleaseError("Marketplace ZIP is missing its second JetBrains signature")
    require_record(arguments.sbom, records["sbom"], MAX_JSON_BYTES, "SBOM")
    require_record(arguments.certificate, records["certificate"], SIGNATURE.MAX_CONFIG_BYTES, "certificate chain")
    try:
        SIGNATURE.verify_official(arguments.signer_jar, arguments.signed, arguments.certificate)
        SIGNATURE.verify_official(arguments.signer_jar, arguments.marketplace_zip, arguments.certificate)
    except SIGNATURE.SignatureError as error:
        raise ReleaseError(f"Marketplace ZIP is not the verified author ZIP plus a valid Marketplace signature: {error}") from error
    if zip_payload(arguments.signed, "signed ZIP") != zip_payload(arguments.marketplace_zip, "Marketplace ZIP"):
        raise ReleaseError("Marketplace ZIP plugin entries differ from the signed GitHub ZIP")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="mode", required=True)
    for mode in ("create", "verify"):
        command = subcommands.add_parser(mode)
        command.add_argument("--version", required=True)
        command.add_argument("--signed", required=True, type=Path)
        command.add_argument("--sbom", required=True, type=Path)
        command.add_argument("--certificate", required=True, type=Path)
        command.add_argument("--identity", required=True, type=Path)
        command.add_argument("--signer-jar", required=True, type=Path)
        command.add_argument("--manifest", required=True, type=Path)
    subcommands.choices["create"].add_argument("--unsigned", required=True, type=Path)
    subcommands.choices["verify"].add_argument("--github-zip", required=True, type=Path)
    subcommands.choices["verify"].add_argument("--marketplace-zip", required=True, type=Path)
    subcommands.choices["verify"].add_argument("--marketplace-distributed", action="store_true")
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        globals()[arguments.mode](arguments)
    except (OSError, ReleaseError, SIGNATURE.SignatureError) as error:
        print(f"release verification: {error}", file=sys.stderr)
        return 1
    print(f"release verification: {arguments.mode} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
