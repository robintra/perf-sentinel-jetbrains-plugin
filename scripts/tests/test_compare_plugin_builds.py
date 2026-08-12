import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_inspect_plugin_zip import (
    MAIN_JAR,
    PLUGIN_ROOT,
    archive_bytes,
    frontend_jar,
    main_jar,
    searchable_options_jar,
)


REPOSITORY = Path(__file__).resolve().parents[2]
COMPARATOR = REPOSITORY / "scripts/compare-plugin-builds.py"
INSPECTOR = REPOSITORY / "scripts/inspect-plugin-zip.py"
WORKFLOW = REPOSITORY / ".github/workflows/ci.yml"
BUILD = REPOSITORY / "build.gradle.kts"


def plugin_entries(*, main=None, dll=b"rider-dll", pdb=b"rider-pdb"):
    return [
        (f"{PLUGIN_ROOT}/", b""),
        (f"{PLUGIN_ROOT}/dotnet/", b""),
        (f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.dll", dll),
        (f"{PLUGIN_ROOT}/dotnet/PerfSentinel.Rider.pdb", pdb),
        (f"{PLUGIN_ROOT}/lib/", b""),
        (f"{PLUGIN_ROOT}/lib/{MAIN_JAR}", main if main is not None else main_jar()),
        (f"{PLUGIN_ROOT}/lib/perf-sentinel-rider-frontend.jar", frontend_jar()),
        (
            f"{PLUGIN_ROOT}/lib/perf-sentinel-jetbrains-plugin-0.1.0-searchableOptions.jar",
            searchable_options_jar(),
        ),
    ]


class PluginBuildComparatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive_a = self.root / "a" / "perf-sentinel-0.1.0.zip"
        self.archive_b = self.root / "b" / "perf-sentinel-0.1.0.zip"
        self.manifest_a = self.root / "a.json"
        self.manifest_b = self.root / "b.json"
        self.archive_a.parent.mkdir()
        self.archive_b.parent.mkdir()
        self.write_archives(plugin_entries(), plugin_entries())

    def tearDown(self):
        self.temporary.cleanup()

    def write_archives(self, entries_a, entries_b):
        self.archive_a.write_bytes(archive_bytes(entries_a))
        self.archive_b.write_bytes(archive_bytes(entries_b))
        for archive, manifest in (
            (self.archive_a, self.manifest_a),
            (self.archive_b, self.manifest_b),
        ):
            result = subprocess.run(
                [sys.executable, str(INSPECTOR), str(archive), "--manifest", str(manifest)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)

    def run_comparator(self):
        return subprocess.run(
            [
                sys.executable,
                str(COMPARATOR),
                "--archive-a", str(self.archive_a),
                "--manifest-a", str(self.manifest_a),
                "--archive-b", str(self.archive_b),
                "--manifest-b", str(self.manifest_b),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message):
        result = self.run_comparator()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_two_identical_closed_archives(self):
        result = self.run_comparator()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Windows plugin builds are byte-identical", result.stdout)

    def test_rejects_changed_byte_and_pdb(self):
        variants = (
            plugin_entries(main=main_jar((("resource.txt", b"changed"),))),
            plugin_entries(pdb=b"changed-pdb"),
        )
        for entries in variants:
            with self.subTest(entry=entries[3][1]):
                self.write_archives(plugin_entries(), entries)
                self.assert_rejected("archive bytes differ")

    def test_rejects_changed_descriptor(self):
        changed_main = archive_bytes(sorted([
            ("META-INF/", b""),
            ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"),
            ("META-INF/plugin.xml", b"<idea-plugin><id>changed</id></idea-plugin>"),
            ("io/github/robintra/perfsentinel/Plugin.class", b"\xca\xfe\xba\xbe"),
        ]))
        self.write_archives(plugin_entries(), plugin_entries(main=changed_main))
        self.assert_rejected("archive bytes differ")

    def test_rejects_order_change_missing_backend_and_extra_file(self):
        variants = []
        reordered = plugin_entries()
        reordered[2], reordered[3] = reordered[3], reordered[2]
        variants.append(reordered)
        variants.append([entry for entry in plugin_entries() if not entry[0].endswith(".dll")])
        variants.append([*plugin_entries(), (f"{PLUGIN_ROOT}/unexpected.txt", b"bad")])
        for entries in variants:
            with self.subTest(entries=len(entries)):
                self.archive_b.write_bytes(archive_bytes(entries))
                result = self.run_comparator()
                self.assertEqual(1, result.returncode)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_stale_or_modified_manifest(self):
        self.manifest_b.write_text('{"formatVersion":1}\n', encoding="utf-8")
        self.assert_rejected("manifest does not describe archive")

    def test_rejects_missing_inputs_without_traceback(self):
        self.archive_b.unlink()
        result = self.run_comparator()
        self.assertEqual(1, result.returncode)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class WindowsRiderWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")

    def test_has_two_isolated_windows_builds_and_one_comparison(self):
        self.assertIn("  windows-rider-build:\n", self.workflow)
        self.assertIn("runs-on: windows-2025", self.workflow)
        self.assertIn("- build: a", self.workflow)
        self.assertIn("- build: b", self.workflow)
        self.assertIn("GRADLE_USER_HOME: ${{ github.workspace }}\\.gradle-home-${{ matrix.build }}", self.workflow)
        self.assertIn("  windows-rider:\n", self.workflow)
        self.assertIn("scripts/compare-plugin-builds.py", self.workflow)

    def test_runs_locked_release_tests_coverage_and_closed_packaging(self):
        windows = self.workflow.split("  windows-rider-build:\n", 1)[1].split("\n  windows-rider:\n", 1)[0]
        self.assertIn("actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68", windows)
        self.assertIn("dotnet-version: 10.0.302", windows)
        self.assertIn('(dotnet --version) -ne "10.0.302"', windows)
        self.assertIn("--locked-mode", windows)
        self.assertIn("--configuration Release", windows)
        self.assertIn('--collect:"XPlat Code Coverage"', windows)
        self.assertIn('--logger "trx;LogFileName=tests.trx"', windows)
        self.assertIn(":rider-frontend:test", windows)
        self.assertIn("-PriderConfiguration=Release", windows)
        self.assertIn("scripts/inspect-plugin-zip.py", windows)
        self.assertIn("scripts/check-coverage.py", windows)
        self.assertEqual(1, windows.count("dotnet test src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj"))
        self.assertNotIn("--filter", windows)
        self.assertIn("Get-Content -Raw build/dotnet/TestResults/tests.trx", windows)
        self.assertIn("CSharpSymbolResolverTests did not run successfully", windows)
        self.assertLess(windows.index("clean :rider-frontend:test buildPlugin"), windows.index("dotnet restore"))
        self.assertLess(windows.index("dotnet restore"), windows.index("--collect:"))
        compare = self.workflow.split("  windows-rider:\n", 1)[1].split("\n  gate:\n", 1)[0]
        self.assertIn("cmp config/coverage-baseline.json build/windows/a/rider-baseline.json", compare)

    def test_linux_packaging_and_verifier_use_the_exact_dotnet_sdk(self):
        for start, end in (("  plugin-verifier:\n", "\n  zip:\n"), ("  zip:\n", "\n  dependency-review:\n")):
            with self.subTest(job=start.strip()):
                job = self.workflow.split(start, 1)[1].split(end, 1)[0]
                self.assertIn("actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68", job)
                self.assertIn("dotnet-version: 10.0.302", job)

    def test_rider_result_is_required_by_the_fail_closed_gate(self):
        checker = (REPOSITORY / "scripts/check-ci-results.py").read_text(encoding="utf-8")
        self.assertIn('"rider_windows"', checker)
        gate = self.workflow.split("  gate:\n", 1)[1]
        self.assertIn("windows-rider", gate)
        self.assertIn("RIDER_WINDOWS: ${{ needs.windows-rider.result }}", gate)

    def test_release_backend_configuration_is_closed_and_used_for_packaging(self):
        self.assertIn('gradleProperty("riderConfiguration").orElse("Debug")', self.build)
        self.assertIn("unknown riderConfiguration", self.build)
        self.assertIn('"--configuration",\n        riderConfiguration.get()', self.build)
        self.assertIn('"dotnet/bin/PerfSentinel.Rider/${riderConfiguration.get()}"', self.build)


if __name__ == "__main__":
    unittest.main()
