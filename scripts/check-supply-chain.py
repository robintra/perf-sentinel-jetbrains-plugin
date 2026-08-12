#!/usr/bin/env python3
"""Fail-closed checks for repository supply-chain inputs."""

import argparse
import json
import re
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path
from xml.etree import ElementTree


PRERELEASE = re.compile(r"(?i)(?:^|[.\-_])(alpha|beta|eap|milestone|preview|rc|snapshot|nightly)(?:[.\-_\d]|$)")
DYNAMIC = re.compile(
    r"(?i)(?:\d+\.\+|latest\.(?:release|integration)|\bSNAPSHOT\b|(?<![\w\"])[\[(]\d[\w., +*\-]*[\])])"
)
MUTABLE_IDE = re.compile(r"(?i)(?:\b(?:latest|recommended)\s*\(|LATEST-EAP-SNAPSHOT)")
REQUIRED_LOCKS = (
    "gradle.lockfile",
    "protocol/gradle.lockfile",
    "rider-frontend/gradle.lockfile",
    "src/dotnet/PerfSentinel.Rider/packages.lock.json",
    "src/dotnet/PerfSentinel.Rider.Tests/packages.lock.json",
)


def read_json(path, errors):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid or missing {path.name}: {exc}")
        return {}


def check_inventory(root, errors):
    inventory = read_json(root / "config" / "supply-chain.json", errors)
    minimum_age_hours = inventory.get("minimumReleaseAgeHours", 72)
    try:
        audited = date.fromisoformat(inventory["auditedAt"])
    except (KeyError, TypeError, ValueError):
        errors.append("supply-chain inventory has no valid audit date")
        audited = None

    for dependency in inventory.get("dependencies", []):
        name = dependency.get("name", "unnamed dependency")
        version = str(dependency.get("version", ""))
        if PRERELEASE.search(version):
            errors.append(f"{name} uses prerelease version {version}")
        if DYNAMIC.search(version):
            errors.append(f"{name} uses dynamic version {version}")
        try:
            released = date.fromisoformat(dependency["releasedAt"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name} has no valid release date")
        else:
            if audited and (audited - released).days * 24 < minimum_age_hours:
                errors.append(f"{name} was released within {minimum_age_hours} hours of the audit")
        if not str(dependency.get("source", "")).startswith("https://"):
            errors.append(f"{name} has no primary HTTPS source")

    if audited:
        for exception in inventory.get("exceptions", []):
            try:
                expiry = date.fromisoformat(exception["expiresAt"])
            except (KeyError, TypeError, ValueError):
                errors.append("compatibility exception has no valid expiry date")
                continue
            if expiry < audited:
                errors.append("compatibility exception has expired")
            if (expiry - audited).days > 90:
                errors.append("compatibility exception exceeds 90 days")
            if not exception.get("reason"):
                errors.append("compatibility exception has no reason")
            if not exception.get("owner"):
                errors.append("compatibility exception has no owner")
    return inventory


def check_gradle(root, errors):
    settings = root / "settings.gradle.kts"
    build = root / "build.gradle.kts"
    try:
        locking = settings.read_text(encoding="utf-8") + build.read_text(encoding="utf-8")
        if "lockAllConfigurations()" not in locking:
            errors.append("Gradle does not lock all configurations")
    except OSError:
        errors.append("missing settings.gradle.kts")

    metadata = root / "gradle" / "verification-metadata.xml"
    try:
        xml = ElementTree.parse(metadata)
        checksums = [node.get("value", "") for node in xml.iter() if node.tag.rsplit("}", 1)[-1] == "sha256"]
        if not checksums or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in checksums):
            errors.append("Gradle SHA-256 verification metadata is incomplete")
    except (OSError, ElementTree.ParseError):
        errors.append("Gradle SHA-256 verification metadata is missing or invalid")

    for relative in REQUIRED_LOCKS:
        path = root / relative
        try:
            if not path.read_text(encoding="utf-8").strip():
                errors.append(f"empty dependency lock: {relative}")
        except OSError:
            errors.append(f"missing dependency lock: {relative}")


def check_build_files(root, errors):
    suffixes = {".gradle", ".kts", ".toml", ".csproj", ".props"}
    ignored = {".git", ".gradle", ".idea", ".intellijPlatform", "build", "graphify-out"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes or ignored.intersection(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if DYNAMIC.search(text):
            errors.append(f"dynamic version in {path.relative_to(root)}")
        if PRERELEASE.search(text):
            errors.append(f"prerelease version in {path.relative_to(root)}")
        if MUTABLE_IDE.search(text):
            errors.append(f"mutable IDE version in {path.relative_to(root)}")


def check_nuget_sources(root, inventory, errors):
    approved = set(inventory.get("approvedNuGetSources", []))
    configs = list((root / "src" / "dotnet").rglob("[Nn]u[Gg]et.[Cc]onfig"))
    if not configs:
        errors.append("missing NuGet.Config")
        return
    for config in configs:
        try:
            xml = ElementTree.parse(config)
        except ElementTree.ParseError:
            errors.append(f"invalid NuGet.Config: {config.relative_to(root)}")
            continue
        for source in xml.findall(".//packageSources/add"):
            value = source.get("value", "")
            if value not in approved:
                errors.append(f"unapproved NuGet source {value} in {config.relative_to(root)}")


def check_online(inventory, errors):
    system_ca = Path("/etc/ssl/cert.pem")
    context = ssl.create_default_context(cafile=str(system_ca) if system_ca.is_file() else None)
    for dependency in inventory.get("dependencies", []):
        source = dependency.get("source")
        if not source:
            continue
        try:
            request = urllib.request.Request(
                source,
                headers={"User-Agent": "perf-sentinel-supply-chain/1", "Range": "bytes=0-0"},
            )
            with urllib.request.urlopen(request, timeout=15, context=context) as response:
                if response.status >= 400:
                    errors.append(f"unreachable source for {dependency.get('name')}: HTTP {response.status}")
        except Exception as exc:  # Network failures must fail the online gate closed.
            errors.append(f"unreachable source for {dependency.get('name')}: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    errors = []
    inventory = check_inventory(root, errors)
    check_gradle(root, errors)
    check_build_files(root, errors)
    check_nuget_sources(root, inventory, errors)
    if args.online:
        check_online(inventory, errors)
    if errors:
        print("\n".join(f"error: {error}" for error in errors), file=sys.stderr)
        return 1
    print("Supply-chain inputs are locked and stable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
