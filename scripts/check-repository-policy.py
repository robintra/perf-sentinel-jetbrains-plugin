#!/usr/bin/env python3
"""Report GitHub repository-policy drift without changing remote settings."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


LIST = "list"
NULLABLE = "nullable"
SECRET_REFERENCE = re.compile(r"\$\{\{\s*secrets\s*\.\s*([A-Z][A-Z0-9_]*)\s*\}\}")
SECRET_TOKEN = re.compile(r"(?<![A-Za-z0-9_])secrets(?![A-Za-z0-9_])", re.IGNORECASE)

SETTINGS = {
    "allow_squash_merge": True,
    "allow_rebase_merge": True,
    "allow_merge_commit": False,
    "allow_auto_merge": False,
    "delete_branch_on_merge": True,
}
SECURITY = {
    "secret_scanning": True,
    "secret_scanning_push_protection": True,
    "vulnerability_alerts": True,
    "private_vulnerability_reporting": True,
}
SECRETS = {
    "CERTIFICATE_CHAIN", "PRIVATE_KEY", "PRIVATE_KEY_PASSWORD",
    "PUBLISH_TOKEN", "QODANA_TOKEN",
}

CHECK_SCHEMA = {"context": str}
ACTOR_SCHEMA = {"actor_id": (NULLABLE, int), "actor_type": str, "bypass_mode": str}
# The sole documented bypass: the repository-admin role, so the maintainer can push to the
# default branch directly. Any other actor, team, app or deploy key stays refused.
ADMIN_BYPASS = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
PR_SCHEMA = {
    "allowed_merge_methods": (LIST, str),
    "dismiss_stale_reviews_on_push": bool,
    "require_code_owner_review": bool,
    "require_last_push_approval": bool,
    "required_approving_review_count": int,
    "required_review_thread_resolution": bool,
}
STATUS_SCHEMA = {
    "do_not_enforce_on_create": bool,
    "strict_required_status_checks_policy": bool,
    "required_status_checks": (LIST, CHECK_SCHEMA),
}
POLICY_SCHEMA = {
    "schema_version": int,
    "repository": str,
    "visibility": str,
    "default_branch": str,
    "repository_settings": {name: bool for name in SETTINGS},
    "security": {name: bool for name in SECURITY},
    "branch_ruleset": {
        "ref_include": str,
        "required_approving_review_count": int,
        "required_review_thread_resolution": bool,
        "dismiss_stale_reviews_on_push": bool,
        "require_code_owner_review": bool,
        "require_last_push_approval": bool,
        "strict_required_status_checks_policy": bool,
        "do_not_enforce_on_create": bool,
        "allowed_merge_methods": (LIST, str),
        "required_status_checks": (LIST, CHECK_SCHEMA),
        "require_linear_history": bool,
        "require_signed_commits": bool,
        "allow_force_pushes": bool,
        "allow_deletions": bool,
        "bypass_actors": (LIST, ACTOR_SCHEMA),
    },
    "tag_ruleset": {
        "ref_include": str,
        "allow_updates": bool,
        "allow_deletions": bool,
        "bypass_actors": (LIST, ACTOR_SCHEMA),
    },
    "release_environment": {
        "name": str,
        "minimum_required_reviewers": int,
        "prevent_self_review": bool,
    },
    "workflow_secrets": (LIST, str),
}
REPOSITORY_SCHEMA = {
    "full_name": str, "visibility": str, "private": bool, "default_branch": str,
    **{name: bool for name in SETTINGS},
    "security_and_analysis": (NULLABLE, {
        "secret_scanning": {"status": str},
        "secret_scanning_push_protection": {"status": str},
    }),
}
RULESET_SUMMARIES_SCHEMA = (LIST, {"id": int})
RULESET_SCHEMA = {
    "id": int,
    "target": str,
    "enforcement": str,
    "bypass_actors": (LIST, ACTOR_SCHEMA),
    "conditions": {"ref_name": {"include": (LIST, str), "exclude": (LIST, str)}},
    "rules": (LIST, dict),
}
ENVIRONMENT_SCHEMA = {
    "name": str,
    "protection_rules": (LIST, {
        "id": int,
        "type": str,
        "prevent_self_review": bool,
        "reviewers": (LIST, {"type": str, "reviewer": {"id": int, "name": str}}),
    }),
}


class PolicyError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"unable to read {path}: {error}") from error


def shape(value, schema, label):
    if isinstance(schema, dict):
        if type(value) is not dict or set(value) != set(schema):
            raise PolicyError(f"{label}: expected exact object fields")
        for key, child in schema.items():
            shape(value[key], child, f"{label}.{key}")
    elif isinstance(schema, tuple) and schema[0] == LIST:
        if type(value) is not list:
            raise PolicyError(f"{label}: expected array")
        for index, item in enumerate(value):
            shape(item, schema[1], f"{label}[{index}]")
    elif isinstance(schema, tuple) and schema[0] == NULLABLE:
        if value is not None:
            shape(value, schema[1], label)
    elif type(value) is not schema:
        raise PolicyError(f"{label}: expected {schema.__name__}")


def validate_policy(value):
    shape(value, POLICY_SCHEMA, "policy schema")
    branch = value["branch_ruleset"]
    canonical = (
        value["schema_version"] == 1
        and value["visibility"] == "public"
        and value["default_branch"] == "main"
        and value["repository_settings"] == SETTINGS
        and value["security"] == SECURITY
        and branch["ref_include"] == "~DEFAULT_BRANCH"
        and branch["required_approving_review_count"] == 0
        and branch["required_review_thread_resolution"] is True
        and branch["dismiss_stale_reviews_on_push"] is True
        and branch["require_code_owner_review"] is False
        and branch["require_last_push_approval"] is False
        and branch["strict_required_status_checks_policy"] is True
        and branch["do_not_enforce_on_create"] is False
        and sorted(branch["allowed_merge_methods"]) == ["rebase", "squash"]
        and branch["required_status_checks"] == [{"context": "CI / Gate"}]
        and branch["require_linear_history"] is True
        and branch["require_signed_commits"] is True
        and branch["allow_force_pushes"] is False
        and branch["allow_deletions"] is False
        and branch["bypass_actors"] == ADMIN_BYPASS
        and value["tag_ruleset"] == {
            "ref_include": "refs/tags/v*", "allow_updates": False,
            "allow_deletions": False, "bypass_actors": [],
        }
        and value["release_environment"] == {
            "name": "jetbrains-release", "minimum_required_reviewers": 1,
            "prevent_self_review": False,
        }
        and len(value["workflow_secrets"]) == len(set(value["workflow_secrets"]))
        and set(value["workflow_secrets"]) == SECRETS
    )
    if not canonical:
        raise PolicyError("policy schema contains a non-canonical value")


def selected(value, names, label):
    if type(value) is not dict or not set(names) <= set(value):
        raise PolicyError(f"{label}: missing fields")
    return {name: value[name] for name in names}


def normalize_repository(value):
    if (
        type(value) is dict
        and value.get("visibility") == "private"
        and value.get("private") is True
        and "security_and_analysis" not in value
    ):
        value = {**value, "security_and_analysis": None}
    result = selected(value, REPOSITORY_SCHEMA, "repository")
    security = result["security_and_analysis"]
    if security is not None:
        security = selected(security, ("secret_scanning", "secret_scanning_push_protection"), "security")
        security = {name: selected(item, ("status",), name) for name, item in security.items()}
        result["security_and_analysis"] = security
    return result


def normalize_rule(value):
    if type(value) is not dict or type(value.get("type")) is not str:
        raise PolicyError("rule lacks a type")
    kind = value["type"]
    if kind in {"required_linear_history", "required_signatures", "non_fast_forward", "update", "deletion"}:
        return selected(value, ("type",), "simple rule")
    schema = PR_SCHEMA if kind == "pull_request" else STATUS_SCHEMA if kind == "required_status_checks" else None
    if schema is None:
        raise PolicyError(f"unsupported rule {kind}")
    result = selected(value, ("type", "parameters"), kind)
    result["parameters"] = selected(result["parameters"], schema, f"{kind} parameters")
    if kind == "required_status_checks":
        result["parameters"]["required_status_checks"] = [
            selected(check, ("context",), "required status check")
            for check in result["parameters"]["required_status_checks"]
        ]
    return result


def normalize_ruleset(value):
    result = selected(value, RULESET_SCHEMA, "ruleset")
    result["bypass_actors"] = [selected(item, ACTOR_SCHEMA, "bypass actor") for item in result["bypass_actors"]]
    conditions = selected(result["conditions"], ("ref_name",), "conditions")
    conditions["ref_name"] = selected(conditions["ref_name"], ("include", "exclude"), "ref condition")
    result["conditions"] = conditions
    result["rules"] = [normalize_rule(rule) for rule in result["rules"]]
    return result


def validate_ruleset(value, label):
    shape(value, RULESET_SCHEMA, label)
    for index, rule in enumerate(value["rules"]):
        if type(rule) is not dict or type(rule.get("type")) is not str:
            raise PolicyError(f"{label}.rules[{index}]: invalid rule")
        kind = rule["type"]
        if kind in {"required_linear_history", "required_signatures", "non_fast_forward", "update", "deletion"}:
            shape(rule, {"type": str}, f"{label}.rules[{index}]")
        elif kind == "pull_request":
            shape(rule, {"type": str, "parameters": PR_SCHEMA}, f"{label}.rules[{index}]")
        elif kind == "required_status_checks":
            shape(rule, {"type": str, "parameters": STATUS_SCHEMA}, f"{label}.rules[{index}]")
        else:
            raise PolicyError(f"{label}.rules[{index}]: unsupported rule {kind}")


def normalize_environment(value):
    result = selected(value, ("name", "protection_rules"), "environment")
    rules = []
    for value in result["protection_rules"]:
        rule = selected(value, ("id", "type", "prevent_self_review", "reviewers"), "environment rule")
        reviewers = []
        for entry in rule["reviewers"]:
            entry = selected(entry, ("type", "reviewer"), "reviewer")
            reviewer = entry["reviewer"]
            if type(reviewer) is not dict:
                raise PolicyError("reviewer identity is invalid")
            name = reviewer.get("name") or (
                reviewer.get("login") if entry["type"] == "User" else reviewer.get("slug")
            )
            reviewers.append({"type": entry["type"], "reviewer": {"id": reviewer.get("id"), "name": name}})
        rule["reviewers"] = reviewers
        rules.append(rule)
    result["protection_rules"] = rules
    return result


class GitHubApi:
    def __init__(self, repository, fixture):
        self.repository = repository
        self.fixture = load_json(fixture) if fixture else None

    def fixture_key(self, endpoint, page):
        base = f"repos/{self.repository}"
        if endpoint == base:
            return "repository"
        if endpoint == f"{base}/vulnerability-alerts":
            return "vulnerability_alerts"
        if endpoint == f"{base}/private-vulnerability-reporting":
            return "private_vulnerability_reporting"
        if endpoint == f"{base}/rulesets":
            return f"rulesets:{page}"
        if endpoint.startswith(f"{base}/rulesets/"):
            return f"ruleset:{endpoint.rsplit('/', 1)[1]}"
        if endpoint == f"{base}/environments/{quote('jetbrains-release', safe='')}":
            return "environment"
        raise PolicyError(f"unrecognized endpoint {endpoint}")

    def fetch(self, endpoint, *, page=None, schema=None, validator=None, normalize=lambda value: value, status=200):
        key = self.fixture_key(endpoint, page)
        if self.fixture is not None:
            response = self.fixture.get(key)
            if type(response) is not dict or set(response) != {"status", "body"} or type(response["status"]) is not int:
                raise PolicyError(f"normalized API schema {key}: invalid response wrapper")
            if response["status"] != status:
                raise PolicyError(f"GitHub API {key} returned HTTP {response['status']}")
            value = response["body"]
        else:
            command = ["gh", "api", "--method", "GET", endpoint]
            if page is not None:
                command += ["-f", "per_page=100", "-f", f"page={page}"]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            if result.returncode:
                raise PolicyError(f"GitHub API GET {endpoint} failed: {result.stderr.strip()}")
            value = None if status == 204 else json.loads(result.stdout, object_pairs_hook=unique_object)
            value = normalize(value)
        try:
            if validator is None:
                shape(value, schema, f"normalized API schema {key}")
            else:
                validator(value, f"normalized API schema {key}")
        except (KeyError, TypeError, PolicyError) as error:
            raise PolicyError(f"normalized API schema {key}: {error}") from error
        return value

    def repository_data(self):
        return self.fetch(f"repos/{self.repository}", schema=REPOSITORY_SCHEMA, normalize=normalize_repository)

    def vulnerability_alerts(self):
        self.fetch(f"repos/{self.repository}/vulnerability-alerts", schema=type(None), status=204)

    def private_reporting(self):
        return self.fetch(f"repos/{self.repository}/private-vulnerability-reporting", schema={"enabled": bool})

    def rulesets(self):
        result = []
        for page in range(1, 101):
            items = self.fetch(f"repos/{self.repository}/rulesets", page=page, schema=RULESET_SUMMARIES_SCHEMA,
                               normalize=lambda values: [selected(item, ("id",), "ruleset summary") for item in values])
            result.extend(items)
            if len(items) < 100:
                return result
        raise PolicyError("ruleset pagination exceeded 100 pages")

    def ruleset(self, identifier):
        return self.fetch(f"repos/{self.repository}/rulesets/{identifier}", validator=validate_ruleset,
                          normalize=normalize_ruleset)

    def environment(self):
        return self.fetch(f"repos/{self.repository}/environments/{quote('jetbrains-release', safe='')}",
                          schema=ENVIRONMENT_SCHEMA, normalize=normalize_environment)


def workflow_secrets(root):
    result = set()
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        stripped = SECRET_REFERENCE.sub(lambda match: result.add(match.group(1)) or "", text)
        if SECRET_TOKEN.search(stripped):
            raise PolicyError(f"{path}: non-canonical workflow secret reference")
    return result


def inventory_secrets(root):
    value = load_json(root / "config" / "secret-inventory.json")
    entries = value.get("secrets") if type(value) is dict else None
    if type(entries) is not list:
        raise PolicyError("secret inventory is invalid")
    names = [entry.get("name") for entry in entries if type(entry) is dict]
    if any(type(name) is not str for name in names) or len(names) != len(entries) or len(names) != len(set(names)):
        raise PolicyError("secret inventory is ambiguous")
    return set(names)


def rule_map(ruleset):
    result = {}
    for rule in ruleset["rules"]:
        if rule["type"] in result:
            raise PolicyError("ruleset contains duplicate rules")
        result[rule["type"]] = rule
    return result


def active_ruleset(values, target, include):
    matches = [value for value in values if value["target"] == target and value["enforcement"] == "active"
               and value["conditions"]["ref_name"] == {"include": [include], "exclude": []}]
    if len(matches) != 1:
        raise PolicyError(f"exactly one active {target} ruleset is required")
    return matches[0]


def validate(repository, root, policy, api):
    validate_policy(policy)
    if policy["repository"] != repository:
        raise PolicyError("--repo differs from repository policy")
    errors = []
    referenced, inventoried = workflow_secrets(root), inventory_secrets(root)
    if referenced != SECRETS or inventoried != SECRETS:
        errors.append("workflow secrets differ from policy and inventory")

    data = api.repository_data()
    if data["full_name"] != repository:
        errors.append("repository identity drift")
    if data["visibility"] != "public" or data["private"] is not False:
        errors.append("repository visibility must be public")
    if data["default_branch"] != "main":
        errors.append("default branch must be main")
    for name, expected in SETTINGS.items():
        if data[name] is not expected:
            errors.append(f"repository setting {name} must be {str(expected).lower()}")
    security = data["security_and_analysis"]
    for name in ("secret_scanning", "secret_scanning_push_protection"):
        if type(security) is not dict or security[name]["status"] != "enabled":
            errors.append(f"{name} must be enabled")
    try:
        api.vulnerability_alerts()
    except PolicyError as error:
        errors.append(f"vulnerability alerts must be enabled: {error}")
    try:
        private_reporting = api.private_reporting()
    except PolicyError as error:
        errors.append(f"private vulnerability reporting must be enabled: {error}")
    else:
        if private_reporting["enabled"] is not True:
            errors.append("private vulnerability reporting must be enabled")

    summaries = api.rulesets()
    if len({item["id"] for item in summaries}) != len(summaries):
        raise PolicyError("duplicate ruleset id")
    detailed = [api.ruleset(item["id"]) for item in summaries]
    branch = active_ruleset(detailed, "branch", "~DEFAULT_BRANCH")
    rules = rule_map(branch)
    expected_types = {"required_linear_history", "required_signatures", "non_fast_forward", "deletion",
                      "pull_request", "required_status_checks"}
    if set(rules) != expected_types:
        missing = sorted(expected_types - set(rules))
        errors.append(f"default branch rules differ: {', '.join(missing)}")
    if branch["bypass_actors"] != ADMIN_BYPASS:
        errors.append("default branch bypass beyond the admin role is forbidden")
    expected_pr = {name: policy["branch_ruleset"][name] for name in PR_SCHEMA}
    actual_pr = rules.get("pull_request", {}).get("parameters")
    if type(actual_pr) is not dict:
        errors.append("pull_request rule is required")
    else:
        for name, expected in expected_pr.items():
            actual = actual_pr[name]
            if name == "allowed_merge_methods":
                actual, expected = sorted(actual), sorted(expected)
            if actual != expected:
                errors.append(f"pull_request {name} drift")
    actual_status = rules.get("required_status_checks", {}).get("parameters")
    expected_status = {name: policy["branch_ruleset"][name] for name in STATUS_SCHEMA}
    if actual_status != expected_status:
        errors.append("required status checks differ from the single CI / Gate policy")

    tag = active_ruleset(detailed, "tag", "refs/tags/v*")
    if set(rule_map(tag)) != {"update", "deletion"} or tag["bypass_actors"]:
        errors.append("release tag protection or bypass differs from policy")

    environment = api.environment()
    reviewers = [rule for rule in environment["protection_rules"] if rule["type"] == "required_reviewers"]
    valid = len(reviewers) == 1 and len(reviewers[0]["reviewers"]) >= 1 and all(
        item["type"] in {"User", "Team"} and type(item["reviewer"]["id"]) is int
        and type(item["reviewer"]["name"]) is str and bool(item["reviewer"]["name"])
        for item in reviewers[0]["reviewers"]
    )
    if not valid or reviewers[0]["prevent_self_review"] is not False:
        errors.append("jetbrains-release requires one manual approval")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        policy = load_json(arguments.root / ".github" / "repository-policy.json")
        errors = validate(arguments.repo, arguments.root, policy, GitHubApi(arguments.repo, arguments.fixture))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, PolicyError) as error:
        errors = [f"repository policy check failed closed: {error}"]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("repository policy: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
