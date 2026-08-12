import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


CHECKER = Path(__file__).parents[1] / "inspect-plugin-zip.py"
NORMALIZED_TIME = (1980, 2, 1, 0, 0, 0)
PLUGIN_ROOT = "perf-sentinel"
MAIN_JAR = "perf-sentinel-jetbrains-plugin-0.1.0.jar"


def archive_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for entry in entries:
            name, data = entry[:2]
            timestamp = entry[2] if len(entry) > 2 else NORMALIZED_TIME
            mode = entry[3] if len(entry) > 3 else (0o40755 if name.endswith("/") else 0o100644)
            info = zipfile.ZipInfo(name, timestamp)
            info.comment = entry[4] if len(entry) > 4 else b""
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_STORED if name.endswith("/") else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


def main_jar(extra=(), *, timestamp=NORMALIZED_TIME, unsorted=False, own_class=True):
    entries = [
        ("META-INF/", b""),
        ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"),
        ("META-INF/plugin.xml", b'<idea-plugin><id>io.github.robintra.perfsentinel</id></idea-plugin>'),
    ]
    if own_class:
        entries.append(("io/github/robintra/perfsentinel/Plugin.class", b"\xca\xfe\xba\xbe"))
    entries.extend(extra)
    entries = sorted(entries, key=lambda item: item[0])
    if unsorted:
        entries[-2], entries[-1] = entries[-1], entries[-2]
    return archive_bytes([(entry[0], entry[1], timestamp, *entry[3:]) for entry in entries])


def frontend_jar(extra=()):
    return archive_bytes(sorted([
        ("META-INF/", b""),
        ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"),
        ("io/github/robintra/perfsentinel/rider/RiderAnchorResolver.class", b"\xca\xfe\xba\xbe"),
        *extra,
    ]))


def searchable_options_jar(extra=()):
    return archive_bytes(sorted([
        ("META-INF/", b""),
        ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"),
        ("p-io.github.robintra.perfsentinel-searchableOptions.json", b"{}\n"),
        *extra,
    ]))


def patch_local_header(data, entry_name, offset, fmt, value):
    output = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        header_offset = archive.getinfo(entry_name).header_offset
    struct.pack_into(fmt, output, header_offset + offset, value)
    return bytes(output)


def insert_before_central_directory(data, payload=b""):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        central_offset = archive.start_dir
    size = 24 + len(payload)
    gap = struct.pack("<Q", size) + payload + struct.pack("<Q", size) + b"APK Sig Block 42"
    output = bytearray(data[:central_offset] + gap + data[central_offset:])
    eocd_offset = output.rfind(b"PK\x05\x06")
    struct.pack_into("<I", output, eocd_offset + 16, central_offset + len(gap))
    return bytes(output)


class PluginZipInspectorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.archive = self.root / "perf-sentinel-0.1.0.zip"

    def tearDown(self):
        self.temp_dir.cleanup()

    def entries(self, *, main=None, frontend=None, searchable=None, timestamp=NORMALIZED_TIME, mode=None):
        file_mode = 0o100644 if mode is None else mode
        return [
            (f"{PLUGIN_ROOT}/", b"", timestamp),
            (f"{PLUGIN_ROOT}/dotnet/", b"", timestamp),
            (f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.dll", b"rider-dll", timestamp, file_mode),
            (f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.pdb", b"rider-pdb", timestamp, file_mode),
            (f"{PLUGIN_ROOT}/lib/", b"", timestamp),
            (f"{PLUGIN_ROOT}/lib/{MAIN_JAR}", main if main is not None else main_jar(), timestamp, file_mode),
            (
                f"{PLUGIN_ROOT}/lib/perf-sentinel-rider-frontend.jar",
                frontend if frontend is not None else frontend_jar(),
                timestamp,
                file_mode,
            ),
            (
                f"{PLUGIN_ROOT}/lib/perf-sentinel-jetbrains-plugin-0.1.0-searchableOptions.jar",
                searchable if searchable is not None else searchable_options_jar(),
                timestamp,
                file_mode,
            ),
        ]

    def write_archive(self, entries=None):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.archive.write_bytes(archive_bytes(entries if entries is not None else self.entries()))

    def run_checker(self, *extra):
        return subprocess.run(
            [sys.executable, str(CHECKER), str(self.archive), *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message):
        result = self.run_checker()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)

    def test_accepts_closed_plugin_layout_and_writes_stable_manifest(self):
        self.write_archive()
        first = self.root / "first.json"
        second = self.root / "second.json"

        first_result = self.run_checker("--manifest", str(first))
        second_result = self.run_checker("--manifest", str(second))

        self.assertEqual(0, first_result.returncode, first_result.stderr)
        self.assertEqual(0, second_result.returncode, second_result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        manifest = json.loads(first.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), manifest["sha256"])
        self.assertEqual(8, len(manifest["entries"]))
        self.assertNotIn(str(self.root), first.read_text(encoding="utf-8"))

    def test_rejects_duplicate_entries(self):
        entries = self.entries()
        entries.append(entries[-1])
        self.write_archive(entries)
        self.assert_rejected("duplicate entry")

    def test_rejects_unicode_and_casefold_collisions_outer_and_nested(self):
        for entries in (
            [
                *self.entries(),
                (f"{PLUGIN_ROOT}/caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt", b"a"),
                (f"{PLUGIN_ROOT}/cafe\N{COMBINING ACUTE ACCENT}.txt", b"b"),
            ],
            self.entries(main=main_jar((("resource/Name.txt", b"a"), ("resource/name.txt", b"b")))),
        ):
            with self.subTest(entries=len(entries)):
                self.write_archive(entries)
                self.assert_rejected("duplicate entry")

    def test_rejects_absolute_and_windows_paths(self):
        for name in ("/absolute.txt", "C:/absolute.txt", "//server/share.txt"):
            with self.subTest(name=name):
                self.write_archive([*self.entries(), (name, b"bad")])
                self.assert_rejected("unsafe path")

    def test_rejects_backslashes(self):
        self.write_archive([*self.entries(), (f"{PLUGIN_ROOT}\\secret.txt", b"bad")])
        self.assert_rejected("backslash")

    def test_rejects_unsafe_traversal(self):
        self.write_archive([*self.entries(), (f"{PLUGIN_ROOT}/../secret.txt", b"bad")])
        self.assert_rejected("unsafe path")

    def test_rejects_nonnormalized_timestamp(self):
        self.write_archive(self.entries(timestamp=(2026, 8, 12, 9, 30, 0)))
        self.assert_rejected("timestamp")

    def test_rejects_nondeterministic_file_mode(self):
        self.write_archive(self.entries(mode=0o100600))
        self.assert_rejected("mode")

    def test_rejects_unsorted_outer_entries(self):
        entries = self.entries()
        entries[2], entries[3] = entries[3], entries[2]
        self.write_archive(entries)
        self.assert_rejected("entry order")

    def test_rejects_unsorted_nested_jar_entries(self):
        self.write_archive(self.entries(main=main_jar(unsorted=True)))
        self.assert_rejected("entry order")

    def test_rejects_ds_store(self):
        self.write_archive([*self.entries(), (f"{PLUGIN_ROOT}/.DS_Store", b"bad")])
        self.assert_rejected(".DS_Store")

    def test_rejects_test_and_fixture_files(self):
        for path in (
            "io/github/robintra/perfsentinel/PluginTest.class",
            "io/github/robintra/perfsentinel/PluginTest$Companion.class",
            "test/data/fixture.json",
        ):
            with self.subTest(path=path):
                self.write_archive(self.entries(main=main_jar(((path, b"bad"),))))
                self.assert_rejected("test or fixture")

    def test_rejects_generated_reports(self):
        self.write_archive(self.entries(main=main_jar((("reports/tests/index.html", b"bad"),))))
        self.assert_rejected("generated report")

    def test_rejects_optional_ide_and_bundled_dependency_classes(self):
        class_paths = (
            "com/intellij/openapi/project/Project.class",
            "com/jetbrains/python/PythonFileType.class",
            "org/jetbrains/kotlin/stdlib/CollectionsKt.class",
            "kotlin/collections/CollectionsKt.class",
            "org/jetbrains/rd/framework/RdTask.class",
            "com/google/gson/Gson.class",
            "com/fasterxml/jackson/core/JsonParser.class",
            "com/goide/GoFileType.class",
        )
        for path in class_paths:
            with self.subTest(path=path):
                self.write_archive(self.entries(main=main_jar(((path, b"bad"),))))
                self.assert_rejected("IDE or bundled dependency class")

    def test_rejects_foreign_or_missing_classes_by_jar_role(self):
        variants = (
            (self.entries(main=main_jar(own_class=False)), "main plugin jar has no plugin classes"),
            (self.entries(main=main_jar((("evil/Payload.class", b"bad"),))), "unexpected class"),
            (self.entries(frontend=frontend_jar((("evil/Payload.class", b"bad"),))), "unexpected class"),
            (
                self.entries(searchable=searchable_options_jar((("evil/Payload.class", b"bad"),))),
                "searchable-options jar contains classes",
            ),
        )
        for entries, message in variants:
            with self.subTest(message=message):
                self.write_archive(entries)
                self.assert_rejected(message)

    def test_rejects_secret_paths_and_private_key_content(self):
        variants = (
            (("config/private.pem", b"not-even-a-key"), "secret path"),
            (("config/settings.properties", b"token=ghp_abcdefghijklmnopqrstuvwxyz123456"), "secret content"),
            (("config/settings.properties", b"-----BEGIN PRIVATE KEY-----\nabc"), "secret content"),
        )
        for entry, message in variants:
            with self.subTest(entry=entry[0]):
                self.write_archive(self.entries(main=main_jar((entry,))))
                self.assert_rejected(message)

    def test_rejects_secret_content_in_compiled_classes(self):
        entry = ("io/github/robintra/perfsentinel/Token.class", b"ghp_abcdefghijklmnopqrstuvwxyz123456")
        self.write_archive(self.entries(main=main_jar((entry,))))
        self.assert_rejected("secret content")

    def test_rejects_unexpected_jars(self):
        entries = self.entries()
        entries.insert(-1, (f"{PLUGIN_ROOT}/lib/kotlin-stdlib-2.4.10.jar", main_jar()))
        self.write_archive(entries)
        self.assert_rejected("unexpected jar")

    def test_rejects_archives_embedded_inside_plugin_jars(self):
        embedded = ("lib/dependency.jar", archive_bytes((("Dependency.class", b"bad"),)))
        self.write_archive(self.entries(main=main_jar((embedded,))))
        self.assert_rejected("embedded archive")

    def test_requires_rider_dll_and_pdb(self):
        for suffix in (".dll", ".pdb"):
            with self.subTest(suffix=suffix):
                entries = [entry for entry in self.entries() if not entry[0].endswith(suffix)]
                self.write_archive(entries)
                self.assert_rejected(f"missing Rider {suffix[1:].upper()}")

    def test_rejects_invalid_nested_jar(self):
        self.write_archive(self.entries(frontend=b"not-a-zip"))
        self.assert_rejected("invalid nested jar")

    def test_rejects_excessive_nested_entry_count(self):
        entries = tuple((f"payload/{index:04}.bin", b"") for index in range(2049))
        self.write_archive(self.entries(main=main_jar(entries)))
        self.assert_rejected("too many entries")

    def test_rejects_excessive_compression_ratio(self):
        self.write_archive(self.entries(main=main_jar((("payload.bin", b"0" * 1_000_000),))))
        self.assert_rejected("compression ratio")

    def test_rejects_absolute_build_paths_in_rider_binaries(self):
        for index, content in (
            (2, b"MZ/private/tmp/build/PerfSentinel.Rider.pdb"),
            (3, rb"BSJBD:\a\repository\build\PerfSentinel.Rider.pdb"),
        ):
            with self.subTest(index=index):
                entries = self.entries()
                binary = entries[index]
                entries[index] = (binary[0], content, *binary[2:])
                self.write_archive(entries)
                self.assert_rejected("absolute build path")

    def test_rejects_absolute_build_paths_in_plugin_classes(self):
        leaked = b"\xca\xfe\xba\xbe/private/tmp/agent/workspace/src/Plugin.kt"
        main = main_jar((("io/github/robintra/perfsentinel/Leaked.class", leaked),))
        self.write_archive(self.entries(main=main))
        self.assert_rejected("absolute build path")

    def test_rejects_inconsistent_outer_local_headers(self):
        original = archive_bytes(self.entries())
        entry = f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.dll"
        variants = (
            (10, "<H", 1),
            (6, "<H", 8),
            (8, "<H", zipfile.ZIP_STORED),
            (14, "<I", 1),
            (22, "<I", len(b"rider-dll") + 1),
        )
        for offset, fmt, value in variants:
            with self.subTest(offset=offset):
                self.archive.write_bytes(patch_local_header(original, entry, offset, fmt, value))
                self.assert_rejected("local header")

        renamed = bytearray(original)
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            info = archive.getinfo(entry)
        renamed[info.header_offset + 30] = ord("P")
        self.archive.write_bytes(renamed)
        self.assert_rejected("local header")

    def test_rejects_inconsistent_nested_local_headers(self):
        nested = main_jar()
        nested = patch_local_header(nested, "META-INF/plugin.xml", 8, "<H", zipfile.ZIP_STORED)
        self.write_archive(self.entries(main=nested))
        self.assert_rejected("local header")

    def test_rejects_trailing_zip_data(self):
        self.archive.write_bytes(archive_bytes(self.entries()) + b"trailing")
        self.assert_rejected("trailing data")

    def test_rejects_outer_and_nested_entry_comments(self):
        entries = self.entries()
        commented = entries[2]
        entries[2] = (*commented, b"comment")
        nested = main_jar((
            ("io/github/robintra/perfsentinel/Commented.class", b"bad", NORMALIZED_TIME, 0o100644, b"comment"),
        ))
        for scope, variant in (("outer", entries), ("nested", self.entries(main=nested))):
            with self.subTest(scope=scope):
                self.write_archive(variant)
                self.assert_rejected("entry comment")

    def test_rejects_any_gap_before_central_directory(self):
        for payload in (b"", b"payload"):
            with self.subTest(scope="outer", payload=payload):
                self.archive.write_bytes(insert_before_central_directory(archive_bytes(self.entries()), payload))
                self.assert_rejected("data gap")

        nested = insert_before_central_directory(main_jar())
        self.write_archive(self.entries(main=nested))
        self.assert_rejected("data gap")

    def test_allows_incidental_drive_marker_in_binary_data(self):
        entries = self.entries()
        pdb = entries[3]
        entries[3] = (pdb[0], b"BSJB\x00A:/\xff\x00", *pdb[2:])
        self.write_archive(entries)
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
