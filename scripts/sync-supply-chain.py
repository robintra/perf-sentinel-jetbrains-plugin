#!/usr/bin/env python3
"""Bring config/supply-chain.json back in step with the repository.

check-supply-chain.py refuses an inventory that disagrees with what the
repository declares, and nothing writes that inventory. A Renovate or
Dependabot pull request therefore bumps a manifest and fails the gate until
somebody rewrites the matching entry by hand. This performs that rewrite,
resolving every declaration through check-supply-chain.py itself so the two
cannot drift apart.

Offline it refreshes what the working tree already proves: the version behind
each `declaration`, and the commit SHA the workflows pin for each action.
`--online` also refreshes the tag, release date, source URL and Gradle
checksum from the endpoints the checker validates against.

The SHAs mirrored in scripts/tests are deliberately left alone. Those tests
exist so that a pin cannot move without a second, conscious edit, and a script
rewriting both sides of the comparison would leave them asserting nothing.
They are reported instead.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"
    r"(?:/[^@\s]+)?@(?P<sha>[0-9a-f]{40})"
)
CREATED = re.compile(r"Created\s+(\d{1,2}\s+\w+\s+\d{4})\.")
INSTANT = "%Y-%m-%dT%H:%M:%SZ"


def load_checker():
    """Import check-supply-chain.py, the single reader of the declarations.

    Resolved next to this file rather than under --root: the checker is part of
    the tooling, while --root is the repository being synced.
    """
    path = Path(__file__).resolve().parent / "check-supply-chain.py"
    spec = importlib.util.spec_from_file_location("check_supply_chain", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared_fields(declaration: str, actual: str) -> dict[str, str]:
    """The fields check-supply-chain compares for one declaration."""
    if declaration == "qodana.yml#linter":
        release, _, digest = actual.partition("@")
        return {"release": release, "version": digest}
    if declaration.endswith("#sha256"):
        return {"sha256": actual}
    return {"version": actual}


def declaration_changes(root, checker, dependencies, problems):
    supported = checker.DIRECT_DECLARATIONS | checker.OPTIONAL_DIRECT_DECLARATIONS
    changes = []
    for dependency in dependencies:
        declaration = dependency.get("declaration")
        if not declaration or declaration not in supported:
            continue
        try:
            values = checker.declared_versions(root, declaration)
        except Exception as error:  # noqa: BLE001 - reported, never silent
            problems.append(f"{dependency['name']}: cannot read {declaration}: {error}")
            continue
        distinct = set(values)
        if len(distinct) != 1:
            # No value, or several declarations of one dependency that disagree.
            # The checker reports exactly that, and picking a winner here would
            # hide a real divergence behind a green gate.
            problems.append(f"{dependency['name']}: {declaration} resolves to {values}")
            continue
        for field, value in declared_fields(declaration, distinct.pop()).items():
            if dependency.get(field) != value:
                changes.append((dependency, field, dependency.get(field), value))
    return changes


def workflow_pins(root: Path) -> dict[str, set[str]]:
    """Commit SHA each workflow pins, keyed by owner/repo."""
    pins: dict[str, set[str]] = {}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES.match(line)
            if match:
                pins.setdefault(match["repo"], set()).add(match["sha"])
    return pins


def action_changes(root, dependencies, problems):
    pins = workflow_pins(root)
    changes = []
    for dependency in dependencies:
        if dependency.get("kind") != "github-action":
            continue
        shas = pins.get(dependency["name"], set())
        if len(shas) != 1:
            problems.append(
                f"{dependency['name']}: workflows pin {sorted(shas) or 'nothing'}"
            )
            continue
        sha = shas.pop()
        if dependency.get("version") != sha:
            changes.append((dependency, "version", dependency.get("version"), sha))
    return changes


def github_release(client, repo: str, sha: str) -> tuple[str, str]:
    """Tag pointing at `sha`, and when that release was published."""
    tags = client.json(f"https://api.github.com/repos/{repo}/tags?per_page=100")
    tag = next((item["name"] for item in tags if item["commit"]["sha"] == sha), None)
    if tag is None:
        raise ValueError(f"no tag in the first 100 points at {sha}")
    release = client.json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return tag, release["published_at"]


def instant(value: str) -> str:
    """An upstream timestamp in the Z form the inventory records."""
    return value.replace("+00:00", "Z")


def gradle_metadata(client, dependency) -> dict[str, str]:
    data = client.json("https://services.gradle.org/versions/current")
    built = datetime.strptime(data["buildTime"], "%Y%m%d%H%M%S%z").astimezone(UTC)
    checksum = "wrapperChecksum" if dependency["name"].endswith("wrapper JAR") else "checksum"
    # The wrapper JAR entry is compared on its checksum alone, so its version
    # has no declaration to follow. check-supply-chain requires Gradle to be the
    # current release, which makes services.gradle.org authoritative for both.
    return {
        "version": data["version"],
        "releasedAt": instant(built.isoformat()),
        "sha256": data[checksum],
    }


def nuget_published(client, checker, dependency) -> dict[str, str]:
    package = checker.NUGET_PACKAGES[dependency["name"]].lower()
    index = client.json(
        f"https://api.nuget.org/v3/registration5-semver1/{package}/index.json"
    )
    for page in index["items"]:
        for item in page.get("items") or client.json(page["@id"])["items"]:
            entry = item["catalogEntry"]
            catalog = entry if isinstance(entry, dict) else client.json(entry)
            if catalog["version"] == dependency["version"]:
                return {"releasedAt": instant(catalog["published"])}
    raise ValueError(f"version {dependency['version']} is not on nuget.org")


def plugin_published(client, checker, dependency) -> dict[str, str]:
    plugin = checker.PLUGIN_IDS[dependency["name"]]
    page = client.text(
        f"https://plugins.gradle.org/plugin/{plugin}/{dependency['version']}"
    )
    match = CREATED.search(page)
    if match is None:
        raise ValueError("the plugin page carries no creation date")
    created = datetime.strptime(match.group(1), "%d %B %Y").replace(tzinfo=UTC)
    # The plugin portal publishes a day, not an instant, and the inventory
    # records these entries at that granularity.
    return {"releasedAt": created.strftime("%Y-%m-%d")}


def online_metadata(client, checker, dependency) -> dict[str, str]:
    """Everything about an entry that the working tree cannot prove."""
    kind, name = dependency["kind"], dependency["name"]
    if kind == "build-tool":
        return gradle_metadata(client, dependency)
    if name in checker.PLUGIN_IDS:
        return plugin_published(client, checker, dependency)
    if kind == "nuget" and name in checker.NUGET_PACKAGES:
        return nuget_published(client, checker, dependency)
    repo = name if kind == "github-action" else checker.GITHUB_REPOS.get(name)
    if repo:
        tag, published = github_release(client, repo, dependency["version"])
        return {"release": tag, "releasedAt": published}
    raise ValueError(f"no refresh route for kind {kind}")


def refresh(root, checker, inventory, online, problems):
    """Apply every change the repository proves, then the online metadata."""
    dependencies = [
        item for item in inventory.get("dependencies", []) if isinstance(item, dict)
    ]
    changes = declaration_changes(root, checker, dependencies, problems)
    changes += action_changes(root, dependencies, problems)
    for dependency, field, _old, new in changes:
        dependency[field] = new

    touched = {id(dependency) for dependency, *_ in changes}
    if online:
        # Every entry, not only the ones the working tree just moved: a release
        # date for a given version never changes, so refreshing all of them is
        # idempotent, and it is the only way to reach a field no declaration
        # covers, such as the version on the Gradle wrapper JAR entry.
        client = checker.OnlineClient()
        for dependency in dependencies:
            try:
                metadata = online_metadata(client, checker, dependency)
            except Exception as error:  # noqa: BLE001 - reported, never silent
                if id(dependency) in touched:
                    problems.append(
                        f"{dependency['name']}: cannot refresh metadata: {error}"
                    )
                continue
            for field, value in metadata.items():
                current = dependency.get(field)
                # Entries record a release date at the granularity their source
                # publishes, a day for the plugin portal and an instant for the
                # rest. Rewriting one that already denotes the same release
                # would be churn, and would drop the precision it carries.
                if (
                    field == "releasedAt"
                    and isinstance(current, str)
                    and checker.same_release_date(current, value)
                ):
                    continue
                if current != value:
                    changes.append((dependency, field, current, value))
                    dependency[field] = value

    # official_source derives the URL from the entry, so it is recomputed last,
    # once version and release have settled.
    for dependency, *_ in list(changes):
        source = checker.official_source(dependency)
        if source and dependency.get("source") != source:
            changes.append((dependency, "source", dependency.get("source"), source))
            dependency["source"] = source
    return changes


def mirrored_pins(root: Path) -> list[str]:
    """Test files repeating a pin, which stay a deliberate second edit."""
    tests = root / "scripts" / "tests"
    return sorted(
        f"{path.relative_to(root)}:{number}"
        for path in tests.glob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"[0-9a-f]{40}", line)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--online",
        action="store_true",
        help="refresh release dates, tags and checksums from the official sources",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would change and exit non-zero, writing nothing",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    checker = load_checker()
    path = root / "config" / "supply-chain.json"
    inventory = json.loads(path.read_text(encoding="utf-8"))

    problems: list[str] = []
    changes = refresh(root, checker, inventory, args.online, problems)

    for dependency, field, old, new in changes:
        print(f"{dependency['name']}: {field} {old} -> {new}")
    # Warnings never set the exit code: check-supply-chain.py is the gate, and a
    # second one here would fail every run over drift this script cannot fix.
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)

    if not changes:
        print("Inventory already matches the repository.")
        return 0
    if args.check:
        print(f"{len(changes)} inventory field(s) are stale.", file=sys.stderr)
        return 1

    inventory["auditedAt"] = datetime.now(UTC).strftime(INSTANT)
    path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated {len(changes)} field(s) in {path.relative_to(root)}.")
    if not args.online:
        print("Release dates were left untouched: rerun with --online to refresh them.")
    mirrored = mirrored_pins(root)
    if mirrored:
        print("Pins mirrored in tests, to review by hand:")
        for location in mirrored:
            print(f"  {location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
