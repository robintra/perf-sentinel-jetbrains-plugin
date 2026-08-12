#!/usr/bin/env python3
"""Fail-closed inspection and stable manifest generation for plugin ZIPs."""

import argparse
from collections import Counter
import hashlib
import json
import re
import struct
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath


NORMALIZED_TIME = (1980, 2, 1, 0, 0, 0)
PLUGIN_ROOT = "perf-sentinel"
MAX_ARCHIVE_SIZE = 400 * 1024 * 1024
MAX_ENTRY_COUNT = 2048
MAX_ENTRY_SIZE = 400 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
CHUNK_SIZE = 64 * 1024
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
EOCD = struct.Struct("<4s4H2LH")
LOCAL_FILE_SIGNATURE = 0x04034B50
EOCD_SIGNATURE = b"PK\x05\x06"
UTF8_FLAG = 0x800
DEPENDENCY_CLASS_PREFIXES = (
    "com/fasterxml/jackson/",
    "com/google/gson/",
    "com/intellij/",
    "com/jetbrains/",
    "com/goide/",
    "kotlin/",
    "org/jetbrains/",
)
SECRET_CONTENT = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
)
ABSOLUTE_BUILD_PATH = re.compile(
    rb"(?:[A-Za-z]:[\\/](?:Users|agent|actions-runner|workspace|__w|tmp|a)[\\/][\x20-\x7e]{2,}"
    rb"|/(?:Users|home|private|tmp|workspace|__w)/[\x20-\x7e]{2,})"
)


class ValidationError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_path(name, label):
    if "\\" in name:
        raise ValidationError(f"{label}: backslash in entry path {name!r}")
    if not name or name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:/", name):
        raise ValidationError(f"{label}: unsafe path {name!r}")
    raw = name[:-1] if name.endswith("/") else name
    parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in parts) or PurePosixPath(raw).as_posix() != raw:
        raise ValidationError(f"{label}: unsafe path {name!r}")


def validate_entry_metadata(info, label):
    if info.date_time != NORMALIZED_TIME:
        raise ValidationError(f"{label}: nonnormalized timestamp for {info.filename}")
    expected_mode = 0o40755 if info.is_dir() else 0o100644
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode != expected_mode:
        raise ValidationError(f"{label}: nondeterministic mode for {info.filename}: {mode:o}")
    if info.extra:
        raise ValidationError(f"{label}: ZIP extra metadata for {info.filename}")
    if info.comment:
        raise ValidationError(f"{label}: entry comment for {info.filename}")
    if info.flag_bits not in {0, UTF8_FLAG}:
        raise ValidationError(f"{label}: unsupported ZIP flags for {info.filename}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValidationError(f"{label}: unsupported compression method for {info.filename}")


def read_at(source, offset, size, label):
    try:
        current = source.tell()
        source.seek(offset)
        data = source.read(size)
        source.seek(current)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"{label}: unable to read ZIP structure: {exc}") from exc
    if len(data) != size:
        raise ValidationError(f"{label}: truncated ZIP structure")
    return data


def source_size(source, label):
    try:
        current = source.tell()
        source.seek(0, 2)
        size = source.tell()
        source.seek(current)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"{label}: unable to size ZIP: {exc}") from exc
    return size


def dos_timestamp(date_time):
    year, month, day, hour, minute, second = date_time
    return (hour << 11) | (minute << 5) | (second // 2), ((year - 1980) << 9) | (month << 5) | day


def validate_eocd(archive, source, size, infos, label):
    tail_size = min(size, EOCD.size + 65535)
    tail = read_at(source, size - tail_size, tail_size, label)
    relative_offset = tail.rfind(EOCD_SIGNATURE)
    if relative_offset < 0 or relative_offset + EOCD.size > len(tail):
        raise ValidationError(f"{label}: missing end-of-central-directory record")
    offset = size - tail_size + relative_offset
    signature, disk, central_disk, disk_entries, total_entries, central_size, central_offset, comment_size = EOCD.unpack_from(
        tail, relative_offset
    )
    if signature != EOCD_SIGNATURE or disk or central_disk or disk_entries != len(infos) or total_entries != len(infos):
        raise ValidationError(f"{label}: unsupported central-directory layout")
    if comment_size or offset + EOCD.size + comment_size != size:
        raise ValidationError(f"{label}: trailing data after end-of-central-directory record")
    if central_offset != archive.start_dir or central_offset + central_size != offset:
        raise ValidationError(f"{label}: inconsistent central-directory bounds")


def validate_local_headers(archive, infos, label):
    source = archive.fp
    if source is None:
        raise ValidationError(f"{label}: closed ZIP source")
    size = source_size(source, label)
    validate_eocd(archive, source, size, infos, label)
    intervals = []
    for info in infos:
        if info.header_offset < 0 or info.header_offset + LOCAL_HEADER.size > archive.start_dir:
            raise ValidationError(f"{label}: local header outside archive bounds for {info.filename}")
        fields = LOCAL_HEADER.unpack(read_at(source, info.header_offset, LOCAL_HEADER.size, label))
        signature, _, flags, method, dos_time, dos_date, crc, compressed, uncompressed, name_size, extra_size = fields
        raw_name = read_at(source, info.header_offset + LOCAL_HEADER.size, name_size, label)
        expected_time, expected_date = dos_timestamp(info.date_time)
        expected_name = info.filename.encode("utf-8")
        if (
            signature != LOCAL_FILE_SIGNATURE
            or flags != info.flag_bits
            or method != info.compress_type
            or dos_time != expected_time
            or dos_date != expected_date
            or crc != info.CRC
            or compressed != info.compress_size
            or uncompressed != info.file_size
            or raw_name != expected_name
            or extra_size
            or (any(byte > 0x7F for byte in raw_name) and not flags & UTF8_FLAG)
        ):
            raise ValidationError(f"{label}: inconsistent local header for {info.filename}")
        data_start = info.header_offset + LOCAL_HEADER.size + name_size
        data_end = data_start + compressed
        if data_end > archive.start_dir:
            raise ValidationError(f"{label}: local entry overlaps central directory for {info.filename}")
        intervals.append((info.header_offset, data_end, info.filename))
    intervals.sort()
    previous_end = 0
    for start, end, name in intervals:
        if start != previous_end:
            raise ValidationError(f"{label}: overlapping or gapped local entry {name}")
        previous_end = end
    if previous_end != archive.start_dir:
        raise ValidationError(f"{label}: data gap before central directory")


def validate_common(archive, label, *, expected_order=None):
    if archive.comment:
        raise ValidationError(f"{label}: ZIP comment is not reproducible")
    infos = archive.infolist()
    if len(infos) > MAX_ENTRY_COUNT:
        raise ValidationError(f"{label}: too many entries")
    names = [info.filename for info in infos]
    canonical_names = [unicodedata.normalize("NFC", name).casefold() for name in names]
    counts = Counter(canonical_names)
    duplicate = next((name for name, canonical in zip(names, canonical_names) if counts[canonical] > 1), None)
    if duplicate is not None:
        raise ValidationError(f"{label}: duplicate entry {duplicate}")
    for info in infos:
        validate_path(info.filename, label)
        validate_entry_metadata(info, label)
        if info.file_size > MAX_ENTRY_SIZE:
            raise ValidationError(f"{label}: entry exceeds safety limit: {info.filename}")
        if info.file_size and (
            not info.compress_size or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
        ):
            raise ValidationError(f"{label}: excessive compression ratio for {info.filename}")
    if expected_order is not None and names != expected_order:
        raise ValidationError(f"{label}: invalid entry order")
    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_SIZE:
        raise ValidationError(f"{label}: uncompressed content exceeds safety limit")
    validate_local_headers(archive, infos, label)
    return infos


def forbidden_name(name, label):
    lower = name.lower()
    parts = lower.rstrip("/").split("/")
    basename = parts[-1]
    if basename == ".ds_store":
        raise ValidationError(f"{label}: .DS_Store is forbidden")
    if label != "plugin ZIP" and Path(basename).suffix in {".jar", ".zip"}:
        raise ValidationError(f"{label}: embedded archive {name}")
    if any(part in {"coverage", "report", "reports", "test-results"} for part in parts):
        raise ValidationError(f"{label}: generated report {name}")
    if (
        any(part in {"fixture", "fixtures", "test", "testdata", "tests"} for part in parts)
        or re.search(r"(?:test|tests|testkt)(?:\$.*)?\.class$", basename)
    ):
        raise ValidationError(f"{label}: test or fixture file {name}")
    if basename in {".env", "credentials", "credentials.json", "id_rsa"} or basename.startswith(".env.") or Path(basename).suffix in {
        ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx",
    }:
        raise ValidationError(f"{label}: secret path {name}")
    if lower.endswith(".class") and lower.startswith(DEPENDENCY_CLASS_PREFIXES):
        raise ValidationError(f"{label}: IDE or bundled dependency class {name}")


def stream_entry(archive, info, label, *, sink=None):
    digest = hashlib.sha256()
    if info.is_dir():
        return digest.hexdigest()
    tail = b""
    total = 0
    try:
        with archive.open(info) as source:
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                total += len(chunk)
                if total > MAX_ENTRY_SIZE or total > info.file_size:
                    raise ValidationError(f"{label}: entry exceeds declared safety limit: {info.filename}")
                digest.update(chunk)
                if sink is not None:
                    sink.write(chunk)
                window = tail + chunk
                if any(pattern.search(window) for pattern in SECRET_CONTENT):
                    raise ValidationError(f"{label}: secret content in {info.filename}")
                if ABSOLUTE_BUILD_PATH.search(window):
                    raise ValidationError(f"{label}: absolute build path in {info.filename}")
                tail = window[-256:]
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"{label}: unable to read {info.filename}: {exc}") from exc
    if total != info.file_size:
        raise ValidationError(f"{label}: size mismatch for {info.filename}")
    return digest.hexdigest()


def nested_manifest(source, label):
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"invalid nested jar {label}: {exc}") from exc
    with archive:
        infos = validate_common(archive, label, expected_order=sorted(archive.namelist()))
        entries = []
        for info in infos:
            forbidden_name(info.filename, label)
            entries.append(entry_manifest(info, stream_entry(archive, info, label)))
        return entries, {info.filename for info in infos}


def entry_manifest(info, digest):
    return {
        "compressedSize": info.compress_size,
        "crc32": f"{info.CRC:08x}",
        "mode": "0755" if info.is_dir() else "0644",
        "path": info.filename,
        "sha256": digest,
        "size": info.file_size,
        "timestamp": "1980-02-01T00:00:00Z",
    }


def inspect(path):
    if not path.is_file():
        raise ValidationError(f"missing plugin ZIP: {path}")
    if path.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValidationError("plugin ZIP exceeds 400 MiB")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"invalid plugin ZIP: {exc}") from exc

    with archive:
        names = archive.namelist()
        infos = validate_common(archive, "plugin ZIP")
        for info in infos:
            forbidden_name(info.filename, "plugin ZIP")
        roots = {name.split("/", 1)[0] for name in names if name}
        if roots != {PLUGIN_ROOT}:
            raise ValidationError(f"plugin ZIP must have the single root {PLUGIN_ROOT}/")

        prefix = f"{PLUGIN_ROOT}/lib/"
        jar_names = [name for name in names if name.startswith(prefix) and name.endswith(".jar")]
        main_pattern = re.compile(
            rf"^{re.escape(prefix)}perf-sentinel-jetbrains-plugin-([A-Za-z0-9._+\-]+)\.jar$"
        )
        main_jars = [
            name for name in jar_names
            if main_pattern.fullmatch(name) and not name.endswith("-searchableOptions.jar")
        ]
        if len(main_jars) != 1:
            raise ValidationError("plugin ZIP requires exactly one main plugin jar")
        main_jar = main_jars[0]
        version = main_pattern.fullmatch(main_jar).group(1)
        frontend_jar = f"{prefix}perf-sentinel-rider-frontend.jar"
        searchable_jar = f"{prefix}perf-sentinel-jetbrains-plugin-{version}-searchableOptions.jar"
        expected_jars = {main_jar, frontend_jar, searchable_jar}
        unexpected_jars = sorted(set(jar_names) - expected_jars)
        if unexpected_jars:
            raise ValidationError(f"unexpected jar {unexpected_jars[0]}")
        if set(jar_names) != expected_jars:
            missing = sorted(expected_jars - set(jar_names))[0]
            raise ValidationError(f"missing plugin jar {missing}")

        rider_dll = f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.dll"
        rider_pdb = f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.pdb"
        if rider_dll not in names:
            raise ValidationError("missing Rider DLL")
        if rider_pdb not in names:
            raise ValidationError("missing Rider PDB")

        licenses = sorted(
            name for name in names
            if name in {f"{PLUGIN_ROOT}/LICENSE", f"{PLUGIN_ROOT}/LICENSE.txt", f"{PLUGIN_ROOT}/NOTICE", f"{PLUGIN_ROOT}/NOTICE.txt"}
        )
        expected_order = [
            f"{PLUGIN_ROOT}/",
            *licenses,
            f"{PLUGIN_ROOT}/dotnet/",
            rider_dll,
            rider_pdb,
            f"{PLUGIN_ROOT}/lib/",
            main_jar,
            frontend_jar,
            searchable_jar,
        ]
        allowed = set(expected_order)
        unexpected = [name for name in names if name not in allowed]
        if unexpected:
            name = unexpected[0]
            if name.endswith(".jar"):
                raise ValidationError(f"unexpected jar {name}")
            raise ValidationError(f"unexpected plugin entry {name}")
        if names != expected_order:
            raise ValidationError("plugin ZIP: invalid entry order")

        entries = []
        nested = []
        for info in infos:
            if info.filename.endswith(".jar"):
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as nested_file:
                    digest = stream_entry(archive, info, "plugin ZIP", sink=nested_file)
                    nested_file.seek(0)
                    jar_entries, jar_names_set = nested_manifest(nested_file, info.filename)
                    nested.append({"archive": info.filename, "entries": jar_entries})
                classes = sorted(name for name in jar_names_set if name.endswith(".class"))
                if info.filename == main_jar:
                    if "META-INF/plugin.xml" not in jar_names_set:
                        raise ValidationError("main plugin jar is missing META-INF/plugin.xml")
                    if not classes:
                        raise ValidationError("main plugin jar has no plugin classes")
                    foreign = next(
                        (name for name in classes if not name.startswith("io/github/robintra/perfsentinel/")), None
                    )
                    if foreign:
                        raise ValidationError(f"main plugin jar contains unexpected class {foreign}")
                elif info.filename == frontend_jar:
                    if not classes:
                        raise ValidationError("Rider frontend jar has no plugin classes")
                    foreign = next(
                        (name for name in classes if not name.startswith("io/github/robintra/perfsentinel/rider/")), None
                    )
                    if foreign:
                        raise ValidationError(f"Rider frontend jar contains unexpected class {foreign}")
                elif classes:
                    raise ValidationError(f"searchable-options jar contains classes: {classes[0]}")
                if info.filename == searchable_jar and not any(name.endswith("searchableOptions.json") for name in jar_names_set):
                    raise ValidationError("searchable-options jar has no searchableOptions.json")
            else:
                digest = stream_entry(archive, info, "plugin ZIP")
            entries.append(entry_manifest(info, digest))

    return {
        "archive": path.name,
        "entries": entries,
        "formatVersion": 1,
        "nestedArchives": nested,
        "sha256": sha256_file(path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        manifest = inspect(args.archive)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    destination = args.manifest or args.archive.with_suffix(".manifest.json")
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Plugin ZIP is closed and reproducible: {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
