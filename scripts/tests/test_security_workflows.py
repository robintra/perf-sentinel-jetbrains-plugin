import json
import re
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY / ".github/workflows"


def pinned(action):
    """An action pinned to a full commit SHA. Which SHA is Renovate's to move and
    check-supply-chain.py's to hold against the inventory, so it is not repeated here."""
    return re.escape(action) + r"@[0-9a-f]{40}\b"


CHECKOUT = pinned("actions/checkout")
SETUP_JAVA = pinned("actions/setup-java")
SETUP_DOTNET = pinned("actions/setup-dotnet")
SETUP_GRADLE = pinned("gradle/actions/setup-gradle")
CODEQL = "github/codeql-action"


class DailySecurityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = WORKFLOWS / "security-audit.yml"
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_is_daily_manual_and_main_only(self):
        self.assertIn("schedule:\n    - cron: '17 5 * * *'", self.text)
        self.assertIn("push:\n    branches: [main]", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("github.event_name != 'workflow_dispatch'", self.text)

    def test_runs_locked_dependency_and_workflow_audits(self):
        for expected in (
            CHECKOUT,
            SETUP_JAVA,
            SETUP_DOTNET,
            SETUP_GRADLE,
            pinned("google/osv-scanner-action/.github/workflows/osv-scanner-reusable.yml"),
            pinned("gitleaks/gitleaks-action"),
            r"GITLEAKS_VERSION: \d+\.\d+\.\d+\n",
            pinned("zizmorcore/zizmor-action"),
            pinned("anchore/sbom-action"),
            r"syft-version: v\d+\.\d+\.\d+\n",
            pinned("google/osv-scanner-action/osv-scanner-action"),
            pinned("ossf/scorecard-action"),
        ):
            self.assertRegex(self.text, expected)
        for expected in (
            "fetch-depth: 0",
            "persist-credentials: false",
            "scripts/check-supply-chain.py",
            "--dependency-verification strict dependencies :protocol:dependencies :rider-frontend:dependencies --configuration runtimeClasspath",
            "--locked-mode",
            "NuGetAuditMode=all",
            "--config=osv-scanner.toml",
            "name: Enforce the SPDX package-source policy",
            "build/security/source.spdx.json",
        ):
            self.assertIn(expected, self.text)
        self.assertNotIn("Require package provenance in the SBOM", self.text)
        self.assertNotIn("--licenses", self.text)
        self.assertNotIn("python3 - <<'PY'", self.text)
        self.assertGreaterEqual(self.text.count("mkdir -p build/security"), 2)

    def test_runs_the_qodana_surface_without_exposing_tokens_to_forks(self):
        # The .NET surface left with the Community licence: qodana-dotnet is an
        # Ultimate linter, and CodeQL already analyses csharp.
        self.assertIn("name: Qodana JVM", self.text)
        self.assertIn("qodana.yml", self.text)
        self.assertIn("category: qodana-jvm", self.text)
        self.assertNotIn("qodana-rider", self.text)
        self.assertEqual(1, self.text.count("QODANA_TOKEN: ${{ secrets.QODANA_TOKEN }}"))

    def test_scheduled_failure_reconciles_one_sanitized_issue(self):
        self.assertIn("name: Reconcile scheduled audit alert", self.text)
        self.assertIn("state: 'all'", self.text)
        self.assertIn("pull_request", self.text)
        self.assertIn("[Security Audit] scheduled failure", self.text)
        self.assertIn("issues: write", self.text)
        self.assertIn("getLabel", self.text)
        self.assertIn("createLabel", self.text)
        self.assertIn("name: 'security-audit'", self.text)
        for forbidden in ("sarif", "QODANA_TOKEN"):
            notify = self.text.split("  notify:\n", 1)[1]
            self.assertNotIn(forbidden, notify)


class SupplyChainFreshnessWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "supply-chain-freshness.yml").read_text(encoding="utf-8")
        cls.audit, cls.notify = cls.text.split("\n  notify:\n", 1)

    def test_drift_is_reconciled_into_one_issue_instead_of_a_red_run(self):
        # check-supply-chain.py exits 2 when every error is drift; only then is
        # the run green, and the issue carries the list of pins behind.
        self.assertIn('[ "$exit_status" -ne 2 ]', self.audit)
        self.assertIn("name: Reconcile supply-chain drift", self.notify)
        self.assertIn("[Supply Chain] pins behind upstream", self.notify)
        self.assertIn("state: 'all'", self.notify)
        self.assertIn("createLabel", self.notify)

    def test_only_the_notify_job_can_write_issues(self):
        self.assertNotIn("issues: write", self.audit)
        self.assertIn("issues: write", self.notify)

    def test_the_drift_list_reaches_the_script_through_the_environment(self):
        # Interpolating ${{ }} into a github-script body is template injection;
        # the list must arrive as an environment variable and be read there.
        script = self.notify.split("script: |", 1)[1]
        self.assertNotIn("${{", script)
        self.assertIn("process.env.DRIFT", script)


class RenovateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "renovate.yml").read_text(encoding="utf-8")
        cls.triggers = cls.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        cls.global_config = json.loads((REPOSITORY / ".github/renovate-global.json").read_text(encoding="utf-8"))

    def test_never_runs_on_a_pull_request(self):
        self.assertIn("schedule:", self.triggers)
        self.assertIn("workflow_dispatch:", self.triggers)
        self.assertNotIn("pull_request", self.triggers)

    def test_the_app_key_is_reachable_only_through_the_renovate_environment(self):
        self.assertIn("    environment: renovate\n", self.text)
        self.assertEqual({"RENOVATE_APP_ID", "RENOVATE_APP_PRIVATE_KEY"}, set(re.findall(r"secrets\.(\w+)", self.text)))

    def test_only_the_app_token_writes_and_only_what_renovate_needs(self):
        self.assertIn("permissions:\n  contents: read\n", self.text)
        self.assertEqual(
            {"checks": "read", "contents": "write", "issues": "write", "metadata": "read",
             "pull-requests": "write", "statuses": "write", "workflows": "write"},
            dict(re.findall(r"^\s+permission-([a-z-]+): (\w+)$", self.text, re.M)),
        )
        self.assertEqual(5, len(re.findall(r": write$", self.text, re.M)))

    def test_runs_the_digest_pinned_image_behind_harden_runner(self):
        self.assertIn("renovate-image: renovate/renovate\n", self.text)
        self.assertRegex(self.text, r"renovate-version: \d+\.\d+\.\d+@sha256:[0-9a-f]{64}\n")
        self.assertIn("step-security/harden-runner@", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_the_hook_gets_the_read_only_workflow_token_not_the_app_token(self):
        self.assertIn("""RENOVATE_CUSTOM_ENV_VARIABLES: '{"GITHUB_TOKEN": "${{ github.token }}"}'""", self.text)
        self.assertIn("token: ${{ steps.app-token.outputs.token }}", self.text)

    def test_global_config_allows_only_the_sync_hook(self):
        self.assertEqual(["^python3 scripts/sync-supply-chain\\.py --online$"], self.global_config["allowedCommands"])
        self.assertNotIn("dryRun", self.global_config)


class CodeQLWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")

    def test_runs_daily_on_main_and_pull_requests(self):
        self.assertIn("pull_request:\n    branches: [main]", self.text)
        self.assertIn("push:\n    branches: [main]", self.text)
        self.assertIn("schedule:\n    - cron: '41 5 * * *'", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("github.event_name != 'workflow_dispatch'", self.text)

    def test_has_manual_java_kotlin_and_csharp_builds(self):
        self.assertIn("languages: java-kotlin", self.text)
        self.assertIn("languages: csharp", self.text)
        self.assertEqual(2, len(re.findall(pinned(f"{CODEQL}/init"), self.text)))
        self.assertEqual(2, len(re.findall(pinned(f"{CODEQL}/analyze"), self.text)))
        self.assertEqual(2, self.text.count("build-mode: manual"))
        self.assertEqual(2, self.text.count("queries: +security-extended"))
        self.assertIn("gradle --no-daemon --no-build-cache --dependency-verification strict compileKotlin :protocol:rdgen :rider-frontend:compileKotlin", self.text)
        self.assertIn("dotnet restore src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj --locked-mode", self.text)
        self.assertIn("dotnet build src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj --configuration Release --no-restore", self.text)
        self.assertIn("category: /language:java-kotlin", self.text)
        self.assertIn("category: /language:csharp", self.text)

    def test_fork_pull_requests_use_only_read_permissions_for_uploads(self):
        self.assertIn("security-events: write", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("contents: write", self.text)


class DependencySubmissionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "dependency-submission.yml").read_text(encoding="utf-8")

    def test_is_trusted_main_and_schedule_only(self):
        self.assertIn("push:\n    branches: [main]", self.text)
        self.assertIn("schedule:\n    - cron: '53 5 * * *'", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertIn("contents: write", self.text)
        self.assertNotIn("if: github.ref == 'refs/heads/main'", self.text)

    def test_submits_the_strict_gradle_graph_at_full_sha(self):
        self.assertRegex(self.text, CHECKOUT)
        self.assertRegex(self.text, SETUP_JAVA)
        self.assertRegex(self.text, pinned("gradle/actions/dependency-submission"))
        self.assertIn("dependency-graph: generate-and-submit", self.text)
        self.assertIn("dependency-resolution-task: dependencies :protocol:dependencies :rider-frontend:dependencies", self.text)
        self.assertIn("additional-arguments: --configuration runtimeClasspath --dependency-verification strict", self.text)
        self.assertIn("validate-wrappers: true", self.text)
        self.assertIn("persist-credentials: false", self.text)


class JdkPinTests(unittest.TestCase):
    """A floating JDK changes Build-JVM in every jar manifest, so the archive stops being reproducible."""

    def test_every_workflow_reads_the_pinned_build_from_the_version_file(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if not re.search(SETUP_JAVA, text):
                continue
            self.assertIn("java-version-file: .java-version", text, path.name)
            self.assertNotIn("java-version:", text, path.name)

    def test_the_version_file_pins_an_exact_temurin_build(self):
        # setup-java resolves Temurin against the Adoptium semver namespace, where an LTS
        # release reads 21.0.12+8.0.LTS. A bare 21.0.12+8 matches nothing.
        version = (REPOSITORY / ".java-version").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^21\.\d+\.\d+\+\d+\.\d+\.LTS$")


if __name__ == "__main__":
    unittest.main()
