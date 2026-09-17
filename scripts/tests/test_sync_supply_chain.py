import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SYNC = Path(__file__).parents[1] / "sync-supply-chain.py"


def load_sync():
    """The script is not importable by name, so load it the way it loads the checker."""
    spec = importlib.util.spec_from_file_location("sync_supply_chain", SYNC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeedClient:
    """Serves one canned product-feed document, and records what was asked."""

    def __init__(self, document):
        self.document = document
        self.urls = []

    def json(self, url):
        self.urls.append(url)
        return self.document
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


class ChecksumClient:
    """Serves the .sha256 document JetBrains publishes next to each installer."""

    def __init__(self, sums):
        self.sums = sums
        self.urls = []

    def text(self, url):
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1].removesuffix(".sha256")
        return f"{self.sums[name]}  {name}\n"


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

    def test_a_test_mirroring_a_moved_pin_is_reported(self):
        # The mirrored copies exist so a pin cannot move without a second,
        # conscious edit. Reporting only hexadecimal SHAs left every version
        # mirror invisible, and twice let a stale one reach CI.
        mirror = self.root / "scripts" / "tests" / "test_mirror.py"
        mirror.parent.mkdir(parents=True)
        mirror.write_text('EXPECTED = "gradle-9.7.0-bin.zip"\n', encoding="utf-8")

        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("scripts/tests/test_mirror.py:1", result.stdout)

    def test_a_jetbrains_product_release_date_comes_from_the_product_feed(self):
        module = load_sync()
        client = FeedClient({"PS": [
            {"version": "2026.2.2", "date": "2026-09-03"},
            {"version": "2026.2.1", "date": "2026-08-10"},
        ]})
        dependency = {
            "name": "PhpStorm 2026.2",
            "kind": "jetbrains-product",
            "version": "2026.2.2",
            "source": "https://data.services.jetbrains.com/products/releases?code=PS&type=release",
        }

        metadata = module.online_metadata(client, module.load_checker(), dependency)

        self.assertEqual({"releasedAt": "2026-09-03"}, metadata)
        self.assertEqual([dependency["source"]], client.urls)

    def test_an_image_declaration_splits_into_release_and_digest(self):
        module = load_sync()
        digest = "sha256:" + "c" * 64
        self.assertEqual(
            {"release": "44.74.1", "version": digest},
            module.declared_fields(module.load_checker(), ".github/workflows/renovate.yml#renovate-version", f"44.74.1@{digest}"),
        )

    def test_the_renovate_image_release_date_comes_from_its_github_release(self):
        module = load_sync()
        client = FeedClient({"tag_name": "44.74.1", "published_at": "2026-09-09T23:48:34Z"})
        dependency = {
            "name": "Renovate image", "kind": "container", "version": "sha256:" + "c" * 64,
            "release": "44.74.1", "source": "https://hub.docker.com/r/renovate/renovate",
        }

        metadata = module.online_metadata(client, module.load_checker(), dependency)

        self.assertEqual({"releasedAt": "2026-09-09T23:48:34Z"}, metadata)
        self.assertEqual(["https://api.github.com/repos/renovatebot/renovate/releases/tags/44.74.1"], client.urls)

    def write_verifier_pin(self, version):
        """A verifier-only product, as the lock file and the metadata hold it."""
        (self.root / "gradle").mkdir(exist_ok=True)
        (self.root / "gradle.lockfile").write_text(
            f"webide:PhpStorm:{version}=intellijPluginVerifierIdesDependency\n", encoding="utf-8"
        )
        (self.root / "gradle" / "verification-metadata.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<verification-metadata xmlns="https://schema.gradle.org/dependency-verification">\n'
            "   <components>\n"
            f'      <component group="webide" name="PhpStorm" version="{version}">\n'
            f'         <artifact name="PhpStorm-{version}-aarch64.dmg">\n'
            '            <sha256 value="aa" origin="Verified from JetBrains checksum"/>\n'
            "         </artifact>\n"
            f'         <artifact name="PhpStorm-{version}.tar.gz">\n'
            '            <sha256 value="bb" origin="Verified from JetBrains checksum"/>\n'
            "         </artifact>\n"
            "      </component>\n"
            "   </components>\n"
            "</verification-metadata>\n",
            encoding="utf-8",
        )

    def phpstorm_move(self):
        dependency = {
            "name": "PhpStorm 2026.2",
            "kind": "jetbrains-product",
            "version": "2026.2.2",
            "source": "https://data.services.jetbrains.com/products/releases?code=PS&type=release",
        }
        return [(dependency, "version", "2026.2.1", "2026.2.2")]

    def test_the_lock_file_follows_a_verifier_only_pin(self):
        module = load_sync()
        self.write_verifier_pin("2026.2.1")
        client = ChecksumClient({
            "PhpStorm-2026.2.2-aarch64.dmg": "c" * 64,
            "PhpStorm-2026.2.2.tar.gz": "d" * 64,
        })
        problems = []

        module.derived_writes(self.root, client, self.phpstorm_move(), problems)

        self.assertEqual([], problems)
        self.assertEqual(
            "webide:PhpStorm:2026.2.2=intellijPluginVerifierIdesDependency\n",
            (self.root / "gradle.lockfile").read_text(encoding="utf-8"),
        )

    def test_the_verification_metadata_follows_with_the_published_checksums(self):
        module = load_sync()
        self.write_verifier_pin("2026.2.1")
        client = ChecksumClient({
            "PhpStorm-2026.2.2-aarch64.dmg": "c" * 64,
            "PhpStorm-2026.2.2.tar.gz": "d" * 64,
        })
        problems = []

        module.derived_writes(self.root, client, self.phpstorm_move(), problems)

        metadata = (self.root / "gradle" / "verification-metadata.xml").read_text(encoding="utf-8")
        self.assertEqual([], problems)
        self.assertIn('<component group="webide" name="PhpStorm" version="2026.2.2">', metadata)
        self.assertIn('<artifact name="PhpStorm-2026.2.2.tar.gz">', metadata)
        self.assertIn(f'<sha256 value="{"d" * 64}" origin="Verified from JetBrains checksum"/>', metadata)
        self.assertNotIn("2026.2.1", metadata)
        self.assertEqual(
            [
                "https://download.jetbrains.com/webide/PhpStorm-2026.2.2-aarch64.dmg.sha256",
                "https://download.jetbrains.com/webide/PhpStorm-2026.2.2.tar.gz.sha256",
            ],
            client.urls,
        )

    def test_a_product_a_test_ide_also_consumes_is_refused(self):
        # RustRover feeds both the verifier and testRustRover262. Moving the
        # installer line alone would leave the platform coordinates behind, so
        # the lock needs a full Gradle regeneration and this must not pretend
        # otherwise.
        module = load_sync()
        (self.root / "gradle").mkdir(exist_ok=True)
        (self.root / "gradle.lockfile").write_text(
            "com.jetbrains.intellij.rustrover:RustRover:2026.2.1=intellijPlatformClasspath_testRustRover262\n"
            "rustrover:RustRover:2026.2.1=intellijPluginVerifierIdesDependency\n",
            encoding="utf-8",
        )
        before = (self.root / "gradle.lockfile").read_text(encoding="utf-8")
        dependency = {"name": "RustRover 2026.2", "kind": "jetbrains-product", "version": "2026.2.2"}
        problems = []

        written = module.derived_writes(
            self.root, None, [(dependency, "version", "2026.2.1", "2026.2.2")], problems
        )

        self.assertEqual([], written)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("RustRover 2026.2", problems[0])
        self.assertEqual(before, (self.root / "gradle.lockfile").read_text(encoding="utf-8"))

    def test_an_installer_other_configurations_resolve_is_refused(self):
        # IntelliJ IDEA 2025.3 is the compile platform, not just a verifier
        # target: moving it drags the bundled module and every platform
        # coordinate with it.
        module = load_sync()
        (self.root / "gradle").mkdir(exist_ok=True)
        (self.root / "gradle.lockfile").write_text(
            "idea:idea:2025.3.6.1=compileClasspath,intellijPlatformClasspath,intellijPluginVerifierIdesDependency\n",
            encoding="utf-8",
        )
        before = (self.root / "gradle.lockfile").read_text(encoding="utf-8")
        dependency = {"name": "IntelliJ IDEA 2025.3", "kind": "jetbrains-product", "version": "2025.3.7"}
        problems = []

        written = module.derived_writes(
            self.root, None, [(dependency, "version", "2025.3.6.1", "2025.3.7")], problems
        )

        self.assertEqual([], written)
        self.assertIn("IntelliJ IDEA 2025.3", problems[0])
        self.assertEqual(before, (self.root / "gradle.lockfile").read_text(encoding="utf-8"))

    def test_an_offline_run_leaves_the_derived_artifacts_alone(self):
        # The checksums only exist upstream, so offline the script must not
        # move a lock file it cannot finish, and must say why.
        self.write_verifier_pin("2026.2.1")
        before = (self.root / "gradle.lockfile").read_text(encoding="utf-8")

        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, (self.root / "gradle.lockfile").read_text(encoding="utf-8"))
        self.assertIn("--online", result.stdout)

    def test_disagreeing_workflow_pins_are_reported_not_guessed(self):
        self.write_workflow(NEW_SHA, "c" * 40)

        result = self.run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("actions/checkout: workflows pin", result.stderr)
        self.assertEqual(OLD_SHA, self.entry("actions/checkout")["version"])


if __name__ == "__main__":
    unittest.main()
