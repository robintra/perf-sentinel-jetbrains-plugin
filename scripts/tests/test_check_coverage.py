import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-coverage.py"
SPEC = importlib.util.spec_from_file_location("coverage_checker", CHECKER)
COVERAGE_CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = COVERAGE_CHECKER
SPEC.loader.exec_module(COVERAGE_CHECKER)


def kover(filename="App.kt", package="io/github/example", lines=((1, 1),)):
    entries = "".join(
        f'<line nr="{number}" mi="{int(not covered)}" ci="{covered}" mb="0" cb="0"/>'
        for number, covered in lines
    )
    covered = sum(value > 0 for _, value in lines)
    missed = len(lines) - covered
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<report name="perf-sentinel"><sessioninfo id="test" start="1" dump="2"/>'
        f'<package name="{package}"><class name="App" sourcefilename="{filename}">'
        '<method name="run" desc="()V">'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        '</method>'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        '</class>'
        f'<sourcefile name="{filename}">{entries}'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        '</sourcefile>'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        '</package>'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        '</report>'
    )


def cobertura(filename="src/dotnet/App.cs", lines=((1, 1),)):
    covered = sum(value > 0 for _, value in lines)
    entries = "".join(
        f'<line number="{number}" hits="{hits}" branch="False"/>'
        for number, hits in lines
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<coverage lines-covered="{covered}" lines-valid="{len(lines)}" '
        f'line-rate="{covered / len(lines) if lines else 0}" branch-rate="0" '
        'version="6.0.4" timestamp="1" branches-covered="0" branches-valid="0">'
        '<sources><source>/_/</source></sources><packages>'
        '<package name="PerfSentinel.Rider" line-rate="1" branch-rate="0" complexity="0"><classes>'
        f'<class name="App" filename="{filename}" line-rate="1" branch-rate="0" complexity="0">'
        '<methods/><lines>'
        f'{entries}</lines></class>'
        '</classes></package></packages></coverage>'
    )


def numeric_baseline(jvm=100, rider=100):
    rider_value = (
        '{"status":"pending_windows"}'
        if rider == "pending_windows"
        else f'{{"total_line_coverage":{rider}}}'
    )
    return (
        '{"schema_version":1,"new_code_minimum":80,"surfaces":{'
        f'"jvm":{{"total_line_coverage":{jvm}}},"rider":{rider_value}'
        '}}\n'
    )


def changed_manifest(surface, lines=(), path=None):
    path = path or (
        "src/main/kotlin/io/github/example/App.kt" if surface == "jvm" else "src/dotnet/App.cs"
    )
    records = "".join(f"{path}\t{line}\n" for line in lines)
    return f"coverage-changed-lines-v1\t{surface}\n{records}"


class CoverageCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.current = self.root / "current.xml"
        self.reference = self.root / "reference.xml"
        self.baseline = self.root / "coverage-baseline.json"
        self.changed = self.root / "changed-lines.tsv"
        self.changed_surface = None

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, path, content):
        path.write_text(content, encoding="utf-8")

    def run_checker(self, surface, *extra):
        changed_arguments = (
            ["--changed-lines-file", str(self.changed)] if "--baseline-report" in extra else []
        )
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--surface",
                surface,
                "--current-report",
                str(self.current),
                "--baseline-file",
                str(self.baseline),
                *changed_arguments,
                *map(str, extra),
            ],
            cwd=REPOSITORY,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, result, message):
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def prepare(self, surface, reference_lines, current_lines, jvm=100, rider=100):
        report = kover if surface == "jvm" else cobertura
        self.write(self.reference, report(lines=reference_lines))
        self.write(self.current, report(lines=current_lines))
        self.write(self.baseline, numeric_baseline(jvm, rider))
        reference_numbers = {number for number, _ in reference_lines}
        self.write_changed(surface, (number for number, _ in current_lines if number not in reference_numbers))

    def write_changed(self, surface, lines=(), path=None):
        self.write(self.changed, changed_manifest(surface, lines, path))
        self.changed_surface = surface

    def check(self, surface):
        if self.changed_surface != surface:
            self.write_changed(surface)
        return self.run_checker(surface, "--baseline-report", self.reference)

    def test_checks_jvm_and_rider_numeric_baselines_independently(self):
        cases = (("jvm", 50, 100), ("rider", 100, 50))
        for surface, jvm, rider in cases:
            with self.subTest(surface=surface):
                self.prepare(surface, ((1, 1), (2, 0)), ((1, 1), (2, 0)), jvm, rider)
                result = self.check(surface)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn(f"{surface} total line coverage: 50.00%", result.stdout)

    def test_rejects_total_regression_against_numeric_baseline(self):
        for surface in ("jvm", "rider"):
            with self.subTest(surface=surface):
                reference = tuple((number, int(number <= 51)) for number in range(1, 101))
                current = tuple((number, int(number <= 50)) for number in range(1, 101))
                self.prepare(surface, reference, current, 51, 51)
                self.assert_rejected(self.check(surface), "total line coverage regressed: 50.00% < 51.00%")

    def test_rejects_reference_report_that_does_not_match_numeric_baseline(self):
        self.prepare("jvm", ((1, 1), (2, 0)), ((1, 1), (2, 0)), jvm=60)
        self.assert_rejected(self.check("jvm"), "baseline report is 50.00%, configured baseline is 60.00%")

    def test_rejects_79_99_percent_new_code_coverage_without_rounding_up(self):
        for surface in ("jvm", "rider"):
            with self.subTest(surface=surface):
                additions = tuple((number, int(number <= 8_000)) for number in range(2, 10_002))
                self.prepare(surface, ((1, 0),), ((1, 0), *additions), 0, 0)
                self.assert_rejected(self.check(surface), "new-code line coverage is 79.99%; required 80.00%")

    def test_accepts_exactly_80_percent_new_code_coverage(self):
        for surface in ("jvm", "rider"):
            with self.subTest(surface=surface):
                self.prepare(
                    surface,
                    ((1, 0),),
                    ((1, 0), (2, 1), (3, 1), (4, 1), (5, 1), (6, 0)),
                    0,
                    0,
                )
                result = self.check(surface)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("new-code line coverage: 80.00%", result.stdout)

    def test_deleted_lines_are_not_new_code(self):
        for surface in ("jvm", "rider"):
            with self.subTest(surface=surface):
                self.prepare(surface, ((1, 1), (2, 0)), ((1, 1),), 50, 50)
                result = self.check(surface)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("no changed executable lines", result.stdout)

    def test_existing_modified_line_is_new_code(self):
        self.prepare("jvm", ((1, 0),), ((1, 0),), jvm=0)
        self.write_changed("jvm", (1,))

        self.assert_rejected(self.check("jvm"), "new-code line coverage is 0.00%; required 80.00%")

    def test_maps_only_exact_jvm_and_rider_source_roots_from_changed_manifest(self):
        self.write_changed(
            "jvm",
            (7,),
            "src/main/kotlin/io/github/example/App.kt",
        )
        with self.changed.open("a", encoding="utf-8") as stream:
            stream.write("rider-frontend/src/main/kotlin/io/github/example/Rider.kt\t8\n")
        self.assertEqual(
            {
                ("io/github/example/app.kt", 7),
                ("io/github/example/rider.kt", 8),
            },
            COVERAGE_CHECKER.read_changed_lines(self.changed, "jvm"),
        )

        self.write_changed("rider", (9,), "src/dotnet/PerfSentinel.Rider/App.cs")
        self.assertEqual(
            {("src/dotnet/perfsentinel.rider/app.cs", 9)},
            COVERAGE_CHECKER.read_changed_lines(self.changed, "rider"),
        )

    def test_rejects_unsupported_or_duplicate_changed_manifest_records(self):
        manifests = (
            changed_manifest("jvm", (1,), "protocol/src/main/kotlin/Model.kt"),
            changed_manifest("rider", (1,), "src/main/kotlin/App.kt"),
            changed_manifest("jvm", (1, 1)),
        )
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                self.write(self.changed, manifest)
                with self.assertRaises(COVERAGE_CHECKER.CoverageError):
                    COVERAGE_CHECKER.read_changed_lines(
                        self.changed,
                        manifest.split("\t", 1)[1].splitlines()[0],
                    )

    def test_normalizes_slashes_dot_segments_and_pathmap_root(self):
        self.write(self.reference, kover(filename=r"folder\\.\\App.kt", package=r"src\\main\\kotlin"))
        self.write(self.current, kover(filename="folder/App.kt", package="src/main/kotlin"))
        self.write(self.baseline, numeric_baseline())
        result = self.check("jvm")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no changed executable lines", result.stdout)

        self.write(self.reference, cobertura(filename=r"\\_\\src\\dotnet\\App.cs"))
        self.write(self.current, cobertura(filename="/_/src/dotnet/App.cs"))
        result = self.check("rider")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no changed executable lines", result.stdout)

    def test_rejects_unstable_absolute_and_traversing_paths(self):
        cases = (
            ("jvm", kover(filename="../../App.kt")),
            ("rider", cobertura(filename="C:/agent/_work/App.cs")),
            ("rider", cobertura(filename="./C:/agent/_work/App.cs")),
            ("rider", cobertura(filename=r".\C:\agent\_work\App.cs")),
        )
        self.write(self.baseline, numeric_baseline())
        for surface, report in cases:
            with self.subTest(surface=surface):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check(surface), "source path is not stable")

    def test_rejects_malformed_and_wrong_format_reports(self):
        self.write(self.baseline, numeric_baseline())
        cases = (
            ("jvm", "<report>", "unable to parse Kover XML"),
            ("jvm", cobertura(), "Kover root must be report"),
            ("rider", "<coverage>", "unable to parse Cobertura XML"),
            ("rider", kover(), "Cobertura root must be coverage"),
        )
        for surface, report, message in cases:
            with self.subTest(surface=surface, message=message):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check(surface), message)

    def test_rejects_zero_executable_lines(self):
        self.write(self.baseline, numeric_baseline(0, 0))
        for surface, report in (("jvm", kover(lines=())), ("rider", cobertura(lines=()))):
            with self.subTest(surface=surface):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check(surface), "report contains no executable lines")

    def test_rejects_duplicate_line_records_after_path_normalization(self):
        kover_duplicate = kover(lines=((1, 1), (1, 0)))
        cobertura_duplicate = cobertura(lines=((1, 1), (1, 0)))
        self.write(self.baseline, numeric_baseline())
        for surface, report in (("jvm", kover_duplicate), ("rider", cobertura_duplicate)):
            with self.subTest(surface=surface):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check(surface), "duplicate executable line")

    def test_rejects_incoherent_kover_and_cobertura_counters(self):
        self.write(self.baseline, numeric_baseline())
        kover_report = kover()
        head, tail = kover_report.split("</class>", 1)
        cases = (
            ("jvm", head + "</class>" + tail.replace('type="LINE" missed="0" covered="1"', 'type="LINE" missed="1" covered="0"', 1), "Kover LINE counter differs"),
            ("rider", cobertura().replace('lines-covered="1" lines-valid="1"', 'lines-covered="0" lines-valid="2"'), "Cobertura root line counts differ"),
        )
        for surface, report, message in cases:
            with self.subTest(surface=surface):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check(surface), message)

    def test_rejects_unknown_cobertura_attributes_and_nested_elements(self):
        self.write(self.baseline, numeric_baseline())
        reports = (
            cobertura().replace('<class name=', '<class unknown="value" name='),
            cobertura().replace('branch="False"/>', 'branch="False"><garbage/></line>'),
            cobertura().replace('<methods/>', '<methods><garbage/></methods>'),
        )
        for report in reports:
            with self.subTest(report=report[-120:]):
                self.write(self.reference, report)
                self.write(self.current, report)
                self.assert_rejected(self.check("rider"), "Cobertura structure is invalid")

    def test_accepts_canonical_cobertura_branch_and_method_structure(self):
        branch_line = (
            '<line number="1" hits="1" branch="True" condition-coverage="50% (1/2)">'
            '<conditions><condition number="0" type="jump" coverage="50%"/></conditions></line>'
        )
        report = cobertura().replace(
            "<methods/>",
            '<methods><method name="Run" signature="()" line-rate="1" branch-rate="0.5" complexity="1">'
            f"<lines>{branch_line}</lines></method></methods>",
        ).replace('<line number="1" hits="1" branch="False"/>', branch_line)
        self.write(self.baseline, numeric_baseline())
        self.write(self.reference, report)
        self.write(self.current, report)

        self.assertEqual(0, self.check("rider").returncode)

    def test_rejects_malformed_kover_session_structure(self):
        report = kover().replace(
            '<sessioninfo id="test" start="1" dump="2"/>',
            '<sessioninfo id="test" start="1" dump="2"><garbage/></sessioninfo>',
        )
        self.write(self.baseline, numeric_baseline())
        self.write(self.reference, report)
        self.write(self.current, report)

        self.assert_rejected(self.check("jvm"), "Kover session structure is invalid")

    def test_accepts_realistic_kover_session_timestamps(self):
        report = kover().replace('start="1" dump="2"', 'start="1786529359000" dump="1786529359999"')
        self.write(self.baseline, numeric_baseline())
        self.write(self.reference, report)
        self.write(self.current, report)

        self.assertEqual(0, self.check("jvm").returncode)

    def test_rejects_duplicate_kover_method_identity(self):
        report = kover()
        method = (
            '<method name="run" desc="()V">'
            '<counter type="LINE" missed="0" covered="1"/></method>'
        )
        report = report.replace(method, method + method)
        head, tail = report.rsplit("</method>", 1)
        report = head + "</method>" + tail.replace(
            'type="LINE" missed="0" covered="1"',
            'type="LINE" missed="0" covered="2"',
        )
        self.write(self.baseline, numeric_baseline())
        self.write(self.reference, report)
        self.write(self.current, report)

        self.assert_rejected(self.check("jvm"), "duplicate Kover method")

    def test_rejects_inflated_kover_source_and_container_counters(self):
        report = kover()
        head, tail = report.split("</class>", 1)
        self.write(self.baseline, numeric_baseline())
        for replacement in (
            'type="LINE" missed="0" covered="2"',
            'type="LINE" missed="1" covered="1"',
        ):
            with self.subTest(counter=replacement):
                inflated = head + "</class>" + tail.replace(
                    'type="LINE" missed="0" covered="1"', replacement
                )
                self.write(self.reference, inflated)
                self.write(self.current, inflated)
                self.assert_rejected(
                    self.check("jvm"),
                    "Kover sourcefile LINE counter differs from class counters",
                )

    def test_rejects_bom_non_utf8_and_dtd_before_xml_parsing(self):
        valid = kover().encode()
        payloads = (
            b"\xef\xbb\xbf" + valid,
            b"\xff" + valid,
            kover().replace("<report", '<!DOCTYPE report [<!ENTITY probe "expanded">]><report').encode(),
        )
        self.write(self.baseline, numeric_baseline())
        self.write(self.reference, kover())
        for payload in payloads:
            with self.subTest(prefix=payload[:12]):
                self.current.write_bytes(payload)
                result = self.check("jvm")
                self.assertEqual(1, result.returncode)
                self.assertNotIn("expanded", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_duplicate_closed_or_non_numeric_baselines(self):
        self.write(self.reference, kover())
        self.write(self.current, kover())
        baselines = (
            numeric_baseline().replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
            numeric_baseline()[:-2] + ',"unexpected":true}\n',
            numeric_baseline().replace('"total_line_coverage":100', '"total_line_coverage":true', 1),
            numeric_baseline().replace('"total_line_coverage":100', '"total_line_coverage":NaN', 1),
        )
        for baseline in baselines:
            with self.subTest(baseline=baseline[:70]):
                self.write(self.baseline, baseline)
                self.assert_rejected(self.check("jvm"), "unable to parse coverage baseline")

    def test_pending_windows_rider_baseline_fails_closed(self):
        self.write(self.reference, cobertura())
        self.write(self.current, cobertura())
        self.write(self.baseline, numeric_baseline(rider="pending_windows"))
        self.assert_rejected(self.check("rider"), "Rider baseline is pending genuine Windows coverage")

    def test_pending_status_is_forbidden_for_jvm(self):
        self.write(self.reference, kover())
        self.write(self.current, kover())
        self.write(
            self.baseline,
            numeric_baseline().replace(
                '"jvm":{"total_line_coverage":100}',
                '"jvm":{"status":"pending_windows"}',
            ),
        )
        self.assert_rejected(self.check("jvm"), "unable to parse coverage baseline")

    def test_establishes_genuine_jvm_baseline_and_marks_rider_pending(self):
        self.write(self.current, kover(lines=((1, 1), (2, 0))))
        result = self.run_checker("jvm", "--establish-baseline")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "schema_version": 1,
                "new_code_minimum": 80,
                "surfaces": {
                    "jvm": {"total_line_coverage": 50},
                    "rider": {"status": "pending_windows"},
                },
            },
            json.loads(self.baseline.read_text(encoding="utf-8")),
        )

    def test_establishing_rider_requires_windows(self):
        self.write(self.current, cobertura(lines=((1, 1), (2, 1), (3, 0))))
        self.write(self.baseline, numeric_baseline(jvm=50, rider="pending_windows"))
        self.assert_rejected(
            self.run_checker("rider", "--establish-baseline"),
            "Rider baseline establishment requires Windows",
        )

    def test_establishing_rider_on_windows_requires_jvm_and_preserves_it(self):
        self.write(self.current, cobertura(lines=((1, 1), (2, 1), (3, 0))))
        with mock.patch.object(COVERAGE_CHECKER.sys, "platform", "win32"):
            with self.assertRaisesRegex(COVERAGE_CHECKER.CoverageError, "establish the JVM baseline first"):
                COVERAGE_CHECKER.establish("rider", self.current, self.baseline)
            self.write(self.baseline, numeric_baseline(jvm=50, rider="pending_windows"))
            with redirect_stdout(io.StringIO()):
                COVERAGE_CHECKER.establish("rider", self.current, self.baseline)
        data = json.loads(self.baseline.read_text(encoding="utf-8"))
        self.assertEqual(50, data["surfaces"]["jvm"]["total_line_coverage"])
        self.assertAlmostEqual(66.66666666666667, data["surfaces"]["rider"]["total_line_coverage"])


if __name__ == "__main__":
    unittest.main()
