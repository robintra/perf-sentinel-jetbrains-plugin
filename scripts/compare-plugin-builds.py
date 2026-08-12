#!/usr/bin/env python3
"""Compare two independently inspected unsigned plugin archives."""

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


CHUNK_SIZE = 64 * 1024
MAX_MANIFEST_SIZE = 16 * 1024 * 1024
INSPECTOR_PATH = Path(__file__).with_name("inspect-plugin-zip.py")


def load_inspector():
    spec = importlib.util.spec_from_file_location("plugin_zip_inspector", INSPECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load plugin ZIP inspector")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSPECTOR = load_inspector()


class ComparisonError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError as error:
        raise ComparisonError(f"unable to read archive {path}") from error
    return digest.hexdigest()


def read_manifest(path: Path, archive: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_MANIFEST_SIZE + 1)
    except OSError as error:
        raise ComparisonError(f"unable to read manifest {path}") from error
    if not payload or len(payload) > MAX_MANIFEST_SIZE or payload.startswith(b"\xef\xbb\xbf"):
        raise ComparisonError("manifest does not describe archive")
    try:
        data = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ComparisonError("manifest does not describe archive") from error
    try:
        expected = INSPECTOR.inspect(archive)
    except INSPECTOR.ValidationError as error:
        raise ComparisonError(f"archive is not closed: {archive}") from error
    expected_payload = (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if type(data) is not dict or payload != expected_payload:
        raise ComparisonError("manifest does not describe archive")
    return payload


def compare(archive_a: Path, manifest_a: Path, archive_b: Path, manifest_b: Path) -> str:
    left_manifest = read_manifest(manifest_a, archive_a)
    right_manifest = read_manifest(manifest_b, archive_b)
    left_digest, right_digest = sha256(archive_a), sha256(archive_b)
    if left_digest != right_digest:
        raise ComparisonError("archive bytes differ")
    if left_manifest != right_manifest:
        raise ComparisonError("content manifests differ")
    return left_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-a", required=True, type=Path)
    parser.add_argument("--manifest-a", required=True, type=Path)
    parser.add_argument("--archive-b", required=True, type=Path)
    parser.add_argument("--manifest-b", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        digest = compare(
            arguments.archive_a,
            arguments.manifest_a,
            arguments.archive_b,
            arguments.manifest_b,
        )
    except (OSError, ComparisonError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Windows plugin builds are byte-identical: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
