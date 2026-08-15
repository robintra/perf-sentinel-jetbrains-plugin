import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-repository-policy.py"
REPO = "robintra/perf-sentinel-jetbrains-plugin"
SECRETS = (
    "CERTIFICATE_CHAIN",
    "PRIVATE_KEY",
    "PRIVATE_KEY_PASSWORD",
    "PUBLISH_TOKEN",
    "QODANA_TOKEN",
)


def policy():
    return {
        "schema_version": 1,
        "repository": REPO,
        "visibility": "public",
        "default_branch": "main",
        "repository_settings": {
            "allow_squash_merge": True,
            "allow_rebase_merge": True,
            "allow_merge_commit": False,
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
        },
        "security": {
            "secret_scanning": True,
            "secret_scanning_push_protection": True,
            "vulnerability_alerts": True,
            "private_vulnerability_reporting": True,
        },
        "branch_ruleset": {
            "ref_include": "~DEFAULT_BRANCH",
            "required_approving_review_count": 0,
            "required_review_thread_resolution": True,
            "dismiss_stale_reviews_on_push": True,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "strict_required_status_checks_policy": True,
            "do_not_enforce_on_create": False,
            "allowed_merge_methods": ["rebase", "squash"],
            "required_status_checks": [{"context": "CI / Gate"}],
            "require_linear_history": True,
            "require_signed_commits": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
            "bypass_actors": [
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
            ],
        },
        "tag_ruleset": {
            "ref_include": "refs/tags/v*",
            "allow_updates": False,
            "allow_deletions": False,
            "bypass_actors": [],
        },
        "release_environment": {
            "name": "jetbrains-release",
            "minimum_required_reviewers": 1,
            "prevent_self_review": False,
        },
        "workflow_secrets": list(SECRETS),
    }


def pull_request_rule():
    return {
        "type": "pull_request",
        "parameters": {
            "allowed_merge_methods": ["squash", "rebase"],
            "dismiss_stale_reviews_on_push": True,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_approving_review_count": 0,
            "required_review_thread_resolution": True,
        },
    }


def public_api_fixture():
    return {
        "repository": {
            "status": 200,
            "body": {
                "full_name": REPO,
                "visibility": "public",
                "private": False,
                "default_branch": "main",
                "allow_squash_merge": True,
                "allow_rebase_merge": True,
                "allow_merge_commit": False,
                "allow_auto_merge": False,
                "delete_branch_on_merge": True,
                "security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"},
                },
            },
        },
        "vulnerability_alerts": {"status": 204, "body": None},
        "private_vulnerability_reporting": {
            "status": 200,
            "body": {"enabled": True},
        },
        "rulesets:1": {"status": 200, "body": [{"id": 101}, {"id": 102}]},
        "rulesets:2": {"status": 200, "body": []},
        "ruleset:101": {
            "status": 200,
            "body": {
                "id": 101,
                "target": "branch",
                "enforcement": "active",
                "bypass_actors": [
                    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
                ],
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                "rules": [
                    {"type": "required_linear_history"},
                    {"type": "required_signatures"},
                    {"type": "non_fast_forward"},
                    {"type": "deletion"},
                    pull_request_rule(),
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "do_not_enforce_on_create": False,
                            "strict_required_status_checks_policy": True,
                            "required_status_checks": [{"context": "CI / Gate"}],
                        },
                    },
                ],
            },
        },
        "ruleset:102": {
            "status": 200,
            "body": {
                "id": 102,
                "target": "tag",
                "enforcement": "active",
                "bypass_actors": [],
                "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
                "rules": [{"type": "update"}, {"type": "deletion"}],
            },
        },
        "environment": {
            "status": 200,
            "body": {
                "name": "jetbrains-release",
                "protection_rules": [
                    {
                        "id": 7,
                        "type": "required_reviewers",
                        "prevent_self_review": False,
                        "reviewers": [
                            {"type": "User", "reviewer": {"id": 11, "name": "robintra"}}
                        ],
                    }
                ],
            },
        },
    }


def write_root(root, value, *, extra_secret=None):
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "repository-policy.json").write_text(json.dumps(value), encoding="utf-8")
    references = list(SECRETS)
    if extra_secret:
        references.append(extra_secret)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "env:\n" + "\n".join(f"  {name}: ${{{{ secrets.{name} }}}}" for name in references) + "\n",
        encoding="utf-8",
    )
    (root / "config").mkdir()
    (root / "config" / "secret-inventory.json").write_text(
        json.dumps({"schemaVersion": 1, "secrets": [{"name": name} for name in SECRETS]}),
        encoding="utf-8",
    )


def run_checker(api=None, *, policy_value=None, extra_secret=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_root(root, policy_value or policy(), extra_secret=extra_secret)
        fixture = root / "api.json"
        fixture.write_text(json.dumps(api or public_api_fixture()), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--repo",
                REPO,
                "--root",
                str(root),
                "--fixture",
                str(fixture),
            ],
            text=True,
            capture_output=True,
            check=False,
        )


class RepositoryPolicyTests(unittest.TestCase):
    def assert_drift(self, api, text):
        result = run_checker(api)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(text, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_the_minimal_public_policy(self):
        result = run_checker()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_requires_public_visibility_and_main(self):
        for field, value, message in (
            ("visibility", "private", "visibility"),
            ("private", True, "visibility"),
            ("default_branch", "develop", "default branch"),
        ):
            with self.subTest(field=field):
                api = public_api_fixture()
                api["repository"]["body"][field] = value
                self.assert_drift(api, message)

    def test_reports_private_security_omission_as_activation_drift(self):
        api = public_api_fixture()
        api["repository"]["body"].update(visibility="private", private=True)
        api["repository"]["body"]["security_and_analysis"] = None

        result = run_checker(api)

        self.assertEqual(1, result.returncode)
        self.assertIn("visibility", result.stderr)
        self.assertIn("secret_scanning", result.stderr)
        self.assertNotIn("missing fields", result.stderr)

    def test_requires_exact_merge_settings(self):
        expected = policy()["repository_settings"]
        for field, value in expected.items():
            with self.subTest(field=field):
                api = public_api_fixture()
                api["repository"]["body"][field] = not value
                self.assert_drift(api, field)

    def test_requires_the_single_ci_gate(self):
        for checks in ([], [{"context": "CI / Gate"}, {"context": "Qodana JVM"}]):
            with self.subTest(checks=checks):
                api = public_api_fixture()
                api["ruleset:101"]["body"]["rules"][-1]["parameters"]["required_status_checks"] = checks
                self.assert_drift(api, "status checks")

    def test_requires_signed_linear_pr_history_and_only_the_admin_bypass(self):
        for mutation, message in (
            ("required_signatures", "required_signatures"),
            ("required_linear_history", "required_linear_history"),
            ("non_fast_forward", "non_fast_forward"),
            ("deletion", "deletion"),
            ("pull_request", "pull_request"),
        ):
            with self.subTest(mutation=mutation):
                api = public_api_fixture()
                rules = api["ruleset:101"]["body"]["rules"]
                rules[:] = [rule for rule in rules if rule["type"] != mutation]
                self.assert_drift(api, message)
        api = public_api_fixture()
        api["ruleset:101"]["body"]["bypass_actors"].append(
            {"actor_id": 99, "actor_type": "Team", "bypass_mode": "always"}
        )
        self.assert_drift(api, "bypass")

    def test_requires_exact_pull_request_semantics(self):
        fields = policy()["branch_ruleset"]
        for field in (
            "required_approving_review_count",
            "required_review_thread_resolution",
            "dismiss_stale_reviews_on_push",
            "require_code_owner_review",
            "require_last_push_approval",
        ):
            with self.subTest(field=field):
                api = public_api_fixture()
                parameters = api["ruleset:101"]["body"]["rules"][4]["parameters"]
                parameters[field] = not fields[field]
                self.assert_drift(api, field)

    def test_protects_release_tags_without_bypass(self):
        for mutation in ("update", "deletion"):
            with self.subTest(mutation=mutation):
                api = public_api_fixture()
                rules = api["ruleset:102"]["body"]["rules"]
                rules[:] = [rule for rule in rules if rule["type"] != mutation]
                self.assert_drift(api, "release tag")
        api = public_api_fixture()
        api["ruleset:102"]["body"]["rules"][0] = {"type": "non_fast_forward"}
        self.assert_drift(api, "release tag")
        api = public_api_fixture()
        api["ruleset:102"]["body"]["bypass_actors"] = [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ]
        self.assert_drift(api, "release tag")

    def test_requires_public_security_features_and_reporting(self):
        for feature in ("secret_scanning", "secret_scanning_push_protection"):
            with self.subTest(feature=feature):
                api = public_api_fixture()
                api["repository"]["body"]["security_and_analysis"][feature]["status"] = "disabled"
                self.assert_drift(api, feature)
        api = public_api_fixture()
        api["vulnerability_alerts"]["status"] = 404
        self.assert_drift(api, "vulnerability alerts")
        api = public_api_fixture()
        api["private_vulnerability_reporting"]["body"]["enabled"] = False
        self.assert_drift(api, "private vulnerability reporting")
        api = public_api_fixture()
        api["private_vulnerability_reporting"]["status"] = 404
        result = run_checker(api)
        self.assertEqual(1, result.returncode)
        self.assertIn("private vulnerability reporting", result.stderr)
        self.assertIn("HTTP 404", result.stderr)

    def test_requires_the_release_environment_approval(self):
        for rules in ([], [{"id": 7, "type": "required_reviewers", "prevent_self_review": False, "reviewers": []}]):
            with self.subTest(rules=rules):
                api = public_api_fixture()
                api["environment"]["body"]["protection_rules"] = rules
                self.assert_drift(api, "jetbrains-release")

    def test_requires_exact_workflow_secret_inventory(self):
        result = run_checker(extra_secret="EXTRA_TOKEN")
        self.assertEqual(1, result.returncode)
        self.assertIn("workflow secrets", result.stderr)

    def test_closes_policy_and_normalized_api_schemas(self):
        mutations = []
        value = policy()
        value["unexpected"] = True
        mutations.append(value)
        value = policy()
        value["schema_version"] = True
        mutations.append(value)
        value = policy()
        value["branch_ruleset"]["required_approving_review_count"] = False
        mutations.append(value)
        for value in mutations:
            with self.subTest(value=value):
                result = run_checker(policy_value=value)
                self.assertEqual(1, result.returncode)
                self.assertIn("policy schema", result.stderr)
        api = public_api_fixture()
        api["repository"]["body"]["unexpected"] = True
        self.assert_drift(api, "normalized API schema")
        api = public_api_fixture()
        api["rulesets:1"]["body"][0]["id"] = True
        self.assert_drift(api, "normalized API schema")
        api = public_api_fixture()
        api["ruleset:101"]["body"]["rules"][4]["parameters"]["required_approving_review_count"] = False
        self.assert_drift(api, "normalized API schema")
        api = public_api_fixture()
        api["ruleset:101"]["body"]["rules"][-1]["parameters"]["required_status_checks"][0]["unexpected"] = True
        self.assert_drift(api, "normalized API schema")

    def test_fails_closed_on_http_errors_and_requires_bounded_pagination(self):
        api = public_api_fixture()
        api["rulesets:1"]["body"] = [{"id": identifier} for identifier in range(1, 101)]
        api["rulesets:2"]["status"] = 401
        self.assert_drift(api, "HTTP 401")
        source = CHECKER.read_text(encoding="utf-8") if CHECKER.exists() else ""
        self.assertIn("range(1, 101)", source)
        self.assertIn('"GET"', source)
        for method in ('"PUT"', '"PATCH"', '"POST"', '"DELETE"'):
            self.assertNotIn(method, source)

    def test_documents_contribution_and_private_security_reporting(self):
        expected = {
            REPOSITORY / "CONTRIBUTING.md": ("CI / Gate", "windows-2025", "Plugin Verifier"),
            REPOSITORY / "SECURITY.md": ("private vulnerability", "Do not open a public issue"),
            REPOSITORY / ".github" / "PULL_REQUEST_TEMPLATE.md": ("CI / Gate", "signed"),
            REPOSITORY / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml": ("Bug report", "Rider"),
            REPOSITORY / ".github" / "ISSUE_TEMPLATE" / "security.yml": ("private vulnerability", "public issue"),
        }
        for path, fragments in expected.items():
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8")
                for fragment in fragments:
                    self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
