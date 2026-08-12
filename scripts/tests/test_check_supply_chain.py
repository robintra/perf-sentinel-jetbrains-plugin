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
            "auditedAt": "2026-08-12",
            "approvedNuGetSources": [
                "https://api.nuget.org/v3/index.json",
                "https://resharper-platform.jetbrains.com/api/v2/",
            ],
            "dependencies": [
                {
                    "name": "kotlin",
                    "version": "2.4.10",
                    "releasedAt": "2026-07-14",
                    "source": "https://kotlinlang.org/docs/releases.html",
                }
            ],
            "exceptions": [],
        }
        self.write_inventory()
        (self.root / "settings.gradle.kts").write_text(
            'allprojects { dependencyLocking { lockAllConfigurations() } }',
            encoding="utf-8",
        )
        (self.root / "build.gradle.kts").write_text(
            'implementation("example:library:1.2.3")', encoding="utf-8"
        )
        (self.root / "gradle" / "verification-metadata.xml").write_text(
            '<verification-metadata><component><artifact name="library-1.2.3.jar">'
            '<sha256 value="' + "a" * 64 + '"/></artifact></component></verification-metadata>',
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
            </packageSources></configuration>""",
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
                "dependency": "kotlin",
                "reason": "matrix incompatibility",
                "expiresAt": "2026-11-11",
            }
        ]
        self.write_inventory()
        self.assert_rejected("90 days")

    def test_rejects_compatibility_exception_without_owner(self):
        self.inventory["exceptions"] = [
            {
                "dependency": "rdGen",
                "reason": "upstream Gradle 10 deprecation",
                "expiresAt": "2026-11-10",
            }
        ]
        self.write_inventory()
        self.assert_rejected("owner")

    def test_rejects_expired_compatibility_exception(self):
        self.inventory["exceptions"] = [
            {
                "dependency": "rdGen",
                "owner": "JetBrains RD",
                "reason": "upstream Gradle 10 deprecation",
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


if __name__ == "__main__":
    unittest.main()
