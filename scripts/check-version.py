#!/usr/bin/env python3
"""Bind one stable plugin tag to every release declaration."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ElementTree
from datetime import date
from pathlib import Path


TAG = re.compile(r"^v0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
VERSION = re.compile(r"^0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
HEADING = re.compile(r"^## \[(?P<version>[^]]+)] - (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})$")


def fail(message: str) -> None:
    raise ValueError(message)


def read(path: Path, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        fail(f"{description} cannot be read: {error}")


def properties(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(read(root / "gradle.properties", "Gradle properties").splitlines(), 1):
        if not line or line.startswith(("#", "!")):
            continue
        match = re.fullmatch(r"(?P<key>[A-Za-z][A-Za-z0-9._-]*)=(?P<value>[^\r\n]*)", line)
        if match is None or match.group("key") in values:
            fail(f"Gradle properties line {number} is ambiguous")
        values[match.group("key")] = match.group("value")
    return values


def manifest_version(root: Path) -> str:
    try:
        plugin = ElementTree.fromstring(read(
            root / "src/main/resources/META-INF/plugin.xml", "plugin manifest"
        ))
    except ElementTree.ParseError as error:
        fail(f"plugin manifest XML is malformed: {error}")
    if plugin.tag != "idea-plugin" or plugin.attrib:
        fail("plugin manifest root is not canonical")
    versions = [child for child in plugin if child.tag == "version"]
    if len(versions) != 1 or versions[0].attrib or list(versions[0]) or versions[0].text is None:
        fail("plugin manifest must contain exactly one direct version")
    return versions[0].text.strip()


def changelog_version(root: Path, version: str) -> None:
    text = read(root / "CHANGELOG.md", "changelog")
    matches = []
    for line in text.splitlines():
        if line == "## [Unreleased]":
            continue
        if not line.startswith("## ["):
            continue
        match = HEADING.fullmatch(line)
        if match is None:
            fail("changelog release heading is not canonical")
        try:
            date.fromisoformat(match.group("date"))
        except ValueError:
            fail("changelog release heading date is invalid")
        matches.append(match.group("version"))
    if matches.count(version) != 1:
        fail(f"changelog must contain exactly one [{version}] release heading")


def check(tag: str, root: Path) -> str:
    if TAG.fullmatch(tag) is None:
        fail("tag must be a stable tag matching v0.MINOR.PATCH")
    version = tag[1:]
    values = properties(root)
    declared = values.get("version", "")
    if VERSION.fullmatch(declared) is None or declared != version:
        fail(f"Gradle version is {declared!r}, expected {version!r}")
    channel = values.get("marketplaceChannel", "")
    if channel != "default":
        fail("Marketplace channel must be exactly 'default'")
    manifest = manifest_version(root)
    if manifest != version:
        fail(f"plugin manifest version is {manifest!r}, expected {version!r}")
    changelog_version(root, version)
    build = read(root / "build.gradle.kts", "ZIP name")
    if len(re.findall(r'archiveBaseName\.set\(\s*"perf-sentinel"\s*\)', build)) != 1:
        fail("ZIP name must be declared exactly once as perf-sentinel")
    channel_binding = re.findall(
        r'channels\.set\(\s*providers\.gradleProperty\(\s*"marketplaceChannel"\s*\)'
        r'\.map\s*\{\s*listOf\(it\)\s*}\s*\)',
        build,
    )
    if len(channel_binding) != 1:
        fail("Marketplace channel must be bound exactly once to the publish task")
    return f"perf-sentinel-{version}.zip"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: scripts/check-version.py v0.MINOR.PATCH", file=sys.stderr)
        return 2
    try:
        archive = check(sys.argv[1], Path.cwd())
    except ValueError as error:
        print(f"version contract: {error}", file=sys.stderr)
        return 1
    print(f"version contract matches {sys.argv[1]} ({archive})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
