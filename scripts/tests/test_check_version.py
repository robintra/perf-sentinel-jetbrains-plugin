import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-version.py"


class VersionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.write_contract()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_contract(self):
        (self.root / "src/main/resources/META-INF").mkdir(parents=True, exist_ok=True)
        (self.root / "gradle.properties").write_text(
            "group=io.github.robintra\nversion=0.1.0\nmarketplaceChannel=default\n",
            encoding="utf-8",
        )
        (self.root / "src/main/resources/META-INF/plugin.xml").write_text(
            "<idea-plugin><id>io.github.robintra.perfsentinel</id><version>0.1.0</version></idea-plugin>\n",
            encoding="utf-8",
        )
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-08-12\n\n- Initial release.\n",
            encoding="utf-8",
        )
        (self.root / "build.gradle.kts").write_text(
            'tasks.named<BuildPluginTask>("buildPlugin") {\n    archiveBaseName.set("perf-sentinel")\n}\n'
            'tasks.named<PublishPluginTask>("publishPlugin") {\n'
            '    channels.set(providers.gradleProperty("marketplaceChannel").map { listOf(it) })\n}\n',
            encoding="utf-8",
        )

    def run_checker(self, tag="v0.1.0"):
        return subprocess.run(
            ["python3", str(CHECKER), tag], cwd=self.root, text=True,
            capture_output=True, check=False,
        )

    def test_accepts_one_stable_v0_contract(self):
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("perf-sentinel-0.1.0.zip", result.stdout)

    def test_rejects_prerelease_and_noncanonical_tags(self):
        for tag in ("0.1.0", "v0.1.0-beta.1", "v0.1.0-RC1", "v0.1.0+build", "v1.0.0"):
            with self.subTest(tag=tag):
                result = self.run_checker(tag)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("stable tag", result.stderr)

    def test_rejects_each_version_or_release_channel_drift(self):
        cases = (
            ("gradle.properties", "version=0.1.0", "version=0.1.1", "Gradle version"),
            ("gradle.properties", "marketplaceChannel=default", "marketplaceChannel=beta", "Marketplace channel"),
            ("src/main/resources/META-INF/plugin.xml", "<version>0.1.0</version>", "<version>0.1.1</version>", "plugin manifest"),
            ("CHANGELOG.md", "## [0.1.0] - 2026-08-12", "## [0.1.1] - 2026-08-12", "changelog"),
            ("build.gradle.kts", 'archiveBaseName.set("perf-sentinel")', 'archiveBaseName.set("other")', "ZIP name"),
            ("build.gradle.kts", 'providers.gradleProperty("marketplaceChannel")', 'providers.gradleProperty("otherChannel")', "Marketplace channel"),
        )
        for relative, old, new, message in cases:
            with self.subTest(relative=relative):
                self.write_contract()
                path = self.root / relative
                path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                result = self.run_checker()
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)

    def test_rejects_duplicate_or_malformed_properties(self):
        path = self.root / "gradle.properties"
        for addition in ("version=0.1.0\n", "marketplaceChannel=default\n", "version : 0.1.0\n"):
            with self.subTest(addition=addition.strip()):
                self.write_contract()
                path.write_text(path.read_text(encoding="utf-8") + addition, encoding="utf-8")
                self.assertNotEqual(0, self.run_checker().returncode)

    def test_rejects_malformed_or_duplicate_manifest_versions(self):
        path = self.root / "src/main/resources/META-INF/plugin.xml"
        for content in (
            "<idea-plugin><version>0.1.0</idea-plugin>",
            "<idea-plugin><version>0.1.0</version><version>0.1.0</version></idea-plugin>",
            "<other><version>0.1.0</version></other>",
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertNotEqual(0, self.run_checker().returncode)

    def test_rejects_missing_duplicate_or_noncanonical_changelog_heading(self):
        path = self.root / "CHANGELOG.md"
        for content in (
            "# Changelog\n",
            "## [0.1.0]\n",
            "## [0.1.0] - 2026-02-30\n",
            "## [0.1.0] - 2026-08-12\n## [0.1.0] - 2026-08-12\n",
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertNotEqual(0, self.run_checker().returncode)

    def test_accepts_a_standard_release_link_after_the_heading(self):
        path = self.root / "CHANGELOG.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n[0.1.0]: https://github.com/robintra/perf-sentinel-jetbrains-plugin/releases/tag/v0.1.0\n",
            encoding="utf-8",
        )
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
