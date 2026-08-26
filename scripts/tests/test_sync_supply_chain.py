import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SYNC = Path(__file__).parents[1] / "sync-supply-chain.py"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


class SyncSupplyChainTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir()
        (self.root / "gradle" / "wrapper").mkdir(parents=True)
        (self.root / ".github" / "workflows").mkdir(parents=True)

        (self.root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
            "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.7.1-bin.zip\n",
            encoding="utf-8",
        )
        self.write_workflow(NEW_SHA)
        self.write_inventory(version="9.7.0", action_sha=OLD_SHA)

    def write_workflow(self, *shas):
        steps = "\n".join(
            f"      - uses: actions/checkout@{sha}" for sha in shas
        )
        (self.root / ".github" / "workflows" / "ci.yml").write_text(
            f"jobs:\n  build:\n    steps:\n{steps}\n", encoding="utf-8"
        )

    def write_inventory(self, *, version, action_sha):
        self.inventory_path = self.root / "config" / "supply-chain.json"
        self.inventory_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "auditedAt": "2026-01-01T00:00:00Z",
                    "dependencies": [
                        {
                            "name": "Gradle",
                            "kind": "build-tool",
                            "version": version,
                            "releasedAt": "2026-08-06T14:07:35Z",
                            "source": "https://services.gradle.org/versions/current",
                            "declaration": "gradle/wrapper/gradle-wrapper.properties#distributionUrl",
                        },
                        {
                            "name": "actions/checkout",
                            "kind": "github-action",
                            "version": action_sha,
                            "release": "v7.0.1",
                            "releasedAt": "2026-08-06T14:07:35Z",
                            "source": "https://github.com/actions/checkout/releases/tag/v7.0.1",
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def run_sync(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SYNC), "--root", str(self.root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def inventory(self):
        return json.loads(self.inventory_path.read_text(encoding="utf-8"))

    def entry(self, name):
        return next(
            item for item in self.inventory()["dependencies"] if item["name"] == name
        )

    def test_check_reports_both_drifts_and_writes_nothing(self):
        before = self.inventory_path.read_text(encoding="utf-8")

        result = self.run_sync("--check")

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Gradle: version 9.7.0 -> 9.7.1", result.stdout)
        self.assertIn(f"actions/checkout: version {OLD_SHA} -> {NEW_SHA}", result.stdout)
        self.assertEqual(before, self.inventory_path.read_text(encoding="utf-8"))

    def test_sync_follows_the_declaration_and_the_workflow_pin(self):
        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("9.7.1", self.entry("Gradle")["version"])
        self.assertEqual(NEW_SHA, self.entry("actions/checkout")["version"])
        self.assertNotEqual("2026-01-01T00:00:00Z", self.inventory()["auditedAt"])
        # Release dates need the official sources, so an offline run must not
        # invent one and must say so.
        self.assertEqual("2026-08-06T14:07:35Z", self.entry("Gradle")["releasedAt"])
        self.assertIn("--online", result.stdout)

    def test_a_second_run_changes_nothing(self):
        self.assertEqual(0, self.run_sync().returncode)
        settled = self.inventory_path.read_text(encoding="utf-8")

        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("already matches", result.stdout)
        self.assertEqual(settled, self.inventory_path.read_text(encoding="utf-8"))

    def test_disagreeing_workflow_pins_are_reported_not_guessed(self):
        self.write_workflow(NEW_SHA, "c" * 40)

        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("actions/checkout: workflows pin", result.stderr)
        self.assertEqual(OLD_SHA, self.entry("actions/checkout")["version"])


if __name__ == "__main__":
    unittest.main()
