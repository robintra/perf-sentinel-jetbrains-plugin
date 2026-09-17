import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts/check-dependency-automation.py"
RENOVATE = REPOSITORY / ".github/renovate.json"
GLOBAL = REPOSITORY / ".github/renovate-global.json"
POLICY = REPOSITORY / "DEPENDENCY-POLICY.md"
SOURCE_FILES = (
    Path("build.gradle.kts"),
    Path("rider-frontend/build.gradle.kts"),
    Path("src/dotnet/Plugin.props"),
)


def run_checker(root=REPOSITORY):
    return subprocess.run(
        ["python3", str(CHECKER), str(root)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def make_fixture(directory):
    root = Path(directory)
    for relative in (Path(".github/renovate.json"), Path(".github/renovate-global.json"), Path("DEPENDENCY-POLICY.md"), *SOURCE_FILES):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPOSITORY / relative).read_bytes())
    return root


def run_with_mutation(directory, key, value):
    """Apply one renovate.json mutation inside a fresh fixture and run the checker."""
    root = make_fixture(directory)
    config_path = root / ".github/renovate.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config[key] = value
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return run_checker(root)


class DependencyAutomationTests(unittest.TestCase):
    def test_repository_configuration_is_valid(self):
        result = run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_renovate_owns_gradle_nuget_and_every_jetbrains_version_surface(self):
        config = json.loads(RENOVATE.read_text(encoding="utf-8"))
        self.assertEqual(
            {"gradle", "gradle-wrapper", "nuget", "custom.regex", "github-actions"},
            set(config["enabledManagers"]),
        )
        patterns = [pattern for manager in config["customManagers"] for pattern in manager["managerFilePatterns"]]
        self.assertIn("/^build\\.gradle\\.kts$/", patterns)
        self.assertIn("/^rider-frontend/build\\.gradle\\.kts$/", patterns)
        self.assertIn("/^src/dotnet/Plugin\\.props$/", patterns)
        policy = POLICY.read_text(encoding="utf-8")
        for expected in (
            "gradle/libs.versions.toml",
            "settings.gradle.kts",
            "gradle/wrapper/gradle-wrapper.properties",
            "packages.lock.json",
        ):
            self.assertIn(expected, policy)
        encoded = json.dumps(config, sort_keys=True)
        self.assertIn("github-actions", config["enabledManagers"])
        self.assertEqual(1, encoded.count("minimumReleaseAge"))

    def test_policy_is_stable_only_and_merges_matured_non_major_updates(self):
        policy = POLICY.read_text(encoding="utf-8")
        for expected in (
            "stable releases are eligible immediately",
            "Renovate owns Gradle",
            "Renovate also owns GitHub\nActions",
            "merge on their own once seven days old",
        ):
            self.assertIn(expected, policy)
        self.assertFalse((REPOSITORY / ".github/dependabot.yml").exists())
        config = RENOVATE.read_text(encoding="utf-8")
        for forbidden in ("stabilityDays", "cooldown", "automergeType"):
            self.assertNotIn(forbidden, config)

    def test_checker_rejects_missing_custom_jetbrains_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config = json.loads((root / ".github/renovate.json").read_text(encoding="utf-8"))
            config["customManagers"] = [
                manager
                for manager in config["customManagers"]
                if manager["datasourceTemplate"] != "custom.jetbrains-products"
            ]
            (root / ".github/renovate.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("JetBrains version surfaces", result.stderr)

    def test_checker_rejects_prerelease_and_ordinary_update_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config = json.loads((root / ".github/renovate.json").read_text(encoding="utf-8"))
            config["minimumReleaseAge"] = "3 days"
            config["ignoreUnstable"] = False
            (root / ".github/renovate.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("release delay is allowed only", result.stderr)
            self.assertIn("stable-only", result.stderr)

    def test_checker_rejects_automerge_outside_its_rule_and_a_returning_dependabot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config = json.loads((root / ".github/renovate.json").read_text(encoding="utf-8"))
            config["automerge"] = True
            (root / ".github/renovate.json").write_text(json.dumps(config), encoding="utf-8")
            (root / ".github/dependabot.yml").write_text('{"version": 2, "updates": []}', encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("auto-merge is allowed only", result.stderr)
            self.assertIn("Dependabot version updates must be absent", result.stderr)

    def test_catch_all_rule_disables_inherited_auto_merge(self):
        config = json.loads(RENOVATE.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "description": "Disable inherited auto-merge for every dependency",
                "matchPackageNames": ["*"],
                "automerge": False,
            },
            config["packageRules"][0],
        )

    def test_checker_rejects_unknown_or_wrongly_typed_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config = json.loads((root / ".github/renovate.json").read_text(encoding="utf-8"))
            config["prConcurrentLimit"] = True
            config["unexpected"] = "accepted"
            (root / ".github/renovate.json").write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("schema is not closed", result.stderr)
            self.assertIn("bounded", result.stderr)

    def test_checker_rejects_cross_wave_jetbrains_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config_path = root / ".github/renovate.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["packageRules"] = config["packageRules"][:1]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("compatibility wave", result.stderr)

    def test_checker_rejects_incomplete_custom_match_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config_path = root / ".github/renovate.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            rider = next(item for item in config["customManagers"] if item["depNameTemplate"] == "RD")
            rider["matchStrings"] = rider["matchStrings"][:-1]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("custom manager contract", result.stderr)

    def test_checker_rejects_duplicate_custom_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config_path = root / ".github/renovate.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["customManagers"].append(dict(config["customManagers"][0]))
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("custom manager contract", result.stderr)

    def test_checker_rejects_package_rule_drift(self):
        for mutate in (
            lambda rules: rules.append({"description": "unexpected"}),
            lambda rules: rules[0].update({"groupName": "everything"}),
            lambda rules: rules[1].update({"respectLatest": False}),
        ):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                root = make_fixture(directory)
                config_path = root / ".github/renovate.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                mutate(config["packageRules"])
                config_path.write_text(json.dumps(config), encoding="utf-8")
                result = run_checker(root)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("package rules", result.stderr)

    def test_checker_rejects_unowned_jetbrains_product_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            build = root / "build.gradle.kts"
            build.write_text(
                build.read_text(encoding="utf-8") + '\ncreate(IntelliJPlatformType.CLion, "2026.2.1")\n',
                encoding="utf-8",
            )
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("unowned JetBrains product", result.stderr)

    def test_checker_accepts_multiline_jetbrains_declaration_extracted_by_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            build = root / "build.gradle.kts"
            build.write_text(
                build.read_text(encoding="utf-8").replace(
                    'create(IntelliJPlatformType.GoLand, "2026.2.1")',
                    'create(\n    IntelliJPlatformType.GoLand,\n    "2026.2.1"\n)',
                ),
                encoding="utf-8",
            )
            result = run_checker(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_checker_rejects_multiline_rider_sdk_not_extracted_by_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            props = root / "src/dotnet/Plugin.props"
            props.write_text(
                props.read_text(encoding="utf-8").replace(
                    "<SdkVersion>2025.3.5.1</SdkVersion>",
                    "<SdkVersion>\n    2025.3.5.1\n  </SdkVersion>",
                ),
                encoding="utf-8",
            )
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("not extracted", result.stderr)

    def test_checker_recurses_into_lists_for_forbidden_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config_path = root / ".github/renovate.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["packageRules"][0]["cooldown"] = {"defaultDays": 3}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("release delays", result.stderr)

    def test_checker_rejects_malformed_nested_configuration_without_traceback(self):
        for key, value in (
            ("enabledManagers", None),
            ("customDatasources", []),
            ("customManagers", [{"depNameTemplate": []}]),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                result = run_with_mutation(directory, key, value)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn("Traceback", result.stderr)

    def test_checker_rejects_non_object_roots_without_traceback(self):
        for relative in (Path(".github/renovate.json"), Path(".github/renovate-global.json")):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = make_fixture(directory)
                (root / relative).write_text("[]", encoding="utf-8")
                result = run_checker(root)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn("Traceback", result.stderr)

    def test_security_alerts_have_one_owner(self):
        config = json.loads(RENOVATE.read_text(encoding="utf-8"))
        self.assertEqual({"enabled": False}, config["vulnerabilityAlerts"])
        self.assertIs(False, config["osvVulnerabilityAlerts"])
        self.assertIn("GitHub-native security alerts", POLICY.read_text(encoding="utf-8"))

    def test_coverlet_stays_on_the_net472_compatible_line(self):
        config = json.loads(RENOVATE.read_text(encoding="utf-8"))
        self.assertIn(
            {
                "description": "Keep Coverlet compatible with the Rider net472 test host",
                "matchPackageNames": ["coverlet.collector"],
                "allowedVersions": "<7.0.0",
            },
            config["packageRules"],
        )

    def test_checker_rejects_top_level_policy_drift(self):
        mutations = (
            ("$schema", "https://example.test/schema.json"),
            ("dependencyDashboard", False),
            ("rangeStrategy", "replace"),
        )
        for key, value in mutations:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                result = run_with_mutation(directory, key, value)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("top-level policy", result.stderr)

    def test_checker_rejects_custom_datasource_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config_path = root / ".github/renovate.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["customDatasources"]["jetbrains-products"]["transformTemplates"] = ["{}"]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("official release service", result.stderr)

    def test_matured_non_major_updates_merge_after_the_freshness_grace(self):
        spec = importlib.util.spec_from_file_location("check_supply_chain", REPOSITORY / "scripts/check-supply-chain.py")
        supply = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supply)
        rules = json.loads(RENOVATE.read_text(encoding="utf-8"))["packageRules"]
        automerge = [rule for rule in rules if rule.get("automerge") is True]
        self.assertEqual(1, len(automerge))
        self.assertEqual(["minor", "patch", "digest"], automerge[0]["matchUpdateTypes"])
        self.assertEqual(f"{supply.FRESHNESS_GRACE.days} days", automerge[0]["minimumReleaseAge"])

    def test_checker_rejects_policy_switches_drifting(self):
        for key, value, message in (
            ("lockFileMaintenance", {"enabled": True}, "lock maintenance"),
            ("platformAutomerge", True, "native auto-merge"),
            ("postUpgradeTasks", {"commands": ["bash -c env"]}, "sync hook"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                result = run_with_mutation(directory, key, value)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)

    def test_checker_rejects_a_global_config_allowing_more_than_the_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            path = root / ".github/renovate-global.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config["allowedCommands"].append(".*")
            path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("global configuration", result.stderr)

    def test_the_runner_never_automerges_itself(self):
        rules = json.loads(RENOVATE.read_text(encoding="utf-8"))["packageRules"]
        maturity_index = next(index for index, rule in enumerate(rules) if rule.get("automerge") is True)
        non_automerging_after = {
            name
            for rule in rules[maturity_index + 1:]
            if rule.get("automerge") is False
            for name in rule.get("matchPackageNames", [])
        }
        self.assertEqual(
            {
                "renovate/renovate", "renovatebot/github-action",
                "step-security/harden-runner", "actions/create-github-app-token",
                "JetBrains.ReSharper.SDK.Tests", "JetBrains.Rider.SDK", "RD",
            },
            non_automerging_after,
        )

    def test_rider_ide_and_sdks_move_together_by_hand(self):
        rules = json.loads(RENOVATE.read_text(encoding="utf-8"))["packageRules"]
        self.assertEqual(
            {
                "description": "Move the Rider IDE and SDKs together, merged by hand",
                "matchPackageNames": ["JetBrains.ReSharper.SDK.Tests", "JetBrains.Rider.SDK", "RD"],
                "groupName": "rider-ide-and-sdk",
                "automerge": False,
            },
            rules[-1],
        )


if __name__ == "__main__":
    unittest.main()
