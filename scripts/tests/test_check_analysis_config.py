import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-analysis-config.py"

JVM_DIGEST = "sha256:8ff36b5cebc0a6d720f77dcf3e0a94a03c39b4c42c3724a99ce5f7e462e42f99"
DOTNET_DIGEST = "sha256:083e222c54d976b29a3118036559340a18e804f82d30947548468443ca60de59"


def jvm_qodana():
    return f'''version: "1.0"
linter: jetbrains/qodana-jvm-community:2026.2@{JVM_DIGEST}
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
  # Gradle does not serve its own XSD, so the schemaLocation this file declares cannot resolve.
  - name: XmlHighlighting
    paths:
      - gradle/verification-metadata.xml
'''


def dotnet_qodana():
    return '''version: "1.0"
linter: qodana-dotnet
withinDocker: false
profile:
  name: qodana.recommended
onlyDirectory: src/dotnet
dotnet:
  project: src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj
  configuration: Release
failThreshold: 0
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
                *[
                    {
                        "name": name,
                        "owner": "Maintainers",
                        "trustedJobScope": ["jetbrains-release"],
                        "purpose": f"Provide protected JetBrains release material for {name}.",
                        "rotationProcedure": f"Replace {name} in the protected jetbrains-release environment and verify a dry-run before publication.",
                    }
                    for name in (
                        "CERTIFICATE_CHAIN",
                        "PRIVATE_KEY",
                        "PRIVATE_KEY_PASSWORD",
                        "PUBLISH_TOKEN",
                    )
                ],
            ],
        }
        self.write_json("config/secret-inventory.json", self.secret_inventory)
        self.supply_chain = {
            "dependencies": [
                {
                    "name": "Qodana JVM Community image",
                    "kind": "container",
                    "version": JVM_DIGEST,
                    "release": "2026.2",
                    "source": "https://hub.docker.com/r/jetbrains/qodana-jvm-community",
                    "declaration": "qodana.yml#linter",
                },
                {
                    "name": "Qodana .NET image",
                    "kind": "container",
                    "version": DOTNET_DIGEST,
                    "release": "2026.2",
                    "source": "https://hub.docker.com/r/jetbrains/qodana-dotnet",
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
        self.write(
            "qodana.yml",
            jvm_qodana().replace(
                f"jetbrains/qodana-jvm-community:2026.2@{JVM_DIGEST}",
                "jetbrains/qodana-jvm-community:latest",
            ),
        )
        self.assert_rejected("immutable eligible Qodana image")

    def test_requires_native_qodana_for_net472(self):
        cases = (
            ("withinDocker: false", "withinDocker: true"),
            ("linter: qodana-dotnet", f"linter: jetbrains/qodana-dotnet:2026.2@{DOTNET_DIGEST}"),
        )
        for old, new in cases:
            with self.subTest(new=new):
                self.write("qodana-dotnet.yml", dotnet_qodana().replace(old, new))
                self.assert_rejected("native mode")

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

    def test_rejects_drive_paths_in_exclusions(self):
        cases = (
            ("      - build\n", "      - C:/build\n", "stable repository path"),
            ("      - build\n", "      - build/./\n", "stable repository path"),
        )
        for old, new, message in cases:
            with self.subTest(message=message):
                self.write("qodana.yml", jvm_qodana().replace(old, new))
                self.assert_rejected(message)

    def test_rejects_secret_inventory_schema_and_name_drift(self):
        cases = (
            (lambda value: value.update({"extra": []}), "unknown field"),
            (lambda value: value["secrets"][0].update({"enabled": True}), "unknown field"),
            (lambda value: value["secrets"][0].update({"owner": False}), "non-empty string"),
            (lambda value: value["secrets"].append(dict(value["secrets"][0], name="EXTRA_TOKEN")), "exact secret set"),
            (lambda value: value["secrets"][2].update({"trustedJobScope": ["ci"]}), "trusted-job scope"),
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
            "${{ secrets }}",
            "${{ secrets . EXTRA_TOKEN }}",
            "${{ toJSON(secrets && secrets) }}",
            "${{ '}}' && secrets.EXTRA_TOKEN }}",
            "${{ format('}}{0}', secrets.EXTRA_TOKEN) }}",
            "${{ Secrets.EXTRA_TOKEN }}",
        ):
            with self.subTest(expression=expression):
                self.write(".github/workflows/analysis.yml", f"env:\n  TOKEN: {expression}\n")
                self.assert_rejected("workflow secret")

    def test_accepts_static_inventoried_workflow_secret_references(self):
        self.write(
            ".github/workflows/analysis.yml",
            "env:\n  QODANA_TOKEN: ${{ secrets.QODANA_TOKEN }}\n",
        )
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_ignores_secrets_word_inside_workflow_expression_string(self):
        self.write(".github/workflows/analysis.yml", "env:\n  LABEL: ${{ 'secrets' }}\n")
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

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

    def test_accepts_qodana_uploads_bound_to_distinct_surface_categories(self):
        self.write(
            ".github/workflows/analysis.yml",
            """steps:
  - run: qodana scan --config qodana.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      sarif_file: build/qodana-jvm/results/qodana.sarif.json
      category: qodana-jvm
  - run: qodana scan --config qodana-dotnet.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      sarif_file: build/qodana-rider/results/qodana.sarif.json
      category: qodana-rider
""",
        )
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_swapped_qodana_sarif_category_bindings(self):
        self.write(
            ".github/workflows/analysis.yml",
            """steps:
  - run: qodana scan --config qodana.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-rider
  - run: qodana scan --config qodana-dotnet.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-jvm
""",
        )
        self.assert_rejected("distinct Qodana SARIF categories")

    def test_rejects_unbound_qodana_sarif_category_after_another_upload(self):
        self.write(
            ".github/workflows/analysis.yml",
            """steps:
  - run: qodana scan --config qodana.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-rider
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-jvm
  - run: qodana scan --config qodana-dotnet.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-rider
""",
        )
        self.assert_rejected("distinct Qodana SARIF categories")

    def test_rejects_qodana_sarif_category_outside_upload_with_block(self):
        self.write(
            ".github/workflows/analysis.yml",
            """steps:
  - run: qodana scan --config qodana.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      sarif_file: build/qodana-jvm/results/qodana.sarif.json
    env:
      category: qodana-jvm
  - run: qodana scan --config qodana-dotnet.yml
  - uses: github/codeql-action/upload-sarif@0123456789012345678901234567890123456789
    with:
      category: qodana-rider
""",
        )
        self.assert_rejected("distinct Qodana SARIF categories")

    def test_rejects_missing_task4_supply_chain_bindings(self):
        for dependency in ("Qodana .NET image",):
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
        self.write("qodana.yml", "#" + "x" * (256 * 1024))
        self.assert_rejected("size bound")

    def test_rejects_control_characters_in_text_configs(self):
        self.write("qodana.yml", jvm_qodana().replace("  - name: All", "  - name: A\x01ll"))
        self.assert_rejected("control character")


if __name__ == "__main__":
    unittest.main()
