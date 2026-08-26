import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY / ".github/workflows"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_JAVA = "actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961"
SETUP_DOTNET = "actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68"
SETUP_GRADLE = "gradle/actions/setup-gradle@9c971963bec38e04b3d30dcc455b5382be2fdbfb"
CODEQL = "github/codeql-action"
CODEQL_SHA = "db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28"


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
            "fetch-depth: 0",
            "persist-credentials: false",
            "scripts/check-supply-chain.py",
            "--dependency-verification strict dependencies :protocol:dependencies :rider-frontend:dependencies --configuration runtimeClasspath",
            "--locked-mode",
            "NuGetAuditMode=all",
            "google/osv-scanner-action/.github/workflows/osv-scanner-reusable.yml@6e4298ebc4db23e847df9b2e2de2939d6f066c67",
            "--config=osv-scanner.toml",
            "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e",
            "GITLEAKS_VERSION: 8.30.1",
            "zizmorcore/zizmor-action@3dc1ecc9bcb9e94e9b2c709687979e1298497054",
            "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610",
            "syft-version: v1.51.0",
            "google/osv-scanner-action/osv-scanner-action@6e4298ebc4db23e847df9b2e2de2939d6f066c67",
            "name: Enforce the SPDX package-source policy",
            "build/security/source.spdx.json",
            "ossf/scorecard-action@2d1146689b8cda280b9bc96326124645441f03bc",
        ):
            self.assertIn(expected, self.text)
        self.assertNotIn("Require package provenance in the SBOM", self.text)
        self.assertNotIn("--licenses", self.text)
        self.assertNotIn("python3 - <<'PY'", self.text)
        self.assertGreaterEqual(self.text.count("mkdir -p build/security"), 2)

    def test_runs_both_qodana_surfaces_without_exposing_tokens_to_forks(self):
        self.assertIn("name: Qodana JVM", self.text)
        self.assertIn("name: Qodana Rider", self.text)
        self.assertIn("runs-on: windows-2025", self.text)
        self.assertIn("qodana.yml", self.text)
        self.assertIn("qodana-dotnet.yml", self.text)
        self.assertIn("category: qodana-jvm", self.text)
        self.assertIn("category: qodana-rider", self.text)
        self.assertEqual(2, self.text.count("QODANA_TOKEN: ${{ secrets.QODANA_TOKEN }}"))
        rider = self.text.split("  qodana-rider:\n", 1)[1].split("\n  notify:\n", 1)[0]
        self.assertIn(SETUP_JAVA, rider)
        self.assertIn(SETUP_GRADLE, rider)
        self.assertLess(rider.index(":protocol:rdgen"), rider.index("dotnet restore"))

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
        self.assertEqual(2, self.text.count(f"{CODEQL}/init@{CODEQL_SHA}"))
        self.assertEqual(2, self.text.count(f"{CODEQL}/analyze@{CODEQL_SHA}"))
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
        self.assertIn(CHECKOUT, self.text)
        self.assertIn(SETUP_JAVA, self.text)
        self.assertIn(
            "gradle/actions/dependency-submission@9c971963bec38e04b3d30dcc455b5382be2fdbfb",
            self.text,
        )
        self.assertIn("dependency-graph: generate-and-submit", self.text)
        self.assertIn("dependency-resolution-task: dependencies :protocol:dependencies :rider-frontend:dependencies", self.text)
        self.assertIn("additional-arguments: --configuration runtimeClasspath --dependency-verification strict", self.text)
        self.assertIn("validate-wrappers: true", self.text)
        self.assertIn("persist-credentials: false", self.text)


if __name__ == "__main__":
    unittest.main()
