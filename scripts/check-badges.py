#!/usr/bin/env python3
"""Require the README badge block to match committed evidence."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


REPO_URL = "https://github.com/robintra/perf-sentinel-jetbrains-plugin"
LICENSE_SHA256 = "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef"
KOTLIN_VERSION = "2.4.10"
TARGET_FRAMEWORK = "net472"
RIDER_CSPROJ = "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj"

BADGES = {
    "JetBrains IDEs": (
        "https://img.shields.io/badge/JetBrains%20IDEs-2025.3%20%7C%202026.2-087CFA?logo=jetbrains&logoColor=white",
        f"{REPO_URL}/blob/main/build.gradle.kts",
        "build.gradle.kts",
    ),
    "Kotlin": (
        "https://img.shields.io/badge/Kotlin-2.4.10-7F52FF?logo=kotlin&logoColor=white",
        f"{REPO_URL}/blob/main/settings.gradle.kts",
        "settings.gradle.kts",
    ),
    ".NET": (
        "https://img.shields.io/badge/.NET%20Framework-4.7.2-512BD4?logo=dotnet&logoColor=white",
        f"{REPO_URL}/blob/main/src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj",
        "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj",
    ),
    "CI": (
        f"{REPO_URL}/actions/workflows/ci.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/ci.yml",
        ".github/workflows/ci.yml",
    ),
    "Security Audit": (
        f"{REPO_URL}/actions/workflows/security-audit.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/security-audit.yml",
        ".github/workflows/security-audit.yml",
    ),
    "CodeQL": (
        f"{REPO_URL}/actions/workflows/codeql.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/codeql.yml",
        ".github/workflows/codeql.yml",
    ),
    "Qodana": (
        "https://img.shields.io/badge/Qodana-JVM%20%7C%20Rider-000000?logo=qodana&logoColor=white",
        f"{REPO_URL}/actions/workflows/security-audit.yml",
        ".github/workflows/security-audit.yml",
    ),
    "Release": (
        f"{REPO_URL}/actions/workflows/release.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/release.yml",
        ".github/workflows/release.yml",
    ),
    "Signed ZIP": (
        "https://img.shields.io/badge/JetBrains%20ZIP-signature%20configured-lightgrey?logo=jetbrains&logoColor=white",
        f"{REPO_URL}/actions/workflows/release.yml",
        ".github/workflows/release.yml",
    ),
}


class DuplicateKeyError(ValueError):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(f"duplicate key: {key}")
        value[key] = item
    return value


def marketplace_badges(listing_id):
    destination = f"https://plugins.jetbrains.com/plugin/{listing_id}"
    return {
        "Marketplace version": (
            f"https://img.shields.io/jetbrains/plugin/v/{listing_id}", destination, None
        ),
        "Marketplace downloads": (
            f"https://img.shields.io/jetbrains/plugin/d/{listing_id}", destination, None
        ),
    }


def canonical_prefix(listing_id):
    badges = BADGES | (marketplace_badges(listing_id) if listing_id else {})
    return "# Perf Sentinel for JetBrains IDEs\n\n" + '<p align="center">\n' + "".join(
        f'    <a href="{destination}"><img src="{image}" alt="{label}" /></a>\n'
        for label, (image, destination, _) in badges.items()
    ) + "</p>\n\n"


def load_metadata(path):
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if type(value) is not dict or set(value) != {"schema_version", "marketplace_listing_id"}:
        raise ValueError("release metadata schema is not closed")
    listing_id = value["marketplace_listing_id"]
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("release metadata schema_version must be 1")
    if listing_id is not None and (type(listing_id) is not int or listing_id <= 0):
        raise ValueError("release metadata Marketplace listing ID is invalid")
    return listing_id


def declared_version_errors(root):
    """Both technology badges state a literal version, so the build files must still say so."""
    errors = []
    settings = (root / "settings.gradle.kts").read_text(encoding="utf-8")
    if f'id("org.jetbrains.kotlin.jvm") version "{KOTLIN_VERSION}"' not in settings:
        errors.append("Kotlin badge differs from settings.gradle.kts")
    errors.extend(target_framework_errors(root))
    return errors


def target_framework_errors(root):
    """The .NET badge states 4.7.2 as a literal, so the csproj must still say so."""
    text = (root / RIDER_CSPROJ).read_text(encoding="utf-8")
    if f"<TargetFramework>{TARGET_FRAMEWORK}</TargetFramework>" in text:
        return []
    return [f".NET badge differs from {RIDER_CSPROJ}"]


def validate(root):
    errors = []
    try:
        listing_id = load_metadata(root / "config" / "release-metadata.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return [f"release metadata is invalid: {error}"]

    readme = (root / "README.md").read_bytes()
    prefix = canonical_prefix(listing_id)
    if not readme.startswith(prefix.encode("utf-8")):
        errors.append("README must start with the canonical top badge block")
    body = readme[len(prefix.encode("utf-8")) :].decode("utf-8")
    if re.search(r"\[\s*!\[", body) or (
        re.search(r"<a(?:\s|>)", body, re.IGNORECASE)
        and re.search(r"<img(?:\s|>)", body, re.IGNORECASE)
    ):
        errors.append("README contains a badge outside the canonical top block")
    if listing_id is None and (
        b"img.shields.io/jetbrains/plugin/" in readme
        or b"plugins.jetbrains.com/plugin/" in readme
    ):
        errors.append("Marketplace badges require a recorded listing ID")
    for _, _, evidence in BADGES.values():
        if not (root / evidence).is_file():
            errors.append(f"missing local evidence: {evidence}")
    errors.extend(declared_version_errors(root))
    if hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest() != LICENSE_SHA256:
        errors.append("LICENSE differs from canonical AGPL-3.0-only")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    try:
        errors = validate(arguments.root)
    except (OSError, UnicodeError, ValueError) as error:
        errors = [f"badge check failed closed: {error}"]
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
