import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-analysis-config.py"

JVM_DIGEST = "sha256:f1c5d3efe2f550409c4d95d266c5dc2025a8069d82c9516781eae72e7383b55d"
DOTNET_DIGEST = "sha256:c893fb5f5dbe54cd4b9c2cb1bd11d711242add66c5a3ac65fe7fc302cdb8c0a3"
DOTNET_SCANNER_VERSION = "11.2.1"


def jvm_qodana():
    return f'''version: "1.0"
linter: jetbrains/qodana-jvm-community:2026.1@{JVM_DIGEST}
profile:
  path: .qodana/profiles/plugin.yaml
failureConditions:
  severityThresholds:
    critical: 0
    high: 0
exclude:
  - name: All
    paths:
      - build
      - protocol/build
      - rider-frontend/build
  # The Community image lacks optional product plugins; Plugin Verifier covers these descriptors.
  - name: PluginXmlValidity
    paths:
      - src/main/resources/META-INF/plugin.xml
      - src/main/resources/META-INF/perf-sentinel-rider.xml
  # These are CLR metadata names, not prose misspellings.
  - name: SpellCheckingInspection
    paths:
      - src/dotnet/PerfSentinel.Rider/CSharpSymbolResolver.cs
      - src/dotnet/PerfSentinel.Rider.Tests/CSharpSymbolResolverTests.cs
  # RDGen discovers this model from its package rather than a source reference.
  - name: UnusedSymbol
    paths:
      - protocol/src/main/kotlin/model/rider/PerfSentinelModel.kt
'''


def dotnet_qodana():
    return f'''version: "1.0"
linter: jetbrains/qodana-dotnet:2026.1@{DOTNET_DIGEST}
profile:
  name: qodana.recommended
onlyDirectory: src/dotnet
dotnet:
  project: src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj
  configuration: Release
failThreshold: 0
'''


def sonar_jvm():
    return '''sonar.projectKey=robintrassard_perf-sentinel-jetbrains-plugin-jvm
sonar.projectName=Perf Sentinel JetBrains Plugin JVM
sonar.sourceEncoding=UTF-8
sonar.sources=src/main/kotlin,protocol/src/main/kotlin,rider-frontend/src/main/kotlin
sonar.tests=src/test/kotlin,rider-frontend/src/test/kotlin
sonar.exclusions=build/**,protocol/build/**,rider-frontend/build/**
sonar.coverage.jacoco.xmlReportPaths=build/reports/kover/report.xml
sonar.junit.reportPaths=build/test-results/test,build/test-results/testGoLand253,build/test-results/testPhpStorm253,build/test-results/testPyCharm253,build/test-results/testRubyMine253,build/test-results/testRustRover253,build/test-results/testRustRover262,build/test-results/testWebStorm253,rider-frontend/build/test-results/test
sonar.qualitygate.wait=true
sonar.qualitygate.timeout=600
'''


def sonar_rider():
    return '''scanner.dotnet.version=11.2.1
scanner.dotnet.testProject=src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj
scanner.dotnet.configuration=Release
scanner.dotnet.lockedMode=true
scanner.dotnet.runSettings=src/dotnet/coverage.runsettings
scanner.dotnet.resultsDirectory=build/dotnet/TestResults
sonar.projectKey=robintrassard_perf-sentinel-jetbrains-plugin-rider
sonar.projectName=Perf Sentinel JetBrains Plugin Rider
sonar.sourceEncoding=UTF-8
sonar.exclusions=build/generated/rd/csharp/**,build/dotnet/**
sonar.coverage.exclusions=build/generated/rd/csharp/**
sonar.cs.cobertura.reportsPaths=build/dotnet/TestResults/**/coverage.cobertura.xml
sonar.cs.vstest.reportsPaths=build/dotnet/TestResults/**/*.trx
sonar.qualitygate.wait=true
sonar.qualitygate.timeout=600
'''


class AnalysisConfigCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "src" / "dotnet" / "PerfSentinel.Rider").mkdir(parents=True)
        (self.root / "src" / "dotnet" / "PerfSentinel.Rider.Tests").mkdir(parents=True)
        self.write("qodana.yml", jvm_qodana())
        self.write("qodana-dotnet.yml", dotnet_qodana())
        self.write("sonar-jvm.properties", sonar_jvm())
        self.write("sonar-rider.properties", sonar_rider())
        self.secret_inventory = {
            "schemaVersion": 1,
            "secrets": [
                {
                    "name": "QODANA_TOKEN",
                    "owner": "Maintainers",
                    "trustedJobScope": ["qodana-jvm", "qodana-rider"],
                    "purpose": "Authenticate trusted Qodana analysis uploads for the two isolated surfaces.",
                    "rotationProcedure": "Revoke the project token, create its replacement, and update the repository secret before re-enabling trusted analysis.",
                },
                {
                    "name": "SONAR_TOKEN",
                    "owner": "Maintainers",
                    "trustedJobScope": ["sonar-jvm", "sonar-rider"],
                    "purpose": "Authenticate trusted Sonar analysis for the two isolated projects.",
                    "rotationProcedure": "Revoke the analysis token, create its replacement, and update the repository secret before re-enabling trusted analysis.",
                },
            ],
        }
        self.write_json("config/secret-inventory.json", self.secret_inventory)
        self.supply_chain = {
            "dependencies": [
                {
                    "name": "Qodana JVM Community image",
                    "kind": "container",
                    "version": JVM_DIGEST,
                    "release": "2026.1",
                    "source": "https://hub.docker.com/r/jetbrains/qodana-jvm-community",
                    "declaration": "qodana.yml#linter",
                },
                {
                    "name": "Qodana .NET image",
                    "kind": "container",
                    "version": DOTNET_DIGEST,
                    "release": "2026.1",
                    "source": "https://hub.docker.com/r/jetbrains/qodana-dotnet",
                    "declaration": "qodana-dotnet.yml#linter",
                },
                {
                    "name": "SonarScanner for .NET",
                    "kind": "nuget",
                    "version": DOTNET_SCANNER_VERSION,
                    "source": f"https://www.nuget.org/packages/dotnet-sonarscanner/{DOTNET_SCANNER_VERSION}",
                    "declaration": "sonar-rider.properties#scanner.dotnet.version",
                },
            ]
        }
        self.write_json("config/supply-chain.json", self.supply_chain)
        self.write(
            "src/dotnet/Directory.Build.props",
            "<Project><PropertyGroup><RestoreLockedMode>true</RestoreLockedMode></PropertyGroup></Project>",
        )
        self.write(
            "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj",
            '<Project><ItemGroup><Compile Include="$(MSBuildThisFileDirectory)..\\..\\..\\build\\generated\\rd\\csharp\\**\\*.cs" /></ItemGroup></Project>',
        )
        self.write(
            "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj",
            '<Project><ItemGroup><ProjectReference Include="..\\PerfSentinel.Rider\\PerfSentinel.Rider.csproj" /></ItemGroup></Project>',
        )
        self.write(
            "src/dotnet/coverage.runsettings",
            "<RunSettings><DataCollectionRunSettings><DataCollectors><DataCollector friendlyName=\"XPlat Code Coverage\"><Configuration><Format>cobertura</Format><ExcludeByFile>**/build/generated/rd/csharp/**/*.cs</ExcludeByFile><DeterministicReport>true</DeterministicReport></Configuration></DataCollector></DataCollectors></DataCollectionRunSettings></RunSettings>",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, relative, value):
        self.write(relative, json.dumps(value))

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            cwd=REPOSITORY,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message):
        result = self.run_checker()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_independent_fail_closed_analysis_configs(self):
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_mutable_or_ineligible_qodana_images(self):
        cases = (
            ("qodana.yml", f"jetbrains/qodana-jvm-community:2026.1@{JVM_DIGEST}", "jetbrains/qodana-jvm-community:latest"),
            ("qodana-dotnet.yml", f"jetbrains/qodana-dotnet:2026.1@{DOTNET_DIGEST}", "jetbrains/qodana-dotnet:2026.2"),
        )
        for relative, pinned, mutable in cases:
            with self.subTest(relative=relative):
                original = (self.root / relative).read_text(encoding="utf-8")
                self.write(relative, original.replace(pinned, mutable))
                self.assert_rejected("immutable eligible Qodana image")
                self.write(relative, original)

    def test_rejects_broad_qodana_all_exclusions(self):
        self.write("qodana.yml", jvm_qodana().replace("      - build\n", "      - build\n      - src\n"))
        self.assert_rejected("All exclusion")

    def test_rejects_missing_qodana_suppression_rationale(self):
        self.write(
            "qodana.yml",
            jvm_qodana().replace(
                "  # These are CLR metadata names, not prose misspellings.\n",
                "",
            ),
        )
        self.assert_rejected("suppression rationale")

    def test_rejects_nonzero_or_boolean_jvm_severity_thresholds(self):
        for value in ("1", "false"):
            with self.subTest(value=value):
                self.write("qodana.yml", jvm_qodana().replace("    high: 0", f"    high: {value}"))
                self.assert_rejected("critical and high thresholds must be integer zero")

    def test_rejects_dotnet_scope_release_or_gate_drift(self):
        cases = (
            ("onlyDirectory: src/dotnet", "onlyDirectory: src"),
            ("configuration: Release", "configuration: Debug"),
            ("failThreshold: 0", "failThreshold: 1"),
        )
        for old, new in cases:
            with self.subTest(new=new):
                self.write("qodana-dotnet.yml", dotnet_qodana().replace(old, new))
                self.assert_rejected("Qodana .NET")

    def test_rejects_rider_scanner_or_locked_release_execution_drift(self):
        cases = (
            ("scanner.dotnet.version=11.2.1", "scanner.dotnet.version=latest", "pinned SonarScanner for .NET"),
            ("scanner.dotnet.configuration=Release", "scanner.dotnet.configuration=Debug", "locked Release"),
            ("scanner.dotnet.lockedMode=true", "scanner.dotnet.lockedMode=false", "locked Release"),
        )
        for old, new, message in cases:
            with self.subTest(new=new):
                self.write("sonar-rider.properties", sonar_rider().replace(old, new))
                self.assert_rejected(message)

    def test_rejects_shared_sonar_project_keys(self):
        self.write(
            "sonar-rider.properties",
            sonar_rider().replace(
                "robintrassard_perf-sentinel-jetbrains-plugin-rider",
                "robintrassard_perf-sentinel-jetbrains-plugin-jvm",
            ),
        )
        self.assert_rejected("Sonar project keys must be distinct")

    def test_rejects_missing_surface_coverage_report(self):
        cases = (
            ("sonar-jvm.properties", "sonar.coverage.jacoco.xmlReportPaths=build/reports/kover/report.xml\n", "Kover XML"),
            ("sonar-rider.properties", "sonar.cs.cobertura.reportsPaths=build/dotnet/TestResults/**/coverage.cobertura.xml\n", "Cobertura"),
        )
        for relative, line, message in cases:
            with self.subTest(relative=relative):
                original = (self.root / relative).read_text(encoding="utf-8")
                self.write(relative, original.replace(line, ""))
                self.assert_rejected(message)
                self.write(relative, original)

    def test_rejects_ignored_or_unbounded_quality_gate_waits(self):
        for relative, factory in (("sonar-jvm.properties", sonar_jvm), ("sonar-rider.properties", sonar_rider)):
            for old, new in (("sonar.qualitygate.wait=true", "sonar.qualitygate.wait=false"), ("sonar.qualitygate.timeout=600", "sonar.qualitygate.timeout=0")):
                with self.subTest(relative=relative, new=new):
                    self.write(relative, factory().replace(old, new))
                    self.assert_rejected("quality gate")

    def test_rejects_source_exclusions_outside_generated_or_build_paths(self):
        self.write(
            "sonar-jvm.properties",
            sonar_jvm().replace("rider-frontend/build/**", "rider-frontend/src/main/kotlin/**"),
        )
        self.assert_rejected("source exclusion")

    def test_rejects_drive_paths_and_post_normalization_duplicates(self):
        cases = (
            ("build/**,protocol/build/**", "C:/build/**,protocol/build/**", "stable repository path"),
            ("build/**,protocol/build/**", "build/./**,protocol/build/**", "stable repository path"),
            ("build/**,protocol/build/**", "cafe\u0301/build/**,caf\u00e9/build/**", "duplicate normalized path"),
        )
        for old, new, message in cases:
            with self.subTest(message=message):
                self.write("sonar-jvm.properties", sonar_jvm().replace(old, new))
                self.assert_rejected(message)

    def test_rejects_duplicate_or_unknown_properties(self):
        self.write("sonar-jvm.properties", sonar_jvm() + "sonar.projectKey=duplicate\n")
        self.assert_rejected("duplicate property")
        self.write("sonar-jvm.properties", sonar_jvm() + "sonar.unknown=fixture\n")
        self.assert_rejected("unknown property")

    def test_rejects_secret_inventory_schema_and_name_drift(self):
        cases = (
            (lambda value: value.update({"extra": []}), "unknown field"),
            (lambda value: value["secrets"][0].update({"enabled": True}), "unknown field"),
            (lambda value: value["secrets"][0].update({"owner": False}), "non-empty string"),
            (lambda value: value["secrets"].append(dict(value["secrets"][0], name="EXTRA_TOKEN")), "exactly SONAR_TOKEN and QODANA_TOKEN"),
        )
        for mutate, message in cases:
            with self.subTest(message=message):
                value = json.loads(json.dumps(self.secret_inventory))
                mutate(value)
                self.write_json("config/secret-inventory.json", value)
                self.assert_rejected(message)

    def test_rejects_value_like_secret_inventory_fields_or_content(self):
        value = json.loads(json.dumps(self.secret_inventory))
        value["secrets"][0]["value"] = "qodana-token-value"
        self.write_json("config/secret-inventory.json", value)
        self.assert_rejected("value-like")
        value = json.loads(json.dumps(self.secret_inventory))
        value["secrets"][0]["purpose"] = "ghp_" + "a" * 36
        self.write_json("config/secret-inventory.json", value)
        self.assert_rejected("secret-like value")

    def test_rejects_uninventoried_or_dynamic_workflow_secret_references(self):
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        for expression in (
            "${{ secrets.EXTRA_TOKEN }}",
            "${{ secrets[matrix.secret_name] }}",
            "${{ toJSON(secrets) }}",
        ):
            with self.subTest(expression=expression):
                self.write(".github/workflows/analysis.yml", f"env:\n  TOKEN: {expression}\n")
                self.assert_rejected("workflow secret")

    def test_rejects_shared_qodana_sarif_categories_when_workflows_activate_uploads(self):
        self.write(
            ".github/workflows/analysis.yml",
            """steps:
  - run: qodana scan --config qodana.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-jvm
  - run: qodana scan --config qodana-dotnet.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-jvm
""",
        )
        self.assert_rejected("distinct Qodana SARIF categories")

    def test_rejects_missing_task4_supply_chain_bindings(self):
        for dependency in ("Qodana .NET image", "SonarScanner for .NET"):
            with self.subTest(dependency=dependency):
                value = json.loads(json.dumps(self.supply_chain))
                value["dependencies"] = [item for item in value["dependencies"] if item["name"] != dependency]
                self.write_json("config/supply-chain.json", value)
                self.assert_rejected(f"{dependency} inventory binding")

    def test_rejects_rider_build_and_coverage_contract_drift(self):
        cases = (
            ("src/dotnet/Directory.Build.props", "<RestoreLockedMode>true</RestoreLockedMode>", "<RestoreLockedMode>false</RestoreLockedMode>", "locked mode"),
            ("src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj", "<Compile Include=", "<None Include=", "generated C#"),
            ("src/dotnet/coverage.runsettings", "<Format>cobertura</Format>", "<Format>opencover</Format>", "Cobertura"),
        )
        for relative, old, new, message in cases:
            with self.subTest(relative=relative):
                original = (self.root / relative).read_text(encoding="utf-8")
                self.write(relative, original.replace(old, new))
                self.assert_rejected(message)
                self.write(relative, original)

    def test_rejects_non_utf8_bom_duplicate_json_and_oversized_config(self):
        inventory = self.root / "config" / "secret-inventory.json"
        inventory.write_bytes(b"\xef\xbb\xbf" + inventory.read_bytes())
        self.assert_rejected("strict UTF-8")
        inventory.write_text('{"schemaVersion":1,"schemaVersion":1,"secrets":[]}', encoding="utf-8")
        self.assert_rejected("duplicate key")
        self.write("sonar-jvm.properties", "#" + "x" * (256 * 1024))
        self.assert_rejected("size bound")

    def test_rejects_control_characters_in_text_configs(self):
        self.write(
            "sonar-rider.properties",
            sonar_rider().replace("Plugin Rider", "Plugin\x01Rider"),
        )
        self.assert_rejected("control character")


if __name__ == "__main__":
    unittest.main()
