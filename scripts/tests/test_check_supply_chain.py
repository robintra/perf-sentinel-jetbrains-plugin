import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER = Path(__file__).parents[1] / "check-supply-chain.py"


class SupplyChainCheckerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir()
        (self.root / "gradle").mkdir()
        (self.root / "protocol").mkdir()
        (self.root / "rider-frontend").mkdir()
        for project in ("PerfSentinel.Rider", "PerfSentinel.Rider.Tests"):
            (self.root / "src" / "dotnet" / project).mkdir(parents=True)

        self.inventory = {
            "schemaVersion": 1,
            "auditedAt": "2026-08-12T07:00:00Z",
            "minimumReleaseAgeHours": 72,
            "approvedNuGetSources": [
                "https://api.nuget.org/v3/index.json",
                "https://resharper-platform.jetbrains.com/api/v2/",
            ],
            "dependencies": [
                {
                    "name": "Kotlin Gradle plugin",
                    "kind": "gradle-plugin",
                    "version": "2.4.10",
                    "releasedAt": "2026-07-14",
                    "source": "https://plugins.gradle.org/plugin/org.jetbrains.kotlin.jvm/2.4.10",
                    "declaration": "settings.gradle.kts#org.jetbrains.kotlin.jvm",
                }
            ],
            "exceptions": [],
        }
        required_actions = (
            "actions/checkout", "actions/setup-java", "actions/setup-dotnet", "actions/setup-python",
            "actions/upload-artifact", "actions/download-artifact", "actions/attest-build-provenance",
            "actions/attest", "actions/dependency-review-action", "github/codeql-action",
            "JetBrains/qodana-action", "SonarSource/sonarqube-scan-action", "anchore/sbom-action",
            "ossf/scorecard-action", "step-security/harden-runner", "google/osv-scanner-action",
            "gitleaks/gitleaks-action", "zizmorcore/zizmor-action",
            "slsa-framework/slsa-github-generator", "gradle/actions",
        )
        self.inventory["dependencies"].extend(
            {
                "name": action,
                "kind": "github-action",
                "version": f"{index:040x}",
                "release": "v1.0.0",
                "releasedAt": "2026-01-01",
                "source": f"https://github.com/{action}/releases/tag/v1.0.0",
            }
            for index, action in enumerate(required_actions, 1)
        )
        self.write_inventory()
        (self.root / "settings.gradle.kts").write_text(
            'plugins { id("org.jetbrains.kotlin.jvm") version "2.4.10" }',
            encoding="utf-8",
        )
        (self.root / "build.gradle.kts").write_text(
            'allprojects { dependencyLocking { lockAllConfigurations() } }\n'
            'implementation("example:library:1.2.3")', encoding="utf-8"
        )
        (self.root / "gradle" / "verification-metadata.xml").write_text(
            '<verification-metadata xmlns="https://schema.gradle.org/dependency-verification">'
            '<configuration><verify-metadata>true</verify-metadata>'
            '<verify-signatures>false</verify-signatures></configuration><components>'
            '<component group="example" name="library" version="1.2.3">'
            '<artifact name="library-1.2.3.jar"><sha256 value="' + "a" * 64 + '"/>'
            '</artifact></component></components></verification-metadata>',
            encoding="utf-8",
        )
        for lock in (
            self.root / "gradle.lockfile",
            self.root / "protocol" / "gradle.lockfile",
            self.root / "rider-frontend" / "gradle.lockfile",
        ):
            lock.write_text("example:library:1.2.3=runtimeClasspath\n", encoding="utf-8")
        for project in ("PerfSentinel.Rider", "PerfSentinel.Rider.Tests"):
            (self.root / "src" / "dotnet" / project / "packages.lock.json").write_text(
                json.dumps({"version": 1, "dependencies": {".NETFramework,Version=v4.7.2": {}}}),
                encoding="utf-8",
            )
        (self.root / "src" / "dotnet" / "NuGet.Config").write_text(
            """<configuration><packageSources><clear />
            <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
            <add key="jetbrains" value="https://resharper-platform.jetbrains.com/api/v2/" />
            </packageSources><packageSourceMapping>
            <packageSource key="nuget.org"><package pattern="Example.*" /></packageSource>
            <packageSource key="jetbrains"><package pattern="JetBrains.*" /></packageSource>
            </packageSourceMapping></configuration>""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_inventory(self):
        (self.root / "config" / "supply-chain.json").write_text(
            json.dumps(self.inventory), encoding="utf-8"
        )

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.root)],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message):
        result = self.run_checker()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)

    def test_accepts_complete_locked_stable_inventory(self):
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_missing_gradle_checksum(self):
        (self.root / "gradle" / "verification-metadata.xml").write_text(
            "<verification-metadata/>", encoding="utf-8"
        )
        self.assert_rejected("SHA-256 verification metadata")

    def test_rejects_unlocked_gradle_project(self):
        (self.root / "protocol" / "gradle.lockfile").unlink()
        self.assert_rejected("protocol/gradle.lockfile")

    def test_rejects_absent_nuget_lock(self):
        (self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json").unlink()
        self.assert_rejected("PerfSentinel.Rider/packages.lock.json")

    def test_rejects_prerelease_version(self):
        self.inventory["dependencies"][0]["version"] = "2.4.20-Beta2"
        self.write_inventory()
        self.assert_rejected("prerelease version")

    def test_rejects_mutable_ide_version(self):
        (self.root / "build.gradle.kts").write_text(
            "intellijPlatform { pluginVerification { ides { latest() } } }", encoding="utf-8"
        )
        self.assert_rejected("mutable IDE version")

    def test_rejects_missing_release_date(self):
        del self.inventory["dependencies"][0]["releasedAt"]
        self.write_inventory()
        self.assert_rejected("release date")

    def test_rejects_dependency_released_within_seventy_two_hours(self):
        self.inventory["dependencies"][0]["releasedAt"] = "2026-08-11"
        self.write_inventory()
        self.assert_rejected("72 hours")

    def test_rejects_compatibility_exception_over_ninety_days(self):
        self.inventory["exceptions"] = [
            {
                "type": "compatibility",
                "dependency": "kotlin",
                "owner": "Maintainers",
                "reason": "matrix incompatibility",
                "evidence": "./gradlew check",
                "upstream": "https://example.test/kotlin-release",
                "expiresAt": "2026-11-11",
            }
        ]
        self.write_inventory()
        self.assert_rejected("90 days")

    def test_rejects_compatibility_exception_without_owner(self):
        self.inventory["exceptions"] = [
            {
                "type": "compatibility",
                "dependency": "rdGen",
                "reason": "upstream Gradle 10 deprecation",
                "evidence": "./gradlew :protocol:rdgen --warning-mode all",
                "upstream": "https://example.test/rdgen-release",
                "expiresAt": "2026-11-10",
            }
        ]
        self.write_inventory()
        self.assert_rejected("owner")

    def test_rejects_expired_compatibility_exception(self):
        self.inventory["exceptions"] = [
            {
                "type": "compatibility",
                "dependency": "rdGen",
                "owner": "Maintainers",
                "reason": "upstream Gradle 10 deprecation",
                "evidence": "./gradlew :protocol:rdgen --warning-mode all",
                "upstream": "https://example.test/rdgen-release",
                "expiresAt": "2026-08-11",
            }
        ]
        self.write_inventory()
        self.assert_rejected("expired")

    def test_rejects_dynamic_gradle_version(self):
        (self.root / "build.gradle.kts").write_text(
            'implementation("x:y:1.+")', encoding="utf-8"
        )
        self.assert_rejected("dynamic version")

    def test_rejects_unapproved_nuget_source(self):
        (self.root / "src" / "dotnet" / "NuGet.Config").write_text(
            '<configuration><packageSources><add key="private" value="https://packages.example.test/v3/index.json" />'
            "</packageSources></configuration>",
            encoding="utf-8",
        )
        self.assert_rejected("unapproved NuGet source")

    def test_rejects_duplicate_inventory_keys(self):
        path = self.root / "config" / "supply-chain.json"
        path.write_text(
            json.dumps(self.inventory).replace(
                '"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1', 1
            ),
            encoding="utf-8",
        )
        self.assert_rejected("duplicate key")

    def test_rejects_unknown_nested_inventory_fields(self):
        self.inventory["dependencies"][0]["unexpected"] = True
        self.write_inventory()
        self.assert_rejected("unknown field")

    def test_rejects_wrong_inventory_types(self):
        self.inventory["schemaVersion"] = True
        self.write_inventory()
        self.assert_rejected("schemaVersion")

    def test_rejects_empty_inventory(self):
        self.inventory["dependencies"] = []
        self.write_inventory()
        self.assert_rejected("dependencies must not be empty")

    def test_rejects_weakened_release_age_policy(self):
        self.inventory["minimumReleaseAgeHours"] = 0
        self.write_inventory()
        self.assert_rejected("minimumReleaseAgeHours")

    def test_rejects_inventory_version_drift_from_declaration(self):
        self.inventory["dependencies"][0]["version"] = "2.3.21"
        self.write_inventory()
        (self.root / "settings.gradle.kts").write_text(
            'id("org.jetbrains.kotlin.jvm") version "2.4.10"\n'
            "allprojects { dependencyLocking { lockAllConfigurations() } }",
            encoding="utf-8",
        )
        self.assert_rejected("does not match declaration")

    def test_rejects_incomplete_required_action_inventory(self):
        self.inventory["dependencies"] = [
            dependency for dependency in self.inventory["dependencies"]
            if dependency["name"] != "actions/checkout"
        ]
        self.write_inventory()
        self.assert_rejected("required action")

    def test_enforces_exact_seventy_two_hours_with_timestamps(self):
        self.inventory["auditedAt"] = "2026-08-12T00:30:00Z"
        self.inventory["dependencies"][0]["releasedAt"] = "2026-08-09T01:00:00Z"
        self.write_inventory()
        self.assert_rejected("72 hours")

    def test_rejects_exception_expired_against_real_time(self):
        self.inventory["auditedAt"] = "2020-01-01T00:00:00Z"
        self.inventory["dependencies"][0]["releasedAt"] = "2019-01-01"
        self.inventory["exceptions"] = [
            {
                "type": "compatibility",
                "dependency": "rdGen",
                "owner": "Plugin maintainers",
                "reason": "upstream Gradle deprecation",
                "evidence": "./gradlew :protocol:rdgen --warning-mode all",
                "upstream": "https://example.test/rdgen-release",
                "expiresAt": "2020-02-01",
            }
        ]
        self.write_inventory()
        self.assert_rejected("expired")

    def test_rejects_metadata_artifact_without_sha256(self):
        (self.root / "gradle" / "verification-metadata.xml").write_text(
            '<verification-metadata><components><component group="x" name="y" version="1.2.3">'
            '<artifact name="y-1.2.3.jar"><sha256 value="' + "a" * 64 + '"/></artifact>'
            '<artifact name="y-1.2.3.pom"/></component></components></verification-metadata>',
            encoding="utf-8",
        )
        self.assert_rejected("artifact has no SHA-256")

    def test_rejects_unknown_verification_metadata_element(self):
        metadata = self.root / "gradle" / "verification-metadata.xml"
        metadata.write_text(metadata.read_text().replace("</verification-metadata>", "<trust-me/></verification-metadata>"))
        self.assert_rejected("verification metadata element")

    def test_rejects_malformed_gradle_lock(self):
        (self.root / "gradle.lockfile").write_text("not-a-lock\n", encoding="utf-8")
        self.assert_rejected("invalid Gradle lock entry")

    def test_rejects_locking_only_mentioned_in_comment(self):
        (self.root / "build.gradle.kts").write_text("// lockAllConfigurations()\n", encoding="utf-8")
        self.assert_rejected("does not lock all configurations")

    def test_rejects_unexcepted_prerelease_in_gradle_lock(self):
        (self.root / "gradle.lockfile").write_text(
            "org.example:library:4.0.0-M1=runtimeClasspath\n", encoding="utf-8"
        )
        self.assert_rejected("unexcepted prerelease")

    def test_rejects_exception_not_linked_to_locked_dependency(self):
        self.inventory["exceptions"] = [
            {
                "type": "transitive-prerelease",
                "dependency": "org.example:missing:1.0.0-RC1",
                "owner": "Plugin maintainers",
                "reason": "fixture",
                "evidence": "./gradlew dependencyInsight --dependency missing",
                "upstream": "https://example.test/release",
                "expiresAt": "2026-11-10",
                "lockPaths": ["gradle.lockfile"],
                "configurations": ["runtimeClasspath"],
            }
        ]
        self.write_inventory()
        self.assert_rejected("does not match a lock entry")

    def test_rejects_exception_without_evidence_upstream_or_dependency(self):
        self.inventory["exceptions"] = [
            {
                "owner": "Plugin maintainers",
                "reason": "fixture",
                "expiresAt": "2026-11-10",
            }
        ]
        self.write_inventory()
        self.assert_rejected("exception field")

    def test_rejects_nuget_config_without_clear_or_mapping(self):
        (self.root / "src" / "dotnet" / "NuGet.Config").write_text(
            '<configuration><packageSources>'
            '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" />'
            '<add key="jetbrains" value="https://resharper-platform.jetbrains.com/api/v2/" />'
            '</packageSources></configuration>',
            encoding="utf-8",
        )
        self.assert_rejected("NuGet.Config must clear inherited sources")

    def test_rejects_nuget_credentials(self):
        config = self.root / "src" / "dotnet" / "NuGet.Config"
        config.write_text(
            config.read_text().replace(
                "</configuration>",
                "<packageSourceCredentials><jetbrains><add key=\"Password\" value=\"secret\" />"
                "</jetbrains></packageSourceCredentials></configuration>",
            ),
            encoding="utf-8",
        )
        self.assert_rejected("credentials")

    def test_rejects_ambiguous_nuget_source_mapping(self):
        (self.root / "src" / "dotnet" / "NuGet.Config").write_text(
            '''<configuration><packageSources><clear />
            <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
            <add key="jetbrains" value="https://resharper-platform.jetbrains.com/api/v2/" />
            </packageSources><packageSourceMapping>
            <packageSource key="nuget.org"><package pattern="JetBrains.*" /></packageSource>
            <packageSource key="jetbrains"><package pattern="JetBrains.*" /></packageSource>
            </packageSourceMapping></configuration>''',
            encoding="utf-8",
        )
        self.assert_rejected("ambiguous NuGet mapping")

    def test_rejects_non_lock_nuget_json(self):
        lock = self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json"
        lock.write_text('{"not": "a lock"}', encoding="utf-8")
        self.assert_rejected("invalid NuGet lock")

    def test_rejects_nuget_dependency_without_content_hash(self):
        lock = self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json"
        lock.write_text(
            json.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        ".NETFramework,Version=v4.7.2": {
                            "Example": {"type": "Direct", "requested": "[1.2.3, )", "resolved": "1.2.3"}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assert_rejected("contentHash")

    def test_rejects_rider_version_drift_in_secondary_declarations(self):
        self.inventory["dependencies"].append(
            {
                "name": "Rider 2025.3",
                "kind": "jetbrains-product",
                "version": "2025.3.5",
                "releasedAt": "2026-07-30",
                "source": "https://data.services.jetbrains.com/products/releases?code=RD&type=release",
                "declaration": "build.gradle.kts#Rider 2025.3",
            }
        )
        self.write_inventory()
        (self.root / "build.gradle.kts").write_text(
            'allprojects { dependencyLocking { lockAllConfigurations() } }\n'
            'val runRider = intellijPlatformTesting.runIde.register("runRider") {'
            '\n type = IntelliJPlatformType.Rider\n version = "2025.3.4"\n}\n'
            'create(IntelliJPlatformType.Rider, "2025.3.5")',
            encoding="utf-8",
        )
        (self.root / "rider-frontend" / "build.gradle.kts").write_text(
            'intellijPlatform { rider("2025.3.5") }', encoding="utf-8"
        )
        self.assert_rejected("Rider declarations diverge")

    def test_rejects_rider_drift_when_run_block_properties_are_reordered(self):
        self.inventory["dependencies"].append(
            {
                "name": "Rider 2025.3", "kind": "jetbrains-product", "version": "2025.3.5",
                "releasedAt": "2026-07-30",
                "source": "https://data.services.jetbrains.com/products/releases?code=RD&type=release",
                "declaration": "build.gradle.kts#Rider 2025.3",
            }
        )
        self.write_inventory()
        (self.root / "rider-frontend" / "build.gradle.kts").write_text(
            'intellijPlatform { rider("2025.3.5") }', encoding="utf-8"
        )
        variants = (
            'type = IntelliJPlatformType.Rider\n splitMode = false\n version = "2025.3.4.1"',
            'version = "2025.3.4.1"\n useInstaller = providers.provider { false }\n type = IntelliJPlatformType.Rider',
        )
        for body in variants:
            with self.subTest(body=body):
                (self.root / "build.gradle.kts").write_text(
                    'allprojects { dependencyLocking { lockAllConfigurations() } }\n'
                    'create(IntelliJPlatformType.Rider, "2025.3.5")\n'
                    'val other = intellijPlatformTesting.runIde.register("other") {\n'
                    ' type = IntelliJPlatformType.IntellijIdea\n version = "2025.3.5"\n}\n'
                    'val runRider = intellijPlatformTesting.runIde.register("runRider") {\n'
                    + body + '\n}\n',
                    encoding="utf-8",
                )
                self.assert_rejected("Rider declarations diverge")

    def test_rejects_rider_drift_after_nested_kotlin_block_comment(self):
        self.inventory["dependencies"].append(
            {
                "name": "Rider 2025.3", "kind": "jetbrains-product", "version": "2025.3.5",
                "releasedAt": "2026-07-30",
                "source": "https://data.services.jetbrains.com/products/releases?code=RD&type=release",
                "declaration": "build.gradle.kts#Rider 2025.3",
            }
        )
        self.write_inventory()
        (self.root / "build.gradle.kts").write_text(
            'allprojects { dependencyLocking { lockAllConfigurations() } }\n'
            'create(IntelliJPlatformType.Rider, "2025.3.5")\n'
            'val runRider = intellijPlatformTesting.runIde.register("runRider") {\n'
            ' type = IntelliJPlatformType.Rider\n'
            ' /* outer { /* nested */ } */\n'
            ' version = "2025.3.4.1"\n}\n',
            encoding="utf-8",
        )
        (self.root / "rider-frontend" / "build.gradle.kts").write_text(
            'intellijPlatform { rider("2025.3.5") }', encoding="utf-8"
        )
        self.assert_rejected("Rider declarations diverge")

    def test_rejects_prerelease_resolved_nuget_dependency(self):
        lock = self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json"
        lock.write_text(
            json.dumps({"version": 1, "dependencies": {"net472": {"Example": {
                "type": "Transitive", "resolved": "2.0.0-RC1", "contentHash": "A" * 86 + "=="
            }}}}), encoding="utf-8"
        )
        self.assert_rejected("prerelease in NuGet lock")

    def test_rejects_dynamic_resolved_nuget_dependency(self):
        lock = self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json"
        lock.write_text(
            json.dumps({"version": 1, "dependencies": {"net472": {"Example": {
                "type": "Transitive", "resolved": "2.+", "contentHash": "A" * 86 + "=="
            }}}}), encoding="utf-8"
        )
        self.assert_rejected("dynamic version in NuGet lock")

    def test_rejects_wrong_verification_metadata_root(self):
        metadata = self.root / "gradle" / "verification-metadata.xml"
        metadata.write_text(metadata.read_text().replace("verification-metadata", "trust-metadata"))
        self.assert_rejected("verification metadata root")

    def test_rejects_missing_or_disabled_verification_configuration(self):
        metadata = self.root / "gradle" / "verification-metadata.xml"
        metadata.write_text(metadata.read_text().replace("<verify-metadata>true</verify-metadata>", "<verify-metadata>false</verify-metadata>"))
        self.assert_rejected("verify-metadata must be true")

    def test_rejects_duplicate_verification_component_or_artifact(self):
        metadata = self.root / "gradle" / "verification-metadata.xml"
        component = '<component group="example" name="library" version="1.2.3"><artifact name="library-1.2.3.jar"><sha256 value="' + "b" * 64 + '"/></artifact></component>'
        metadata.write_text(metadata.read_text().replace("</components>", component + "</components>"))
        self.assert_rejected("duplicate verification component")

    def test_rejects_multiple_sha256_values_for_one_artifact(self):
        metadata = self.root / "gradle" / "verification-metadata.xml"
        original = metadata.read_text()
        for value in ("a" * 64, "b" * 64):
            with self.subTest(value=value):
                metadata.write_text(original.replace(
                    "</artifact>", f'<sha256 value="{value}"/></artifact>', 1
                ))
                self.assert_rejected("exactly one SHA-256")

    def test_rejects_duplicate_gradle_lock_coordinate(self):
        lock = self.root / "gradle.lockfile"
        lock.write_text(lock.read_text() * 2, encoding="utf-8")
        self.assert_rejected("duplicate Gradle lock coordinate")

    def test_rejects_non_sha512_nuget_content_hash(self):
        lock = self.root / "src" / "dotnet" / "PerfSentinel.Rider" / "packages.lock.json"
        lock.write_text(
            json.dumps({"version": 1, "dependencies": {"net472": {"Example": {
                "type": "Transitive", "resolved": "2.0.0", "contentHash": "YWJjZA=="
            }}}}), encoding="utf-8"
        )
        self.assert_rejected("SHA-512 contentHash")

    def test_rejects_unplanned_or_duplicate_action(self):
        self.inventory["dependencies"].append(
            {
                "name": "example/unplanned-action", "kind": "github-action",
                "version": "f" * 40, "release": "v1.0.0", "releasedAt": "2026-01-01",
                "source": "https://github.com/example/unplanned-action/releases/tag/v1.0.0",
            }
        )
        self.write_inventory()
        self.assert_rejected("unexpected action")

    def test_rejects_unplanned_audited_tool_or_container(self):
        self.inventory["dependencies"].append(
            {
                "name": "Additional Qodana image", "kind": "container",
                "version": "sha256:" + "f" * 64, "release": "2026.1",
                "releasedAt": "2026-06-15T07:58:19Z",
                "source": "https://hub.docker.com/r/jetbrains/qodana-jvm-community",
            }
        )
        self.write_inventory()
        self.assert_rejected("unexpected tool")

    def test_rejects_missing_or_extra_required_compatibility_exception(self):
        self.inventory["dependencies"].append(
            {"name": "RDGen", "kind": "maven", "version": "2026.2.5", "releasedAt": "2026-06-18", "source": "https://example.test/rdgen"}
        )
        self.write_inventory()
        self.assert_rejected("required RDGen compatibility exception")

    def test_rejects_extra_transitive_exception_even_when_it_matches_a_lock(self):
        self.inventory["dependencies"].append(
            {
                "name": "RDGen", "kind": "maven", "version": "2026.2.5",
                "releasedAt": "2026-06-18",
                "source": "https://repo.maven.apache.org/maven2/com/jetbrains/rd/rd-gen/maven-metadata.xml",
            }
        )
        self.inventory["exceptions"] = [
            {
                "type": "compatibility", "dependency": "RDGen 2026.2.5", "owner": "Maintainers",
                "reason": "upstream Gradle 10 deprecation",
                "evidence": "./gradlew :protocol:rdgen --warning-mode all",
                "upstream": "https://github.com/JetBrains/rd/releases/tag/2026.2.5",
                "expiresAt": "2026-11-10T07:00:00Z",
            },
            {
                "type": "transitive-prerelease", "dependency": "example:extra:1.0.0-M1",
                "owner": "Maintainers", "reason": "unexpected preview",
                "evidence": "./gradlew dependencyInsight --dependency example:extra --configuration testClasspath",
                "upstream": "https://example.test/extra/1.0.0-M1",
                "expiresAt": "2026-11-10T07:00:00Z", "lockPaths": ["gradle.lockfile"],
                "configurations": ["testClasspath"],
            },
        ]
        (self.root / "gradle.lockfile").write_text("example:extra:1.0.0-M1=testClasspath\n")
        self.write_inventory()
        self.assert_rejected("unexpected transitive exception")

    def test_rejects_duplicate_nuget_source_key_or_mapping_pattern(self):
        config = self.root / "src" / "dotnet" / "NuGet.Config"
        original = config.read_text()
        config.write_text(original.replace(
            '</packageSources>', '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" /></packageSources>'
        ))
        self.assert_rejected("duplicate NuGet source key")
        config.write_text(original.replace(
            '</packageSourceMapping>',
            '<packageSource key="nuget.org"><package pattern="Example.*" /></packageSource></packageSourceMapping>',
        ))
        self.assert_rejected("duplicate NuGet mapping key")

    def test_rejects_nonofficial_source_for_known_dependency(self):
        self.inventory["dependencies"][0]["source"] = "https://example.test/kotlin/2.4.10"
        self.write_inventory()
        self.assert_rejected("official source")

    def test_rejects_transitive_evidence_missing_262_wave(self):
        self.inventory["exceptions"] = [{
            "type": "transitive-prerelease", "dependency": "org.assertj:assertj-core:4.0.0-M1",
            "owner": "Maintainers", "reason": "JetBrains test frameworks 253 and 262 require it",
            "evidence": "./gradlew dependencyInsight --dependency org.assertj:assertj-core --configuration intellijPlatformTestClasspath",
            "upstream": "https://www.jetbrains.com/intellij-repository/releases/com/jetbrains/intellij/platform/test-framework/253.33813.55/test-framework-253.33813.55.pom",
            "expiresAt": "2026-11-10T07:00:00Z", "lockPaths": ["gradle.lockfile"],
            "configurations": ["intellijPlatformTestClasspath", "intellijPlatformTestClasspath_testRustRover262"],
        }]
        (self.root / "gradle.lockfile").write_text(
            "org.assertj:assertj-core:4.0.0-M1=intellijPlatformTestClasspath,intellijPlatformTestClasspath_testRustRover262\n"
        )
        self.write_inventory()
        self.assert_rejected("does not cover configuration")


if __name__ == "__main__":
    unittest.main()
