#!/usr/bin/env python3
"""Fail closed over the explicit CI job result set."""

import json
import sys
from pathlib import Path


JOBS = {
    "jvm", "python", "php", "rust", "ruby", "javascript", "go",
    "rider_frontend", "plugin_verifier", "zip", "dependency_review",
    "workflow_security", "sonar_jvm", "qodana_jvm",
}
ANALYSIS = {"sonar_jvm", "qodana_jvm"}


def check(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict or set(payload) != {"schema_version", "change_scope", "results"}:
        raise ValueError("CI result document has an invalid schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("CI result schema_version must be integer 1")
    scope = payload["change_scope"]
    if scope not in {"code", "docs", "fork"}:
        raise ValueError("CI change_scope is invalid")
    results = payload["results"]
    if type(results) is not dict:
        raise ValueError("CI results must be an object")
    missing, extra = JOBS - set(results), set(results) - JOBS
    if missing or extra:
        raise ValueError("CI results have missing or extra jobs: " + ", ".join(sorted(missing | extra)))
    allowed_skips = JOBS if scope == "docs" else ANALYSIS if scope == "fork" else set()
    failures = [name for name in sorted(JOBS) if results[name] != "success" and not (
        name in allowed_skips and results[name] == "skipped"
    )]
    if failures:
        raise ValueError("CI job result is not acceptable: " + ", ".join(failures))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-ci-results.py RESULTS.json", file=sys.stderr)
        return 2
    try:
        check(Path(sys.argv[1]))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("CI gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
