import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts/check-ci-results.py"
WORKFLOW = REPOSITORY / ".github/workflows/ci.yml"
BUILD = REPOSITORY / "build.gradle.kts"


class CiResultCheckerTests(unittest.TestCase):
    @staticmethod
    def run_checker(payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), str(path)],
                text=True,
                capture_output=True,
                check=False,
            )

    @staticmethod
    def success_payload() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "change_scope": "code",
            "results": {
                "jvm": "success",
                "python": "success",
                "php": "success",
                "rust": "success",
                "ruby": "success",
                "javascript": "success",
                "go": "success",
                "rider_frontend": "success",
                "plugin_verifier": "success",
                "zip": "success",
                "dependency_review": "success",
                "workflow_security": "success",
                "qodana_jvm": "success",
                "rider_windows": "success",
            },
        }

    def test_accepts_complete_success(self):
        result = self.run_checker(self.success_payload())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("CI gate: OK", result.stdout)

    def test_rejects_failure_cancellation_missing_or_unknown_results(self):
        for mutation in ("failure", "cancelled", None, "neutral"):
            with self.subTest(mutation=mutation):
                payload = self.success_payload()
                if mutation is None:
                    del payload["results"]["go"]
                else:
                    payload["results"]["go"] = mutation
                result = self.run_checker(payload)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("go", result.stderr)

    def test_allows_only_explicit_analysis_skips_for_forks(self):
        payload = self.success_payload()
        payload["change_scope"] = "fork"
        payload["results"]["qodana_jvm"] = "skipped"
        result = self.run_checker(payload)
        self.assertEqual(0, result.returncode, result.stderr)
        payload["results"]["python"] = "skipped"
        result = self.run_checker(payload)
        self.assertNotEqual(0, result.returncode)

    def test_allows_all_jobs_to_skip_only_for_documentation(self):
        payload = self.success_payload()
        payload["change_scope"] = "docs"
        payload["results"] = dict.fromkeys(payload["results"], "skipped")
        result = self.run_checker(payload)
        self.assertEqual(0, result.returncode, result.stderr)
        payload["change_scope"] = "code"
        result = self.run_checker(payload)
        self.assertNotEqual(0, result.returncode)

    def test_rejects_open_or_malformed_payloads(self):
        cases = (
            {"schema_version": True, "change_scope": "code", "results": {}},
            {**self.success_payload(), "extra": True},
            {**self.success_payload(), "change_scope": "unknown"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.run_checker(payload)
                self.assertNotEqual(0, result.returncode)


class CiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""
        self.build = BUILD.read_text(encoding="utf-8")

    def test_has_visible_language_product_and_aggregate_jobs(self):
        for job in (
            "changes", "jvm", "python", "php", "rust", "ruby", "javascript", "go",
            "rider-frontend", "plugin-verifier", "zip", "dependency-review",
            "workflow-security", "qodana-jvm", "gate",
        ):
            self.assertIn(f"  {job}:\n", self.text)
        self.assertIn("name: CI", self.text)
        self.assertIn("name: CI / Gate", self.text)
        self.assertIn("if: always()", self.text)
        self.assertIn("scripts/check-ci-results.py", self.text)
        self.assertEqual(16, self.text.count("verifier-target:"))
        self.assertIn("-PpluginVerifierTarget=", self.text)
        self.assertIn("cache-disabled: true", self.text)

    def test_plugin_verifier_targets_are_closed_and_bounded(self):
        targets = (
            "idea-253", "idea-262", "rider-253", "rider-262",
            "python-253", "python-262", "php-253", "php-262",
            "rust-253", "rust-262", "ruby-253", "ruby-262",
            "web-253", "web-262", "go-253", "go-262",
        )
        for target in targets:
            self.assertIn(f'"{target}"', self.build)
            self.assertIn(f"verifier-target: {target}", self.text)
        self.assertIn('gradleProperty("pluginVerifierTarget")', self.build)
        self.assertIn("unknown pluginVerifierTarget", self.build)
        self.assertIn("deactivateDependencyLocking()", self.build)

    def test_uses_pinned_actions_and_read_only_default_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        checkout_count = self.text.count("uses: actions/checkout@")
        self.assertEqual(checkout_count, self.text.count("persist-credentials: false"))
        for line in self.text.splitlines():
            if "uses:" in line:
                reference = line.split("uses:", 1)[1].strip()
                self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
        self.assertNotIn("pull_request_target:", self.text)

    def test_trusted_analysis_does_not_expose_secrets_to_forks(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("QODANA_TOKEN: ${{ secrets.QODANA_TOKEN }}", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_dependency_review_falls_back_until_the_repository_is_public(self):
        job = self.text.split("  dependency-review:\n", 1)[1].split(
            "\n  workflow-security:\n", 1
        )[0]
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.repository.private == false",
            job,
        )
        self.assertIn(
            "github.event_name != 'pull_request' || github.event.repository.private == true",
            job,
        )
        self.assertIn("python3 scripts/check-supply-chain.py", job)

    def test_manual_trusted_analysis_is_bound_to_an_exact_head_sha(self):
        self.assertIn("target-sha: ${{ github.sha }}", self.text)
        self.assertIn("ref: ${{ github.sha }}", self.text)
        self.assertNotIn("inputs.head_sha", self.text)
        self.assertGreaterEqual(
            self.text.count("ref: ${{ needs.changes.outputs.target-sha }}"),
            14,
        )
        self.assertIn('if [[ "$EVENT_NAME" == workflow_dispatch ]]; then', self.text)
        self.assertIn('if [[ "$scope" == code && "$EVENT_NAME" == pull_request', self.text)
        self.assertIn("fetch-depth: 0", self.text)

    def test_fork_metadata_is_passed_through_environment(self):
        self.assertIn("PR_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}", self.text)
        self.assertIn('"$PR_HEAD_REPOSITORY" != "$REPOSITORY"', self.text)

    def test_analysis_consumes_coverage_and_workflow_security_runs_real_tools(self):
        self.assertIn("name: jvm-analysis-inputs", self.text)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", self.text)
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", self.text)
        self.assertIn("zizmorcore/zizmor-action@70fb788f84895a7701f5643d103d587e460b5c99", self.text)
        self.assertIn("version: 1.30.0", self.text)
        self.assertIn("actionlint_1.7.12_linux_amd64.tar.gz", self.text)
        self.assertIn("8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8", self.text)
        self.assertIn("ruff-x86_64-unknown-linux-gnu.tar.gz", self.text)
        self.assertIn("65b8bae7e43f12a91b71036a52176012b3aefb725d5ae263e2771474110a0983", self.text)
        self.assertIn("./ruff-x86_64-unknown-linux-gnu/ruff check scripts tools", self.text)
        self.assertIn("gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e", self.text)
        workflow_security = self.text.split("  workflow-security:\n", 1)[1].split("\n  qodana-jvm:\n", 1)[0]
        self.assertIn("fetch-depth: 0", workflow_security)
        self.assertIn("permissions:\n      contents: read\n      pull-requests: read", workflow_security)
        self.assertNotIn("pull-requests: write", workflow_security)
        self.assertIn("GITLEAKS_VERSION: 8.30.1", workflow_security)

    def test_pr_caches_sarif_permission_and_zip_path_are_bounded(self):
        setup_count = self.text.count("uses: gradle/actions/setup-gradle@")
        self.assertGreaterEqual(setup_count, 9)
        self.assertEqual(setup_count - 3, self.text.count("cache-read-only: ${{ github.event_name == 'pull_request' }}"))
        self.assertEqual(3, self.text.count("cache-disabled: true"))
        qodana = self.text.split("  qodana-jvm:\n", 1)[1].split("\n  gate:\n", 1)[0]
        self.assertIn("security-events: write", qodana)
        self.assertNotIn("security-events: write", self.text.split("  qodana-jvm:\n", 1)[0])
        self.assertIn("find build/distributions", self.text)
        self.assertNotIn("github.event.repository.default_branch", self.text)


if __name__ == "__main__":
    unittest.main()
