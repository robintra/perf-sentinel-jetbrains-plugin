#!/usr/bin/env python3
"""Fail-closed checks for repository supply-chain inputs."""

import argparse
import base64
import binascii
import fnmatch
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree


PRERELEASE = re.compile(
    r"(?i)(?:^|[.\-_])(alpha|beta|eap|milestone|preview|rc|snapshot|nightly|m\d+)(?:[.\-_\d]|$)"
)
DYNAMIC = re.compile(
    r"(?i)(?:\d+\.\+|latest\.(?:release|integration)|\bSNAPSHOT\b|(?<![\w\"])[\[(]\d[\w., +*\-]*[])])"
)
MUTABLE_IDE = re.compile(r"(?i)(?:\b(?:latest|recommended)\s*\(|LATEST-EAP-SNAPSHOT)")
GRADLE_LOCK = re.compile(r"^([^:=\s]+):([^:=\s]+):([^=\s]+)=([^,\s]+(?:,[^,\s]+)*)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")

REQUIRED_GRADLE_LOCKS = ("gradle.lockfile", "protocol/gradle.lockfile", "rider-frontend/gradle.lockfile")
REQUIRED_NUGET_LOCKS = (
    "src/dotnet/PerfSentinel.Rider/packages.lock.json",
    "src/dotnet/PerfSentinel.Rider.Tests/packages.lock.json",
)
REQUIRED_ACTIONS = set("""
actions/checkout actions/setup-java actions/setup-dotnet actions/setup-python actions/upload-artifact actions/github-script
actions/download-artifact actions/dependency-review-action github/codeql-action
JetBrains/qodana-action anchore/sbom-action ossf/scorecard-action step-security/harden-runner
google/osv-scanner-action gitleaks/gitleaks-action zizmorcore/zizmor-action gradle/actions
""".split())
REQUIRED_TOOLS = {
    "Kover", "OSV-Scanner", "Gitleaks", "TruffleHog", "Zizmor",
    "Syft", "actionlint", "Marketplace ZIP Signer", "Qodana CLI", "Qodana JVM Community image",
    "Qodana .NET image",
}
REQUIRED_TRANSITIVE_EXCEPTIONS = {
    "com.jgoodies:forms:1.1-preview",
    "org.assertj:assertj-core:4.0.0-M1",
}
KOVER_TRANSITIVE_EXCEPTION = "org.jetbrains.compose.hot-reload:hot-reload-agent:1.1.0-alpha03"
TOP_FIELDS = {
    "schemaVersion",
    "auditedAt",
    "approvedNuGetSources",
    "dependencies",
    "exceptions",
}
DEPENDENCY_FIELDS = {"name", "kind", "version", "releasedAt", "source", "sha256", "release", "compatibility", "declaration"}
DEPENDENCY_KINDS = {
    "build-tool",
    "gradle-plugin",
    "maven",
    "jetbrains-product",
    "nuget",
    "audited-tool",
    "github-action",
    "container",
}
EXCEPTION_BASE_FIELDS = {"type", "dependency", "owner", "reason", "evidence", "upstream", "expiresAt"}
DIRECT_DECLARATIONS = set("""
gradle/wrapper/gradle-wrapper.properties#distributionUrl;gradle/wrapper/gradle-wrapper.jar#sha256
settings.gradle.kts#org.jetbrains.kotlin.jvm;settings.gradle.kts#org.jetbrains.intellij.platform.module;settings.gradle.kts#org.jetbrains.changelog;settings.gradle.kts#org.jetbrains.qodana;settings.gradle.kts#org.gradle.toolchains.foojay-resolver-convention
gradle/libs.versions.toml#gson;gradle/libs.versions.toml#jackson;gradle/libs.versions.toml#junit;gradle/libs.versions.toml#rdGen
build.gradle.kts#IntelliJ IDEA 2025.3;build.gradle.kts#IntelliJ IDEA 2026.2;build.gradle.kts#Rider 2025.3;build.gradle.kts#Rider 2026.2;build.gradle.kts#PyCharm 2025.3;build.gradle.kts#PyCharm 2026.2
build.gradle.kts#PhpStorm 2025.3;build.gradle.kts#PhpStorm 2026.2;build.gradle.kts#RustRover 2025.3;build.gradle.kts#RustRover 2026.2;build.gradle.kts#RubyMine 2025.3;build.gradle.kts#RubyMine 2026.2
build.gradle.kts#WebStorm 2025.3;build.gradle.kts#WebStorm 2026.2;build.gradle.kts#GoLand 2025.3;build.gradle.kts#GoLand 2026.2
src/dotnet/Plugin.props#SdkVersion:JetBrains.Rider.SDK;src/dotnet/Plugin.props#SdkVersion:JetBrains.ReSharper.SDK.Tests;src/dotnet/Directory.Build.props#Microsoft.NETFramework.ReferenceAssemblies;src/dotnet/Directory.Build.props#Microsoft.Bcl.Memory
src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj#Microsoft.NET.Test.Sdk;src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj#NUnit3TestAdapter;qodana.yml#linter
""".replace("\n", ";").strip(";").split(";"))
OPTIONAL_DIRECT_DECLARATIONS = {
    "gradle/libs.versions.toml#kover",
    "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj#coverlet.collector",
}
PLUGIN_IDS = {
    "Kotlin Gradle plugin": "org.jetbrains.kotlin.jvm",
    "IntelliJ Platform Gradle plugin": "org.jetbrains.intellij.platform.module",
    "JetBrains Changelog plugin": "org.jetbrains.changelog",
    "JetBrains Qodana Gradle plugin": "org.jetbrains.qodana",
    "Foojay toolchain resolver": "org.gradle.toolchains.foojay-resolver-convention",
    "Kover": "org.jetbrains.kotlinx.kover",
}
MAVEN_COORDINATES = {
    "Gson": ("com.google.code.gson", "gson"),
    "Jackson BOM": ("com.fasterxml.jackson", "jackson-bom"),
    "JUnit 4": ("junit", "junit"),
    "RDGen": ("com.jetbrains.rd", "rd-gen"),
}
NUGET_PACKAGES = {
    "JetBrains Rider SDK": "JetBrains.Rider.SDK",
    "JetBrains ReSharper SDK Tests": "JetBrains.ReSharper.SDK.Tests",
    "Microsoft .NET Framework reference assemblies": "Microsoft.NETFramework.ReferenceAssemblies",
    "Microsoft.Bcl.Memory": "Microsoft.Bcl.Memory",
    "Microsoft.NET.Test.Sdk": "Microsoft.NET.Test.Sdk",
    "NUnit3TestAdapter": "NUnit3TestAdapter",
    "coverlet.collector": "coverlet.collector",
}
GITHUB_REPOS = {
    "OSV-Scanner": "google/osv-scanner",
    "Gitleaks": "gitleaks/gitleaks",
    "TruffleHog": "trufflesecurity/trufflehog",
    "Zizmor": "zizmorcore/zizmor",
    "Syft": "anchore/syft",
    "actionlint": "rhysd/actionlint",
    "Marketplace ZIP Signer": "JetBrains/marketplace-zip-signer",
    "Qodana CLI": "JetBrains/qodana-cli",
}
CONTAINER_REPOSITORIES = {
    "Qodana JVM Community image": "jetbrains/qodana-jvm-community",
    "Qodana .NET image": "jetbrains/qodana-dotnet",
}
PRODUCT_CODES = {
    "IntelliJ IDEA": "IIU",
    "Rider": "RD",
    "PyCharm": "PCP",
    "PhpStorm": "PS",
    "RustRover": "RR",
    "RubyMine": "RM",
    "WebStorm": "WS",
    "GoLand": "GO",
}
VERIFICATION_NAMESPACE = "https://schema.gradle.org/dependency-verification"


class DuplicateKey(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(f"duplicate key {key}")
        result[key] = value
    return result


def read_json(path, errors):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, json.JSONDecodeError, DuplicateKey) as exc:
        errors.append(f"invalid or missing {path.name}: {exc}")
        return {}


def parse_instant(value, *, end_of_day=False):
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = datetime.strptime(value, "%Y-%m-%d").date()
        boundary = time.max if end_of_day else time.min
        return datetime.combine(day, boundary, timezone.utc)
    if not RFC3339_UTC.fullmatch(value):
        raise ValueError("timestamp must be RFC3339 UTC or YYYY-MM-DD")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_fields(record, required, allowed, label, errors):
    if type(record) is not dict:
        errors.append(f"{label} must be an object")
        return False
    unknown = set(record) - allowed
    missing = required - set(record)
    if unknown:
        errors.append(f"{label} has unknown field(s): {', '.join(sorted(unknown))}")
    if missing:
        errors.append(f"{label} is missing field(s): {', '.join(sorted(missing))}")
    return not unknown and not missing


def official_source(dependency):
    name, kind, version = (dependency.get(key, "") for key in ("name", "kind", "version"))
    if kind == "build-tool":
        return "https://services.gradle.org/versions/current"
    if name in PLUGIN_IDS:
        return f"https://plugins.gradle.org/plugin/{PLUGIN_IDS[name]}/{version}"
    if name in MAVEN_COORDINATES:
        group, artifact = MAVEN_COORDINATES[name]
        return f"https://repo.maven.apache.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
    if kind == "jetbrains-product":
        product = next((product for product in PRODUCT_CODES if name.startswith(product + " ")), "")
        return f"https://data.services.jetbrains.com/products/releases?code={PRODUCT_CODES.get(product, '')}&type=release"
    if name in NUGET_PACKAGES:
        return f"https://www.nuget.org/packages/{NUGET_PACKAGES[name]}/{version}"
    if kind == "github-action":
        return f"https://github.com/{name}/releases/tag/{dependency.get('release', '')}"
    if name in GITHUB_REPOS:
        return f"https://github.com/{GITHUB_REPOS[name]}/releases/tag/{dependency.get('release', '')}"
    if kind == "container" and name in CONTAINER_REPOSITORIES:
        return f"https://hub.docker.com/r/{CONTAINER_REPOSITORIES[name]}"
    return None


def check_inventory(root, errors, now):
    inventory = read_json(root / "config" / "supply-chain.json", errors)
    if not check_fields(inventory, TOP_FIELDS, TOP_FIELDS, "inventory", errors):
        return inventory
    if type(inventory["schemaVersion"]) is not int or inventory["schemaVersion"] != 1:
        errors.append("schemaVersion must be integer 1")
    try:
        audited = parse_instant(inventory["auditedAt"])
        if not inventory["auditedAt"].endswith("Z") or not RFC3339_UTC.fullmatch(inventory["auditedAt"]):
            raise ValueError("audit timestamp requires UTC time")
        if audited > now + timedelta(minutes=5):
            errors.append("auditedAt is in the future")
    except (TypeError, ValueError):
        errors.append("supply-chain inventory has no valid RFC3339 UTC audit timestamp")
        audited = None

    approved = inventory["approvedNuGetSources"]
    if type(approved) is not list or not approved or any(type(item) is not str for item in approved):
        errors.append("approvedNuGetSources must be a non-empty string array")
    elif len(set(approved)) != len(approved):
        errors.append("approvedNuGetSources contains duplicates")

    dependencies = inventory["dependencies"]
    if type(dependencies) is not list or not dependencies:
        errors.append("dependencies must not be empty")
        dependencies = []
    names = set()
    for index, dependency in enumerate(dependencies):
        label = f"dependency[{index}]"
        required = {"name", "kind", "version", "releasedAt", "source"}
        if not check_fields(dependency, required, DEPENDENCY_FIELDS, label, errors):
            if isinstance(dependency, dict) and "releasedAt" not in dependency:
                errors.append(f"{dependency.get('name', label)} has no valid release date")
            continue
        if any(type(dependency[field]) is not str or not dependency[field] for field in required):
            errors.append(f"{label} fields must be non-empty strings")
            continue
        name = dependency["name"]
        if name in names:
            errors.append(f"duplicate dependency name {name}")
        names.add(name)
        if dependency["kind"] not in DEPENDENCY_KINDS:
            errors.append(f"{name} has invalid kind")
        version = dependency["version"]
        if PRERELEASE.search(version):
            errors.append(f"{name} uses prerelease version {version}")
        if DYNAMIC.search(version):
            errors.append(f"{name} uses dynamic version {version}")
        try:
            parse_instant(dependency["releasedAt"], end_of_day=True)
        except ValueError:
            errors.append(f"{name} has no valid release date")
        if not dependency["source"].startswith("https://"):
            errors.append(f"{name} has no primary HTTPS source")
        expected_source = official_source(dependency)
        if not expected_source:
            errors.append(f"{name} has no official source policy")
        elif dependency["source"] != expected_source:
            errors.append(f"{name} does not use its official source {expected_source}")
        if "sha256" in dependency and (type(dependency["sha256"]) is not str or not SHA256.fullmatch(dependency["sha256"])):
            errors.append(f"{name} has invalid SHA-256")
        for field in ("release", "compatibility", "declaration"):
            if field in dependency and (type(dependency[field]) is not str or not dependency[field]):
                errors.append(f"{name} field {field} must be a non-empty string")
        if dependency["kind"] == "github-action":
            if not SHA1.fullmatch(version) or type(dependency.get("release")) is not str:
                errors.append(f"{name} action requires a full SHA and release")

    action_names = {item.get("name") for item in dependencies if isinstance(item, dict) and item.get("kind") == "github-action"}
    for action in sorted(REQUIRED_ACTIONS - action_names):
        errors.append(f"required action missing from inventory: {action}")
    for action in sorted(action_names - REQUIRED_ACTIONS):
        errors.append(f"unexpected action in inventory: {action}")
    tool_names = {
        item.get("name") for item in dependencies
        if isinstance(item, dict) and item.get("kind") in {"audited-tool", "container"}
    }
    for tool in sorted(tool_names - REQUIRED_TOOLS):
        errors.append(f"unexpected tool in inventory: {tool}")
    if (root / "gradle/libs.versions.toml").is_file():
        for tool in sorted(REQUIRED_TOOLS - tool_names):
            errors.append(f"required tool missing from inventory: {tool}")

    exceptions = inventory["exceptions"]
    if type(exceptions) is not list:
        errors.append("exceptions must be an array")
        exceptions = []
    exception_dependencies = []
    for index, exception in enumerate(exceptions):
        label = f"exception[{index}]"
        exception_type = exception.get("type") if isinstance(exception, dict) else None
        if isinstance(exception, dict):
            exception_dependencies.append(exception.get("dependency"))
        allowed = EXCEPTION_BASE_FIELDS | ({"lockPaths", "configurations"} if exception_type == "transitive-prerelease" else set())
        if not check_fields(exception, EXCEPTION_BASE_FIELDS, allowed, label, errors):
            errors.append("compatibility exception field set is invalid")
            continue
        if exception_type not in {"compatibility", "transitive-prerelease"}:
            errors.append(f"{label} has invalid type")
        for field in EXCEPTION_BASE_FIELDS - {"type"}:
            if type(exception[field]) is not str or not exception[field]:
                errors.append(f"{label} exception field {field} must be a non-empty string")
        if exception.get("owner") != "Maintainers":
            errors.append(f"{label} owner must be the actionable internal team Maintainers")
        if not str(exception.get("upstream", "")).startswith("https://"):
            errors.append(f"{label} upstream must be a specific HTTPS URL")
        try:
            expiry = parse_instant(exception["expiresAt"], end_of_day=True)
        except (KeyError, ValueError):
            errors.append("compatibility exception has no valid expiry date")
            continue
        if expiry < now:
            errors.append("compatibility exception has expired")
        if audited and expiry - audited > timedelta(days=90):
            errors.append("compatibility exception exceeds 90 days")
        if exception_type == "transitive-prerelease":
            for field in ("lockPaths", "configurations"):
                if type(exception.get(field)) is not list or not exception[field] or any(type(v) is not str for v in exception[field]):
                    errors.append(f"{label} {field} must be a non-empty string array")
    duplicates = {dependency for dependency in exception_dependencies if exception_dependencies.count(dependency) > 1}
    for dependency in sorted(duplicates):
        errors.append(f"duplicate exception for {dependency}")
    required_compatibility = {"RDGen 2026.2.5"} if "RDGen" in names else set()
    actual_compatibility = {
        item.get("dependency") for item in exceptions
        if isinstance(item, dict) and item.get("type") == "compatibility"
    }
    for dependency in sorted(required_compatibility - actual_compatibility):
        errors.append(f"required RDGen compatibility exception missing: {dependency}")
    for dependency in sorted(actual_compatibility - required_compatibility):
        errors.append(f"unexpected compatibility exception: {dependency}")
    rdgen = next((item for item in exceptions if isinstance(item, dict) and item.get("dependency") == "RDGen 2026.2.5"), None)
    if rdgen and (
        rdgen.get("evidence") != "./gradlew :protocol:rdgen --warning-mode all"
        or rdgen.get("upstream") != "https://github.com/JetBrains/rd/releases/tag/2026.2.5"
    ):
        errors.append("RDGen compatibility exception evidence/upstream is not exact")
    return inventory


def strip_kotlin_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def registered_ide_blocks(text):
    pattern = re.compile(r"intellijPlatformTesting\.(?:runIde|testIde)\.register\([^)]*\)\s*\{")
    blocks = []
    for match in pattern.finditer(text):
        start = match.end() - 1
        depth = 0
        quote = None
        line_comment = False
        block_comment_depth = 0
        escaped = False
        index = start
        while index < len(text):
            pair = text[index:index + 2]
            char = text[index]
            if line_comment:
                if char == "\n":
                    line_comment = False
            elif block_comment_depth:
                if pair == "/*":
                    block_comment_depth += 1
                    index += 1
                if pair == "*/":
                    block_comment_depth -= 1
                    index += 1
            elif quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif pair in {"//", "/*"}:
                line_comment = pair == "//"
                block_comment_depth = int(pair == "/*")
                index += 1
            elif char in {'"', "'"}:
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start + 1:index])
                    break
            index += 1
    return blocks


def declared_versions(root, declaration):
    relative, selector = declaration.split("#", 1)
    path = root / relative
    if declaration.endswith("#sha256"):
        import hashlib
        return [hashlib.sha256(path.read_bytes()).hexdigest()]
    text = path.read_text(encoding="utf-8")
    if relative == "settings.gradle.kts":
        match = re.search(rf'id\("{re.escape(selector)}"\)\s+version\s+"([^"]+)"', strip_kotlin_comments(text))
        return [match.group(1)] if match else []
    if relative == "gradle/libs.versions.toml":
        value = tomllib.loads(text).get("versions", {}).get(selector)
        return [value] if isinstance(value, str) else []
    if declaration.endswith("#distributionUrl"):
        match = re.search(r"gradle-([0-9][^-]*)-bin\.zip", text)
        return [match.group(1)] if match else []
    if relative == "build.gradle.kts":
        product, wave = selector.rsplit(" ", 1)
        types = {
            "IntelliJ IDEA": "IntellijIdea",
            "Rider": "Rider",
            "PyCharm": "PyCharm(?:Professional)?",
            "PhpStorm": "PhpStorm",
            "RustRover": "RustRover",
            "RubyMine": "RubyMine",
            "WebStorm": "WebStorm",
            "GoLand": "GoLand",
        }
        product_type = types[product]
        versions = re.findall(rf'create\(IntelliJPlatformType\.{product_type},\s*"([^"]+)"\)', text)
        for block in registered_ide_blocks(text):
            if re.search(rf'\btype\s*=\s*IntelliJPlatformType\.{product_type}\b', block):
                versions += re.findall(r'\bversion\s*=\s*"([^"]+)"', block)
        if product == "IntelliJ IDEA":
            versions += re.findall(r'intellijIdea\("([^"]+)"\)', text)
        if product == "Rider" and wave == "2025.3":
            frontend = root / "rider-frontend/build.gradle.kts"
            if frontend.is_file():
                versions += re.findall(r'rider\("([^"]+)"\)', frontend.read_text(encoding="utf-8"))
        return [version for version in versions if version.startswith(wave)]
    if relative.endswith((".props", ".csproj")):
        xml = ElementTree.parse(path)
        if selector.startswith("SdkVersion:"):
            return [xml.findtext(".//SdkVersion")]
        package = xml.find(f'.//PackageReference[@Include="{selector}"]')
        return [package.get("Version")] if package is not None else []
    if relative == "qodana.yml":
        match = re.search(r"^linter:\s*jetbrains/qodana-jvm-community:([^@\s]+)@(sha256:[0-9a-f]{64})$", text, re.M)
        return [f"{match.group(1)}@{match.group(2)}"] if match else []
    return []


def check_declarations(root, inventory, errors):
    declared = [item for item in inventory.get("dependencies", []) if isinstance(item, dict) and item.get("declaration")]
    declarations = [item["declaration"] for item in declared]
    for declaration in {value for value in declarations if declarations.count(value) > 1}:
        errors.append(f"duplicate inventory declaration: {declaration}")
    entries = {item["declaration"]: item for item in declared}
    actual_repo = (root / "gradle" / "libs.versions.toml").is_file()
    optional = {
        declaration
        for declaration in OPTIONAL_DIRECT_DECLARATIONS
        if declaration in entries
    }
    required = DIRECT_DECLARATIONS | optional if actual_repo else {key for key in entries if (root / key.split("#", 1)[0]).is_file()}
    for declaration in sorted(required - set(entries)):
        errors.append(f"direct declaration missing from inventory: {declaration}")
    for declaration, dependency in entries.items():
        if declaration not in DIRECT_DECLARATIONS | OPTIONAL_DIRECT_DECLARATIONS:
            errors.append(f"unsupported direct declaration {declaration}")
            continue
        try:
            actual = declared_versions(root, declaration)
        except (OSError, ElementTree.ParseError, ValueError, tomllib.TOMLDecodeError):
            actual = []
        if declaration == "qodana.yml#linter":
            expected = f"{dependency.get('release')}@{dependency.get('version')}"
        else:
            expected = dependency.get("sha256") if declaration.endswith("#sha256") else dependency.get("version")
        if not actual or any(value != expected for value in actual):
            detail = "Rider declarations diverge" if declaration.startswith("build.gradle.kts#Rider") else "does not match declaration"
            errors.append(f"{dependency.get('name')} {detail} {declaration}: expected {expected}, found {actual}")

    kover_builds = (root / "build.gradle.kts", root / "rider-frontend" / "build.gradle.kts")
    if any(path.is_file() and "libs.plugins.kover" in path.read_text(encoding="utf-8") for path in kover_builds):
        kover = [item for item in inventory.get("dependencies", []) if item.get("name") == "Kover"]
        if len(kover) != 1 or kover[0].get("declaration") != "gradle/libs.versions.toml#kover":
            errors.append("Kover declaration missing from inventory")
    test_project = root / "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj"
    if test_project.is_file() and 'Include="coverlet.collector"' in test_project.read_text(encoding="utf-8"):
        coverlet = [item for item in inventory.get("dependencies", []) if item.get("name") == "coverlet.collector"]
        expected = "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj#coverlet.collector"
        if len(coverlet) != 1 or coverlet[0].get("declaration") != expected:
            errors.append("coverlet.collector declaration missing from inventory")


def parse_gradle_locks(root, inventory, errors):
    occurrences = {}
    for relative in REQUIRED_GRADLE_LOCKS:
        path = root / relative
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            errors.append(f"missing dependency lock: {relative}")
            continue
        entries = 0
        coordinates = set()
        for number, line in enumerate(lines, 1):
            if not line or line.startswith("#"):
                continue
            if line.startswith("empty=") and line[6:] and all(part and not part.isspace() for part in line[6:].split(",")):
                continue
            match = GRADLE_LOCK.fullmatch(line)
            if not match:
                errors.append(f"invalid Gradle lock entry {relative}:{number}")
                continue
            entries += 1
            coordinate = ":".join(match.group(i) for i in range(1, 4))
            if coordinate in coordinates:
                errors.append(f"duplicate Gradle lock coordinate {relative}:{number}: {coordinate}")
            coordinates.add(coordinate)
            occurrences.setdefault(coordinate, {})[relative] = set(match.group(4).split(","))
            version = match.group(3)
            if DYNAMIC.search(version):
                errors.append(f"dynamic version in Gradle lock: {coordinate}")
        if not entries:
            errors.append(f"empty dependency lock: {relative}")

    exceptions = {
        item.get("dependency"): item
        for item in inventory.get("exceptions", [])
        if isinstance(item, dict) and item.get("type") == "transitive-prerelease"
    }
    required_exceptions = set(
        REQUIRED_TRANSITIVE_EXCEPTIONS
        if any(item.get("name") == "RDGen" for item in inventory.get("dependencies", []) if isinstance(item, dict))
        else set()
    )
    if any(item.get("name") == "Kover" and item.get("declaration") for item in inventory.get("dependencies", []) if isinstance(item, dict)):
        required_exceptions.add(KOVER_TRANSITIVE_EXCEPTION)
    for coordinate in sorted(required_exceptions - set(exceptions)):
        errors.append(f"required transitive exception missing: {coordinate}")
    for coordinate in sorted(set(exceptions) - required_exceptions):
        errors.append(f"unexpected transitive exception: {coordinate}")
    prereleases = {coordinate for coordinate in occurrences if PRERELEASE.search(coordinate.rsplit(":", 1)[-1])}
    for coordinate in sorted(prereleases - set(exceptions)):
        errors.append(f"unexcepted prerelease in Gradle lock: {coordinate}")
    for coordinate, exception in exceptions.items():
        locked = occurrences.get(coordinate)
        if not locked:
            errors.append(f"transitive exception {coordinate} does not match a lock entry")
            continue
        paths = set(exception.get("lockPaths", []))
        configurations = set(exception.get("configurations", []))
        actual_configurations = set().union(*locked.values())
        if paths != set(locked):
            errors.append(f"transitive exception {coordinate} lockPaths do not match occurrences")
        if configurations != actual_configurations:
            errors.append(f"transitive exception {coordinate} configurations do not match occurrences")
        expected_evidence = f"./gradlew dependencyInsight --dependency {coordinate.rsplit(':', 1)[0]}"
        if not exception.get("evidence", "").startswith(expected_evidence):
            errors.append(f"transitive exception {coordinate} evidence is not reproducible")
        evidence_configurations = {"intellijPlatformTestClasspath"} & configurations
        if any("262" in name for name in configurations):
            evidence_configurations.add("intellijPlatformTestClasspath_testRustRover262")
        if coordinate.startswith("com.jgoodies:forms:"):
            evidence_configurations = {"intellijPlatformJavaCompiler"}
        for configuration in evidence_configurations:
            if f"--configuration {configuration}" not in exception.get("evidence", ""):
                errors.append(f"transitive exception {coordinate} evidence does not cover configuration {configuration}")
    for coordinate in sorted(set(exceptions) - prereleases):
        errors.append(f"transitive exception is not an exact locked prerelease: {coordinate}")
    return occurrences


def check_verification_metadata(root, locked, errors):
    path = root / "gradle" / "verification-metadata.xml"
    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError):
        errors.append("Gradle SHA-256 verification metadata is missing or invalid")
        return
    root_node = tree.getroot()
    if root_node.tag != f"{{{VERIFICATION_NAMESPACE}}}verification-metadata":
        errors.append("invalid verification metadata root")
    allowed_children = {
        "verification-metadata": {"configuration", "components"},
        "configuration": {"verify-metadata", "verify-signatures", "trusted-artifacts"},
        "trusted-artifacts": {"trust"},
        "trust": set(),
        "components": {"component"},
        "component": {"artifact"},
        "artifact": {"sha256"},
        "sha256": set(),
        "verify-metadata": set(),
        "verify-signatures": set(),
    }
    allowed_attributes = {
        "verification-metadata": {"schemaLocation"},
        "configuration": set(),
        "trusted-artifacts": set(),
        "trust": {"group", "file", "regex", "reason"},
        "components": set(),
        "component": {"group", "name", "version"},
        "artifact": {"name"},
        "sha256": {"value", "origin"},
        "verify-metadata": set(),
        "verify-signatures": set(),
    }
    components = set()
    artifact_count = 0
    for node in root_node.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if not node.tag.startswith(f"{{{VERIFICATION_NAMESPACE}}}"):
            errors.append(f"verification metadata element has wrong namespace: {tag}")
        if tag not in allowed_children:
            errors.append(f"unknown verification metadata element {tag}")
            continue
        attrs = {key.rsplit("}", 1)[-1] for key in node.attrib}
        if attrs - allowed_attributes[tag]:
            errors.append(f"unknown verification metadata attribute on {tag}")
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag not in allowed_children[tag]:
                errors.append(f"unexpected verification metadata element {child_tag} under {tag}")
        if tag == "component":
            identity = tuple(node.get(key, "") for key in ("group", "name", "version"))
            if not all(identity):
                errors.append("verification metadata component identity is incomplete")
            if identity in components:
                errors.append(f"duplicate verification component: {':'.join(identity)}")
            components.add(identity)
            artifacts = [child.get("name", "") for child in node if child.tag.rsplit("}", 1)[-1] == "artifact"]
            for artifact in {name for name in artifacts if artifacts.count(name) > 1}:
                errors.append(f"duplicate verification artifact: {':'.join(identity)}:{artifact}")
            if not [child for child in node if child.tag.rsplit("}", 1)[-1] == "artifact"]:
                errors.append(f"verification metadata component has no artifact: {':'.join(identity)}")
        if tag == "artifact":
            artifact_count += 1
            hashes = [
                child.get("value", "")
                for child in node
                if child.tag.rsplit("}", 1)[-1] == "sha256"
            ]
            if len(hashes) != 1 or not SHA256.fullmatch(hashes[0]):
                errors.append(
                    f"verification metadata artifact has no SHA-256 or does not have exactly one SHA-256: {node.get('name', '')}"
                )
    if not components or not artifact_count:
        errors.append("Gradle SHA-256 verification metadata is incomplete")
    configurations = [node for node in root_node if node.tag.rsplit("}", 1)[-1] == "configuration"]
    if len(configurations) != 1:
        errors.append("verification metadata requires exactly one configuration")
    else:
        verify = [node for node in configurations[0] if node.tag.rsplit("}", 1)[-1] == "verify-metadata"]
        if len(verify) != 1 or (verify[0].text or "").strip() != "true":
            errors.append("verification metadata verify-metadata must be true")
    for coordinate in locked:
        identity = tuple(coordinate.split(":", 2))
        if identity not in components:
            errors.append(f"locked component missing from verification metadata: {coordinate}")


def check_gradle(root, inventory, errors):
    try:
        locking = strip_kotlin_comments((root / "build.gradle.kts").read_text(encoding="utf-8"))
        if not re.search(r"allprojects\s*\{.*?dependencyLocking\s*\{.*?lockAllConfigurations\(\)", locking, re.S):
            errors.append("Gradle does not lock all configurations")
    except OSError:
        errors.append("missing build.gradle.kts")
    locked = parse_gradle_locks(root, inventory, errors)
    check_verification_metadata(root, locked, errors)


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


def parse_nuget_locks(root, errors):
    packages = set()
    direct = {}
    for relative in REQUIRED_NUGET_LOCKS:
        path = root / relative
        data = read_json(path, errors)
        if set(data) != {"version", "dependencies"} or type(data.get("version")) is not int or data.get("version") != 1 or type(data.get("dependencies")) is not dict:
            errors.append(f"invalid NuGet lock: {relative}")
            continue
        for framework, dependencies in data["dependencies"].items():
            if type(framework) is not str or type(dependencies) is not dict:
                errors.append(f"invalid NuGet lock framework: {relative}")
                continue
            for package, value in dependencies.items():
                packages.add(package)
                if type(value) is not dict or value.get("type") not in {"Direct", "Transitive", "Project"}:
                    errors.append(f"invalid NuGet lock dependency {package}")
                    continue
                allowed = {
                    "Direct": {"type", "requested", "resolved", "contentHash", "dependencies"},
                    "Transitive": {"type", "resolved", "contentHash", "dependencies"},
                    "Project": {"type", "dependencies"},
                }[value["type"]]
                if set(value) - allowed or "dependencies" in value and type(value["dependencies"]) is not dict:
                    errors.append(f"invalid NuGet lock dependency {package}")
                if value["type"] != "Project":
                    content_hash = value.get("contentHash")
                    resolved = value.get("resolved")
                    if type(resolved) is not str:
                        errors.append(f"invalid NuGet resolved version for {package}")
                    elif PRERELEASE.search(resolved):
                        errors.append(f"prerelease in NuGet lock: {package}:{resolved}")
                    elif DYNAMIC.search(resolved):
                        errors.append(f"dynamic version in NuGet lock: {package}:{resolved}")
                    try:
                        decoded = base64.b64decode(content_hash, validate=True) if type(content_hash) is str else b""
                    except (binascii.Error, ValueError):
                        decoded = b""
                    if len(decoded) != 64:
                        errors.append(f"NuGet lock dependency {package} requires a SHA-512 contentHash")
                if value["type"] == "Direct":
                    if type(value.get("requested")) is not str:
                        errors.append(f"NuGet direct dependency {package} has no requested version")
                    direct[(relative, package)] = value.get("resolved")
    return packages, direct


def nuget_declared_direct(root):
    result = {}
    props = ElementTree.parse(root / "src/dotnet/Directory.Build.props")
    sdk = ElementTree.parse(root / "src/dotnet/Plugin.props").findtext(".//SdkVersion")
    common = props.find('.//PackageReference[@Include="Microsoft.NETFramework.ReferenceAssemblies"]').get("Version")
    patched_memory = props.find('.//PackageReference[@Include="Microsoft.Bcl.Memory"]').get("Version")
    rider = "src/dotnet/PerfSentinel.Rider/packages.lock.json"
    tests = "src/dotnet/PerfSentinel.Rider.Tests/packages.lock.json"
    result[(rider, "JetBrains.Rider.SDK")] = sdk
    result[(rider, "Microsoft.NETFramework.ReferenceAssemblies")] = common
    result[(rider, "Microsoft.Bcl.Memory")] = patched_memory
    result[(tests, "JetBrains.ReSharper.SDK.Tests")] = sdk
    result[(tests, "Microsoft.NETFramework.ReferenceAssemblies")] = common
    result[(tests, "Microsoft.Bcl.Memory")] = patched_memory
    test_project = ElementTree.parse(root / "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj")
    for package in ("Microsoft.NET.Test.Sdk", "NUnit3TestAdapter", "coverlet.collector"):
        reference = test_project.find(f'.//PackageReference[@Include="{package}"]')
        if reference is not None:
            result[(tests, package)] = reference.get("Version")
    return result


def mapping_matches(pattern, package):
    return fnmatch.fnmatchcase(package.lower(), pattern.lower())


def check_nuget(root, inventory, errors):
    packages, direct = parse_nuget_locks(root, errors)
    try:
        expected_direct = nuget_declared_direct(root) if (root / "src/dotnet/Directory.Build.props").is_file() else {}
    except (OSError, ElementTree.ParseError, AttributeError):
        errors.append("unable to parse direct NuGet declarations")
        expected_direct = {}
    if direct != expected_direct:
        errors.append("NuGet lock direct dependencies do not match project declarations")

    config = root / "src/dotnet/NuGet.Config"
    try:
        tree = ElementTree.parse(config)
    except (OSError, ElementTree.ParseError):
        errors.append("invalid or missing NuGet.Config")
        return
    xml = tree.getroot()
    if xml.tag != "configuration" or any(child.tag not in {"packageSources", "packageSourceMapping"} for child in xml):
        errors.append("NuGet.Config has unknown or unsafe sections")
    if xml.find("packageSourceCredentials") is not None or any("credential" in node.tag.lower() for node in xml.iter()):
        errors.append("NuGet.Config must not contain credentials")
    sources_node = xml.find("packageSources")
    source_children = list(sources_node) if sources_node is not None else []
    if not source_children or source_children[0].tag != "clear" or len([node for node in source_children if node.tag == "clear"]) != 1:
        errors.append("NuGet.Config must clear inherited sources")
    sources = {
        node.get("key"): node.get("value")
        for node in source_children
        if node.tag == "add" and set(node.attrib) <= {"key", "value", "protocolVersion"}
    }
    source_keys = [node.get("key") for node in source_children if node.tag == "add"]
    for key in {value for value in source_keys if source_keys.count(value) > 1}:
        errors.append(f"duplicate NuGet source key {key}")
    approved = inventory.get("approvedNuGetSources", [])
    if set(sources.values()) != set(approved) or len(sources) != len(approved):
        errors.append("NuGet.Config sources must exactly match approvedNuGetSources")
        for value in set(sources.values()) - set(approved):
            errors.append(f"unapproved NuGet source {value}")
    mapping_node = xml.find("packageSourceMapping")
    mappings = {}
    mapping_keys = []
    if mapping_node is None:
        errors.append("NuGet.Config requires packageSourceMapping")
    else:
        for source in mapping_node:
            if source.tag != "packageSource" or set(source.attrib) != {"key"} or source.get("key") not in sources:
                errors.append("NuGet.Config has invalid package source mapping")
                continue
            mapping_keys.append(source.get("key"))
            patterns = [node.get("pattern") for node in source if node.tag == "package" and set(node.attrib) == {"pattern"}]
            if not patterns or any(not pattern for pattern in patterns):
                errors.append("NuGet.Config has empty package source mapping")
            mappings[source.get("key")] = patterns
            for pattern in {value for value in patterns if patterns.count(value) > 1}:
                errors.append(f"duplicate NuGet mapping pattern {pattern}")
        for key in {value for value in mapping_keys if mapping_keys.count(value) > 1}:
            errors.append(f"duplicate NuGet mapping key {key}")
    seen_patterns = {}
    for source, patterns in mappings.items():
        for pattern in patterns:
            other = seen_patterns.setdefault(pattern.lower(), source)
            if other != source:
                errors.append(f"ambiguous NuGet mapping pattern {pattern}")
    for package in packages:
        matched = [source for source, patterns in mappings.items() if any(mapping_matches(pattern, package) for pattern in patterns)]
        if len(matched) > 1:
            errors.append(f"ambiguous NuGet mapping for {package}")
        elif not matched:
            errors.append(f"NuGet package has no source mapping: {package}")
    official_nuget = "https://api.nuget.org/v3/index.json"
    if approved == [official_nuget] and (
        sources != {"nuget.org": official_nuget} or mappings != {"nuget.org": ["*"]}
    ):
        errors.append("NuGet.Config keys and mapping patterns do not match the closed repository policy")


class OnlineClient:
    def __init__(self):
        ca = Path("/etc/ssl/cert.pem")
        self.context = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
        self.cache = {}

    def get(self, url, *, method="GET"):
        key = (method, url)
        if key not in self.cache:
            headers = {"User-Agent": "perf-sentinel-supply-chain/2", "Accept": "application/vnd.github+json"}
            token = os.environ.get("GITHUB_TOKEN")
            if token and urllib.parse.urlparse(url).hostname == "api.github.com":
                headers["Authorization"] = f"Bearer {token}"
            if not token and urllib.parse.urlparse(url).hostname == "api.github.com" and shutil.which("gh"):
                result = subprocess.run(["gh", "api", url], capture_output=True, check=False, timeout=12)
                if result.returncode:
                    raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
                self.cache[key] = (result.stdout, {})
            else:
                request = urllib.request.Request(url, headers=headers, method=method)
                with urllib.request.urlopen(request, timeout=12, context=self.context) as response:
                    self.cache[key] = (response.read(), dict(response.headers))
        return self.cache[key]

    def json(self, url):
        return json.loads(self.get(url)[0])

    def text(self, url):
        return self.get(url)[0].decode("utf-8", errors="replace")


def version_key(version):
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"[._-]", version.lstrip("v")))


def same_release_date(recorded, actual):
    try:
        expected = parse_instant(recorded, end_of_day=True)
        instant = parse_instant(actual)
    except ValueError:
        return False
    return instant.date() == expected.date() if len(recorded) == 10 else instant == expected


def latest_eligible(candidates, now):
    stable = [item for item in candidates if not PRERELEASE.search(item[0]) and item[1] <= now]
    return max(stable, key=lambda item: version_key(item[0])) if stable else None


def validate_release(dependency, candidates, now, label, compatible_prefix=None):
    expected = dependency["version"].lower()
    selected = next((item for item in candidates if item[0].lower() == expected), None)
    if not selected or not same_release_date(dependency["releasedAt"], selected[1].isoformat().replace("+00:00", "Z")):
        raise ValueError(f"{label} version/date mismatch")
    if compatible_prefix:
        candidates = [item for item in candidates if item[0].startswith(compatible_prefix)]
    eligible = latest_eligible(candidates, now)
    if not eligible or eligible[0].lower() != expected:
        raise ValueError(f"not latest eligible stable {label} ({eligible[0] if eligible else 'none'})")


def verify_github(client, dependency, now):
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/releases/tag/([^/]+)", dependency["source"])
    if not match or dependency.get("release") != match.group(2):
        raise ValueError("GitHub source/release mismatch")
    repo, tag = match.groups()
    releases = client.json(f"https://api.github.com/repos/{repo}/releases?per_page=100")
    candidates = [
        (item["tag_name"].lstrip("v"), parse_instant(item["published_at"]))
        for item in releases
        if not item["draft"]
        and not item["prerelease"]
        and item.get("published_at")
        and re.fullmatch(r"v?\d+(?:\.\d+)+", item["tag_name"])
    ]
    release_dependency = dict(dependency, version=tag.lstrip("v"))
    validate_release(release_dependency, candidates, now, "GitHub release")
    if dependency["kind"] == "github-action":
        refs = client.json(f"https://api.github.com/repos/{repo}/git/matching-refs/tags/{urllib.parse.quote(tag)}")
        ref = next((item for item in refs if item["ref"] == f"refs/tags/{tag}"), None)
        if ref and ref["object"]["type"] == "tag":
            tag_object = client.json(f"https://api.github.com/repos/{repo}/git/tags/{ref['object']['sha']}")
            ref = {"object": tag_object["object"]}
        if not ref or ref["object"]["type"] != "commit" or ref["object"]["sha"] != dependency["version"]:
            raise ValueError("action SHA does not match its stable release tag")
    elif dependency["version"] != tag.lstrip("v"):
        raise ValueError("tool version does not match release tag")


def verify_plugin(client, dependency, now):
    plugin_id = PLUGIN_IDS[dependency["name"]]
    index = client.text(f"https://plugins.gradle.org/plugin/{plugin_id}")
    versions = set(re.findall(rf'/plugin/{re.escape(plugin_id)}/([^"<]+)', index)) | {dependency["version"]}
    candidates = []
    for candidate in sorted((v for v in versions if not PRERELEASE.search(v)), key=version_key, reverse=True)[:12]:
        candidate_page = client.text(f"https://plugins.gradle.org/plugin/{plugin_id}/{candidate}")
        match = re.search(r"Created\s+(\d{1,2}\s+\w+\s+\d{4})\.", candidate_page)
        if match:
            candidates.append((candidate, datetime.strptime(match.group(1), "%d %B %Y").replace(tzinfo=timezone.utc)))
    validate_release(dependency, candidates, now, "Gradle plugin")


# `_now` is unused here, the six verify_* helpers share one signature for the dispatch below.
def verify_gradle(client, dependency, _now):
    data = client.json("https://services.gradle.org/versions/current")
    released = datetime.strptime(data["buildTime"], "%Y%m%d%H%M%S%z")
    if dependency["version"] != data["version"] or not same_release_date(dependency["releasedAt"], released.isoformat().replace("+00:00", "Z")):
        raise ValueError("Gradle version/date mismatch")
    expected = data["wrapperChecksum"] if dependency["name"] == "Gradle wrapper JAR" else data["checksum"]
    if dependency.get("sha256") != expected:
        raise ValueError("Gradle SHA-256 mismatch")


def verify_jetbrains_product(client, dependency, now):
    product = next((name for name in PRODUCT_CODES if dependency["name"].startswith(name + " ")), None)
    data = client.json(dependency["source"])[PRODUCT_CODES[product]]
    candidates = [(item["version"], parse_instant(item["date"], end_of_day=True)) for item in data if not PRERELEASE.search(item["version"])]
    wave = dependency["name"].rsplit(" ", 1)[-1]
    validate_release(dependency, candidates, now, "JetBrains product", wave)


def verify_maven(client, dependency, now):
    group, artifact = MAVEN_COORDINATES[dependency["name"]]
    base = f"https://repo.maven.apache.org/maven2/{group.replace('.', '/')}/{artifact}"
    metadata = ElementTree.fromstring(client.get(f"{base}/maven-metadata.xml")[0])
    versions = [node.text for node in metadata.findall(".//version") if node.text and not PRERELEASE.search(node.text)]
    candidates = []
    for version in sorted(versions, key=version_key, reverse=True)[:8]:
        _, headers = client.get(f"{base}/{version}/{artifact}-{version}.pom", method="HEAD")
        published = parsedate_to_datetime(headers["Last-Modified"]).astimezone(timezone.utc)
        candidates.append((version, published))
    validate_release(dependency, candidates, now, "Maven release")


def verify_nuget(client, dependency, now):
    package = NUGET_PACKAGES[dependency["name"]]
    registration = client.json(f"https://api.nuget.org/v3/registration5-semver1/{package.lower()}/index.json")
    items = []
    for page in registration["items"]:
        leaves = page.get("items") or client.json(page["@id"])["items"]
        items.extend(leaves)
    candidates = []
    for item in items:
        catalog = item["catalogEntry"] if isinstance(item["catalogEntry"], dict) else client.json(item["catalogEntry"])
        version = catalog["version"]
        if not PRERELEASE.search(version):
            candidates.append((version, parse_instant(catalog["published"]), item["packageContent"]))
    if dependency["name"] == "coverlet.collector":
        if dependency.get("compatibility") != "netstandard2.0":
            raise ValueError("coverlet collector requires its net472 compatibility contract")
        compatible = []
        for version, published, content in sorted(candidates, key=lambda entry: version_key(entry[0]), reverse=True):
            package_bytes = client.get(content)[0]
            if len(package_bytes) > 64 * 1024 * 1024:
                raise ValueError("coverlet package exceeds audit size bound")
            try:
                with zipfile.ZipFile(io.BytesIO(package_bytes)) as package_archive:
                    if "build/netstandard2.0/coverlet.collector.targets" in package_archive.namelist():
                        compatible.append((version, published))
            except zipfile.BadZipFile as error:
                raise ValueError("coverlet package is not a valid NuGet archive") from error
            if compatible:
                break
        validate_release(dependency, compatible, now, "net472-compatible NuGet release")
        return
    compatible = dependency.get("release")
    validate_release(dependency, [(version, published) for version, published, _ in candidates], now, "NuGet release", compatible + "." if compatible else None)


def verify_container(client, dependency, now):
    repository = CONTAINER_REPOSITORIES[dependency["name"]]
    data = client.json(f"https://hub.docker.com/v2/repositories/{repository}/tags/{dependency['release']}")
    if dependency["version"] != data.get("digest"):
        raise ValueError("container digest mismatch")
    tags = client.json(f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=30")["results"]
    candidates = [
        (item["name"], parse_instant(item["last_updated"]))
        for item in tags
        if re.fullmatch(r"\d{4}\.\d+", item["name"])
    ]
    tag_dependency = dict(dependency, version=dependency["release"])
    validate_release(tag_dependency, candidates, now, "container")


def check_online(inventory, errors, now):
    client = OnlineClient()
    for dependency in inventory.get("dependencies", []):
        try:
            kind = dependency["kind"]
            if kind == "build-tool":
                verify_gradle(client, dependency, now)
            elif dependency["name"] in PLUGIN_IDS:
                verify_plugin(client, dependency, now)
            elif kind == "jetbrains-product":
                verify_jetbrains_product(client, dependency, now)
            elif dependency["name"] in MAVEN_COORDINATES:
                verify_maven(client, dependency, now)
            elif kind == "nuget":
                verify_nuget(client, dependency, now)
            elif kind in {"github-action", "audited-tool"} and "github.com" in dependency["source"]:
                verify_github(client, dependency, now)
            elif kind == "container":
                verify_container(client, dependency, now)
            else:
                raise ValueError("no official validator for dependency kind/source")
        except Exception as exc:
            errors.append(f"online validation failed for {dependency.get('name')}: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    now = datetime.now(timezone.utc)
    errors = []
    inventory = check_inventory(root, errors, now)
    check_declarations(root, inventory, errors)
    check_gradle(root, inventory, errors)
    check_build_files(root, errors)
    check_nuget(root, inventory, errors)
    if args.online and not errors:
        check_online(inventory, errors, now)
    if errors:
        print("\n".join(f"error: {error}" for error in errors), file=sys.stderr)
        return 1
    print("Supply-chain inputs are locked, complete, and stable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
