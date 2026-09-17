#!/usr/bin/env python3
"""Bring config/supply-chain.json back in step with the repository.

check-supply-chain.py refuses an inventory that disagrees with what the
repository declares, and nothing writes that inventory. A Renovate pull
request therefore bumps a manifest and fails the gate until the matching
entry is rewritten — which Renovate does itself, running this script as a
postUpgradeTask. This performs that rewrite, resolving every declaration
through check-supply-chain.py itself so the two cannot drift apart.

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


def declared_fields(checker, declaration: str, actual: str) -> dict[str, str]:
    """The fields check-supply-chain compares for one declaration."""
    if declaration in checker.IMAGE_DECLARATIONS:
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
        except Exception as error:  # reported, never silent
            problems.append(f"{dependency['name']}: cannot read {declaration}: {error}")
            continue
        distinct = set(values)
        if len(distinct) != 1:
            # No value, or several declarations of one dependency that disagree.
            # The checker reports exactly that, and picking a winner here would
            # hide a real divergence behind a green gate.
            problems.append(f"{dependency['name']}: {declaration} resolves to {values}")
            continue
        for field, value in declared_fields(checker, declaration, distinct.pop()).items():
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


def jetbrains_published(client, checker, dependency) -> dict[str, str]:
    """The release date the product feed gives for the pinned version."""
    product = next(
        (name for name in checker.PRODUCT_CODES if dependency["name"].startswith(name + " ")),
        None,
    )
    if product is None:
        raise ValueError("the name matches no known JetBrains product")
    # The entry already carries the feed URL the checker validates against, so
    # the two cannot drift onto different documents.
    releases = client.json(dependency["source"])[checker.PRODUCT_CODES[product]]
    for release in releases:
        if release["version"] == dependency["version"]:
            return {"releasedAt": release["date"]}
    raise ValueError(f"version {dependency['version']} is not in the product feed")


def online_metadata(client, checker, dependency) -> dict[str, str]:
    """Everything about an entry that the working tree cannot prove."""
    kind, name = dependency["kind"], dependency["name"]
    if kind == "build-tool":
        return gradle_metadata(client, dependency)
    if name in checker.PLUGIN_IDS:
        return plugin_published(client, checker, dependency)
    if kind == "nuget" and name in checker.NUGET_PACKAGES:
        return nuget_published(client, checker, dependency)
    if kind == "jetbrains-product":
        return jetbrains_published(client, checker, dependency)
    if kind == "container" and name in checker.CONTAINER_RELEASE_REPOS:
        # Assumes the inventoried release tag has no "v" prefix, unlike
        # check-supply-chain.py's stable_release_candidates, which tolerates either. True today
        # for renovatebot/renovate; if that ever changes, this 404s.
        release = client.json(
            f"https://api.github.com/repos/{checker.CONTAINER_RELEASE_REPOS[name]}/releases/tags/{dependency['release']}"
        )
        return {"releasedAt": instant(release["published_at"])}
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
            except Exception as error:  # reported, never silent
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


# Products the plugin verifier pulls as an installer, mapped to the coordinate
# the lock file records and the file name JetBrains publishes. Only IntelliJ
# IDEA spells the two differently: the artifact is idea-<version>, the download
# ideaIU-<version>. A product missing here is one no installer covers, such as
# Rider, which the verifier takes from Maven with useInstaller = false.
INSTALLER_PINS = {
    "IntelliJ IDEA": ("idea", "idea", "ideaIU", "com.jetbrains.intellij.idea"),
    "PyCharm": ("python", "pycharm-professional", "pycharm-professional", "com.jetbrains.intellij.pycharm"),
    "PhpStorm": ("webide", "PhpStorm", "PhpStorm", "com.jetbrains.intellij.phpstorm"),
    "RubyMine": ("ruby", "RubyMine", "RubyMine", "com.jetbrains.intellij.rubymine"),
    "WebStorm": ("webstorm", "WebStorm", "WebStorm", "com.jetbrains.intellij.webstorm"),
    "GoLand": ("go", "goland", "goland", "com.jetbrains.intellij.goland"),
    "RustRover": ("rustrover", "RustRover", "RustRover", "com.jetbrains.intellij.rustrover"),
}


def derived_writes(root, client, changes, problems) -> list[str]:
    """Carry a moved JetBrains pin into the artifacts derived from it.

    The inventory is not the only file a bump leaves behind: the lock file
    names the version, and the verification metadata names every installer it
    covers. Both are mechanical consequences of the declaration, which is why
    they belong here rather than in a human's hands.
    """
    written: list[str] = []
    for dependency, field, old, new in changes:
        if field != "version" or dependency.get("kind") != "jetbrains-product":
            continue
        product = next(
            (name for name in INSTALLER_PINS if dependency["name"].startswith(name + " ")),
            None,
        )
        if product is None:
            problems.append(
                f"{dependency['name']}: no installer coordinate, its lock needs a full regeneration"
            )
            continue
        group, artifact, _download, maven = INSTALLER_PINS[product]
        lock = root / "gradle.lockfile"
        text = lock.read_text(encoding="utf-8")
        # A version a testIde or runIde also resolves comes with platform
        # coordinates and a bundled module, which only a Gradle relock can move.
        if re.search(rf"^{re.escape(maven)}:[^:]+:{re.escape(old)}=", text, re.M):
            problems.append(
                f"{dependency['name']}: a test IDE resolves {old} from Maven, so its lock needs a "
                f"full regeneration and gradle/verification-metadata.xml needs the com.jetbrains:jbr "
                f"component for the runtime it moved to, which dependency locking never records"
            )
            continue
        # Anchored: an installer coordinate is a suffix of its Maven namesake.
        pin = re.search(
            rf"^{re.escape(group)}:{re.escape(artifact)}:{re.escape(old)}=(.*)$", text, re.M
        )
        if pin is None:
            problems.append(f"{dependency['name']}: {group}:{artifact}:{old} is not in the lock file")
            continue
        if pin.group(1) != "intellijPluginVerifierIdesDependency":
            problems.append(
                f"{dependency['name']}: {pin.group(1)} also resolves {old}, its lock needs a full regeneration"
            )
            continue
        lock.write_text(
            text[: pin.start()] + f"{group}:{artifact}:{new}={pin.group(1)}" + text[pin.end():],
            encoding="utf-8",
        )
        written.append(f"gradle.lockfile: {group}:{artifact} {old} -> {new}")
        try:
            written.append(
                rewrite_component(root, client, group, artifact, _download, old, new)
            )
        except Exception as error:  # reported, never silent
            problems.append(f"{dependency['name']}: cannot rewrite verification metadata: {error}")
    return written


def published_checksum(client, group: str, name: str) -> str:
    """The SHA-256 JetBrains publishes beside an installer.

    The archive itself is not downloaded: the plugin verifier jobs already pull
    every installer and let Gradle confront it with this value before anything
    reaches the default branch.
    """
    document = client.text(f"https://download.jetbrains.com/{group}/{name}.sha256")
    checksum = document.split()[0] if document.split() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError(f"{name}.sha256 does not carry a SHA-256")
    return checksum


def rewrite_component(root, client, group, artifact, download, old, new) -> str:
    """Move one verification-metadata component onto the new version."""
    path = root / "gradle" / "verification-metadata.xml"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'      <component group="{re.escape(group)}" name="{re.escape(artifact)}" '
        rf'version="{re.escape(old)}">\n.*?      </component>\n',
        re.S,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"{group}:{artifact}:{old} has no verification component")
    body = ""
    for name in re.findall(r'<artifact name="([^"]+)"', match.group(0)):
        renamed = name.replace(old, new)
        checksum = published_checksum(client, group, renamed.replace(f"{artifact}-", f"{download}-", 1))
        body += (
            f'         <artifact name="{renamed}">\n'
            f'            <sha256 value="{checksum}" origin="Verified from JetBrains checksum"/>\n'
            f"         </artifact>\n"
        )
    component = (
        f'      <component group="{group}" name="{artifact}" version="{new}">\n'
        f"{body}      </component>\n"
    )
    path.write_text(text[: match.start()] + component + text[match.end():], encoding="utf-8")
    return f"gradle/verification-metadata.xml: {group}:{artifact} {old} -> {new}"


def mirrored_pins(root: Path, changes) -> list[str]:
    """Test files repeating a pin, which stay a deliberate second edit.

    A commit SHA is recognisable on sight, but a mirrored version is not, and
    reporting only the former left the version mirrors invisible. Every value
    this run just moved is therefore searched for as well: a test still
    carrying the old one is a mirror waiting for its second edit.
    """
    moved = {
        old
        for _dependency, _field, old, _new in changes
        # Shorter values match too much to be evidence of a mirror.
        if isinstance(old, str) and len(old) >= 5
    }
    tests = root / "scripts" / "tests"
    return sorted(
        f"{path.relative_to(root).as_posix()}:{number}"
        for path in tests.glob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"[0-9a-f]{40}", line) or any(value in line for value in moved)
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
    # The published checksums live upstream, so the derived artifacts can only
    # be finished online, and never on a run that promised to write nothing.
    derived = (
        derived_writes(root, checker.OnlineClient(), changes, problems)
        if changes and args.online and not args.check
        else []
    )

    for dependency, field, old, new in changes:
        print(f"{dependency['name']}: {field} {old} -> {new}")
    for line in derived:
        print(line)
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
    print(f"Updated {len(changes)} field(s) in {path.relative_to(root).as_posix()}.")
    if not args.online:
        print(
            "Release dates and the derived lock and verification entries were left "
            "untouched: rerun with --online to refresh them."
        )
    mirrored = mirrored_pins(root, changes)
    if mirrored:
        print("Pins mirrored in tests, to review by hand:")
        for location in mirrored:
            print(f"  {location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
