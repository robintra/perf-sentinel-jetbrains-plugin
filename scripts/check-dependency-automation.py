#!/usr/bin/env python3
"""Validate exclusive, stable-only dependency automation policy."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path


def load_supply_chain_checker():
    path = Path(__file__).resolve().parent / "check-supply-chain.py"
    spec = importlib.util.spec_from_file_location("check_supply_chain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The automerge rule waits exactly as long as the freshness check does before
# calling a pin behind, so the two windows can never drift apart.
RELEASE_AGE = f"{load_supply_chain_checker().FRESHNESS_GRACE.days} days"
HOOK_COMMAND = "python3 scripts/sync-supply-chain.py --online"


EXPECTED_MANAGERS = {"gradle", "gradle-wrapper", "nuget", "custom.regex", "github-actions"}
EXPECTED_JETBRAINS_CODES = {"GO", "IIU", "PCP", "PS", "RD", "RM", "RR", "WS"}
JETBRAINS_PRODUCT_CODES = {
    "GoLand": "GO", "IntellijIdea": "IIU", "PhpStorm": "PS", "PyCharm": "PCP",
    "PyCharmProfessional": "PCP", "Rider": "RD", "RubyMine": "RM", "RustRover": "RR",
    "WebStorm": "WS",
}
FORBIDDEN_DELAY_KEYS = {"cooldown", "stabilityDays"}
FORBIDDEN_AUTOMERGE_KEYS = {"automergeType"}
RENOVATE_KEYS = {
    "$schema", "automergeStrategy", "customDatasources", "customManagers", "dependencyDashboard",
    "enabledManagers", "internalChecksFilter", "ignoreUnstable", "labels", "lockFileMaintenance",
    "packageRules", "osvVulnerabilityAlerts", "platformAutomerge", "postUpgradeTasks",
    "prConcurrentLimit", "rebaseWhen", "rangeStrategy", "respectLatest", "schedule", "timezone",
    "vulnerabilityAlerts",
}
CUSTOM_MANAGER_KEYS = {"customType", "datasourceTemplate", "depNameTemplate", "managerFilePatterns", "matchStrings", "versioningTemplate"}
EXPECTED_PACKAGE_RULES = [
    {
        "description": "Disable inherited auto-merge for every dependency",
        "matchPackageNames": ["*"],
        "automerge": False,
    },
    {
        "description": "Group ordinary non-major Gradle and NuGet updates",
        "matchManagers": ["gradle", "gradle-wrapper", "nuget", "custom.regex"],
        "matchUpdateTypes": ["minor", "patch"],
        "groupName": "ordinary-build-dependencies",
    },
    {
        "description": "Group ordinary non-major GitHub Actions updates",
        "matchManagers": ["github-actions"],
        "matchUpdateTypes": ["minor", "patch", "digest"],
        "groupName": "ordinary-github-actions",
    },
    {
        "description": "Keep the Renovate image update on its own",
        "matchPackageNames": ["renovate/renovate"],
        "groupName": "renovate-image",
    },
    {
        "description": "Merge non-major updates on their own once they have matured",
        "matchUpdateTypes": ["minor", "patch", "digest"],
        "minimumReleaseAge": RELEASE_AGE,
        "automerge": True,
    },
    {
        "description": "Use stable Maven releases rather than repository publication order",
        "matchDatasources": ["maven"],
        "respectLatest": True,
    },
    {
        "description": "Keep JetBrains 2025.3 targets on their compatibility wave",
        "matchPackageNames": ["GO", "IIU", "JetBrains.Rider.SDK", "PCP", "PS", "RD", "RM", "RR", "WS"],
        "matchCurrentValue": "/^2025\\.3\\./",
        "allowedVersions": "/^2025\\.3\\./",
    },
    {
        "description": "Keep JetBrains 2026.2 targets on their compatibility wave",
        "matchPackageNames": ["GO", "IIU", "PCP", "PS", "RD", "RM", "RR", "WS"],
        "matchCurrentValue": "/^2026\\.2\\./",
        "allowedVersions": "/^2026\\.2\\./",
    },
    {
        "description": "Keep Coverlet compatible with the Rider net472 test host",
        "matchPackageNames": ["coverlet.collector"],
        "allowedVersions": "<7.0.0",
    },
    {
        "description": "Keep the JDK on the Java 21 line the plugin targets",
        "matchPackageNames": ["java-jdk"],
        "allowedVersions": "/^21\\./",
    },
    {
        "description": "Move the Rider IDE and SDKs together, merged by hand",
        "matchPackageNames": ["JetBrains.ReSharper.SDK.Tests", "JetBrains.Rider.SDK", "RD"],
        "groupName": "rider-ide-and-sdk",
        "automerge": False,
    },
]
EXPECTED_CUSTOM_MANAGERS = {
    "IIU": {
        "files": ["/^build\\.gradle\\.kts$/"],
        "patterns": ["intellijIdea\\(\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)", "create\\(\\s*IntelliJPlatformType\\.IntellijIdea\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"],
        "datasource": "custom.jetbrains-products", "versioning": "loose",
    },
    "RD": {
        "files": ["/^build\\.gradle\\.kts$/", "/^rider-frontend/build\\.gradle\\.kts$/"],
        "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.Rider[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.Rider\\s*,\\s*\"(?<currentValue>[0-9.]+)\"", "rider\\(\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"],
        "datasource": "custom.jetbrains-products", "versioning": "loose",
    },
    "PCP": {
        "files": ["/^build\\.gradle\\.kts$/"],
        "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.PyCharmProfessional[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.PyCharm(?:Professional)?\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"],
        "datasource": "custom.jetbrains-products", "versioning": "loose",
    },
    "PS": {"files": ["/^build\\.gradle\\.kts$/"], "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.PhpStorm[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.PhpStorm\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"], "datasource": "custom.jetbrains-products", "versioning": "loose"},
    "RR": {"files": ["/^build\\.gradle\\.kts$/"], "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.RustRover[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.RustRover\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"], "datasource": "custom.jetbrains-products", "versioning": "loose"},
    "RM": {"files": ["/^build\\.gradle\\.kts$/"], "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.RubyMine[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.RubyMine\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"], "datasource": "custom.jetbrains-products", "versioning": "loose"},
    "WS": {"files": ["/^build\\.gradle\\.kts$/"], "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.WebStorm[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.WebStorm\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"], "datasource": "custom.jetbrains-products", "versioning": "loose"},
    "GO": {"files": ["/^build\\.gradle\\.kts$/"], "patterns": ["type\\s*=\\s*IntelliJPlatformType\\.GoLand[\\s\\S]{0,120}?version\\s*=\\s*\"(?<currentValue>[0-9.]+)\"", "create\\(\\s*IntelliJPlatformType\\.GoLand\\s*,\\s*\"(?<currentValue>[0-9.]+)\"\\s*\\)"], "datasource": "custom.jetbrains-products", "versioning": "loose"},
    "JetBrains.Rider.SDK": {"files": ["/^src/dotnet/Plugin\\.props$/"], "patterns": ["<SdkVersion>(?<currentValue>[0-9.]+)</SdkVersion>"], "datasource": "nuget", "versioning": "nuget"},
    "java-jdk": {"files": ["/^\\.java-version$/"], "patterns": ["(?<currentValue>[0-9]+\\.[0-9]+\\.[0-9]+\\+\\S+)"], "datasource": "java-version", "versioning": "loose"},
    "renovate/renovate": {"files": ["/^\\.github/workflows/renovate\\.yml$/"], "patterns": ["renovate-version:\\s*\"?(?<currentValue>[0-9.]+)@(?<currentDigest>sha256:[0-9a-f]{64})\"?"], "datasource": "docker", "versioning": "docker"},
}
EXPECTED_DATASOURCE = {
    "defaultRegistryUrlTemplate": "https://data.services.jetbrains.com/products/releases?code={{{packageName}}}&type=release",
    "format": "json",
    "transformTemplates": [
        '{"releases": $reduce($each($, function($items) { $items }), $append, []).{"version": version, "releaseTimestamp": date}}'
    ],
}
EXPECTED_POST_UPGRADE_TASKS = {
    "commands": [HOOK_COMMAND],
    "fileFilters": ["config/supply-chain.json", "gradle.lockfile", "gradle/verification-metadata.xml"],
    "executionMode": "branch",
}
EXPECTED_GLOBAL_CONFIG = {
    "platform": "github",
    "repositories": ["robintra/perf-sentinel-jetbrains-plugin"],
    "onboarding": False,
    "requireConfig": "required",
    "binarySource": "install",
    "allowedCommands": ["^python3 scripts/sync-supply-chain\\.py --online$"],
    "dryRun": "full",
}


def load_json(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def walk_key_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_key_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_key_values(child)


def validate(root: Path):
    errors = []
    try:
        renovate = load_json(root / ".github/renovate.json")
    except (OSError, ValueError, TypeError) as error:
        return [f"renovate configuration is invalid: {error}"]
    try:
        global_config = load_json(root / ".github/renovate-global.json")
    except (OSError, ValueError, TypeError) as error:
        return [f"Renovate global configuration is invalid: {error}"]
    if (root / ".github/dependabot.yml").exists() or (root / ".github/dependabot.yaml").exists():
        errors.append("Dependabot version updates must be absent: Renovate owns GitHub Actions")
    try:
        policy = (root / "DEPENDENCY-POLICY.md").read_text(encoding="utf-8")
    except OSError as error:
        return [f"dependency policy is unavailable: {error}"]

    if not isinstance(renovate, dict):
        return ["Renovate configuration schema is not closed"]
    if set(renovate) != RENOVATE_KEYS:
        errors.append("Renovate configuration schema is not closed")
    if not isinstance(global_config, dict):
        return ["Renovate global configuration schema is not closed"]
    if global_config != EXPECTED_GLOBAL_CONFIG or not re.fullmatch(global_config["allowedCommands"][0], HOOK_COMMAND):
        errors.append("Renovate global configuration must allow only the sync hook")

    managers = renovate.get("enabledManagers") if isinstance(renovate, dict) else None
    manager_set = set(managers) if isinstance(managers, list) and all(isinstance(item, str) for item in managers) else set()
    manager_count = len(managers) if isinstance(managers, list) else -1
    if manager_set != EXPECTED_MANAGERS or manager_count != len(EXPECTED_MANAGERS):
        errors.append("Renovate manager ownership is not exact")
    expected_top_level = {
        "$schema": "https://docs.renovatebot.com/renovate-schema.json",
        "dependencyDashboard": True,
        "rangeStrategy": "bump",
    }
    if any(renovate.get(key) != value for key, value in expected_top_level.items()):
        errors.append("Renovate top-level policy is not canonical")

    keys = set(walk_keys(renovate))
    if keys & FORBIDDEN_DELAY_KEYS:
        errors.append("stable releases must be immediate; release delays are forbidden")
    if keys & FORBIDDEN_AUTOMERGE_KEYS:
        errors.append("auto-merge is allowed only through the matured non-major rule")
    ages = [value for key, value in walk_key_values(renovate) if key == "minimumReleaseAge"]
    if ages != [RELEASE_AGE]:
        errors.append(f"a release delay is allowed only on the automerge rule, at {RELEASE_AGE}")
    automerge_values = [value for key, value in walk_key_values(renovate) if key == "automerge"]
    if automerge_values != [False, True, False]:
        errors.append("auto-merge is allowed only through the matured non-major rule")
    if renovate.get("platformAutomerge") is not False or renovate.get("automergeStrategy") != "squash":
        errors.append("matured updates must merge through Renovate's own merge, squashed, never native auto-merge")
    if renovate.get("internalChecksFilter") != "none" or renovate.get("rebaseWhen") != "behind-base-branch":
        errors.append("pull requests must open immediately and stay up to date with main")
    if renovate.get("postUpgradeTasks") != EXPECTED_POST_UPGRADE_TASKS:
        errors.append("the sync hook must be exactly the supply-chain synchronisation")
    if renovate.get("ignoreUnstable") is not True or renovate.get("respectLatest") is not True:
        errors.append("stable-only Renovate policy is required")
    if renovate.get("vulnerabilityAlerts") != {"enabled": False} or renovate.get("osvVulnerabilityAlerts") is not False:
        errors.append("Dependabot must be the only security alert owner")
    limit = renovate.get("prConcurrentLimit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5:
        errors.append("Renovate pull requests must be bounded")
    if renovate.get("labels") != ["dependencies"] or renovate.get("timezone") != "Europe/Paris":
        errors.append("Renovate labels and timezone are not canonical")
    if renovate.get("schedule") != ["after 6:00am and before 10:00am"]:
        errors.append("Renovate schedule is not canonical")
    # A global relock drops bundled-module entries and locks prerelease IDEs; see the design.
    if renovate.get("lockFileMaintenance") != {"enabled": False}:
        errors.append("Renovate lock maintenance must stay disabled")
    if "stable releases are eligible immediately" not in policy:
        errors.append("policy must state immediate stable eligibility")

    custom = renovate.get("customManagers")
    if not isinstance(custom, list):
        errors.append("JetBrains version surfaces require custom managers")
    else:
        if len(custom) != len(EXPECTED_CUSTOM_MANAGERS):
            errors.append("Renovate custom manager contract is not exact")
        if any(not isinstance(manager, dict) or set(manager) != CUSTOM_MANAGER_KEYS for manager in custom):
            errors.append("Renovate custom manager schema is not closed")
        codes = {
            manager.get("depNameTemplate")
            for manager in custom
            if isinstance(manager, dict)
            and isinstance(manager.get("depNameTemplate"), str)
            and manager.get("datasourceTemplate") == "custom.jetbrains-products"
        }
        if codes != EXPECTED_JETBRAINS_CODES:
            errors.append("JetBrains version surfaces are incomplete")
        sdk = [
            manager
            for manager in custom
            if isinstance(manager, dict)
            and manager.get("depNameTemplate") == "JetBrains.Rider.SDK"
            and manager.get("datasourceTemplate") == "nuget"
            and "/^src/dotnet/Plugin\\.props$/" in manager.get("managerFilePatterns", [])
        ]
        if len(sdk) != 1:
            errors.append("Rider SDK custom manager is missing or duplicated")
        actual_managers = {}
        for manager in custom:
            if not isinstance(manager, dict) or not isinstance(manager.get("depNameTemplate"), str):
                continue
            actual_managers[manager["depNameTemplate"]] = {
                "files": manager.get("managerFilePatterns"), "patterns": manager.get("matchStrings"),
                "datasource": manager.get("datasourceTemplate"), "versioning": manager.get("versioningTemplate"),
            }
        if actual_managers != EXPECTED_CUSTOM_MANAGERS:
            errors.append("Renovate custom manager contract is not exact")

    datasources = renovate.get("customDatasources")
    jetbrains_datasource = datasources.get("jetbrains-products") if isinstance(datasources, dict) else None
    if jetbrains_datasource != EXPECTED_DATASOURCE:
        errors.append("JetBrains versions must use the official release service")

    rules = renovate.get("packageRules")
    if rules != EXPECTED_PACKAGE_RULES:
        errors.append("Renovate package rules are not exact")
    wave_rules = {
        (rule.get("matchCurrentValue"), rule.get("allowedVersions"))
        for rule in rules if isinstance(rules, list) and isinstance(rule, dict)
    } if isinstance(rules, list) else set()
    expected_wave_rules = {
        ("/^2025\\.3\\./", "/^2025\\.3\\./"),
        ("/^2026\\.2\\./", "/^2026\\.2\\./"),
    }
    if not expected_wave_rules <= wave_rules:
        errors.append("JetBrains updates must remain within their compatibility wave")

    actual_codes = set()
    unknown_products = set()
    source_text = {}
    for relative in ("build.gradle.kts", "rider-frontend/build.gradle.kts", "src/dotnet/Plugin.props"):
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"JetBrains build declaration is unavailable: {error}")
            continue
        source_text[relative] = text
        for product in re.findall(r"IntelliJPlatformType\.([A-Za-z]+)", text):
            code = JETBRAINS_PRODUCT_CODES.get(product)
            if code:
                actual_codes.add(code)
            else:
                unknown_products.add(product)
        if "intellijIdea(" in text:
            actual_codes.add("IIU")
        if "rider(" in text:
            actual_codes.add("RD")
    if unknown_products or actual_codes != EXPECTED_JETBRAINS_CODES:
        errors.append("unowned JetBrains product version surface")

    semantic_versions = Counter()
    for text in source_text.values():
        for product, version in re.findall(
            r"create\s*\(\s*IntelliJPlatformType\.([A-Za-z]+)\s*,\s*\"([0-9.]+)\"", text
        ):
            if product in JETBRAINS_PRODUCT_CODES:
                semantic_versions[(JETBRAINS_PRODUCT_CODES[product], version)] += 1
        for product, version in re.findall(
            r"type\s*=\s*IntelliJPlatformType\.([A-Za-z]+)(?:(?!\btype\s*=)[\s\S]){0,500}?\bversion\s*=\s*\"([0-9.]+)\"",
            text,
        ):
            if product in JETBRAINS_PRODUCT_CODES:
                semantic_versions[(JETBRAINS_PRODUCT_CODES[product], version)] += 1
        for code, helper in (("IIU", "intellijIdea"), ("RD", "rider")):
            for version in re.findall(rf"\b{helper}\s*\(\s*\"([0-9.]+)\"", text):
                semantic_versions[(code, version)] += 1
        for version in re.findall(r"<SdkVersion>\s*([0-9.]+)\s*</SdkVersion>", text):
            semantic_versions[("JetBrains.Rider.SDK", version)] += 1

    extracted_versions = Counter()
    if isinstance(custom, list):
        for manager in custom:
            dep_name = manager.get("depNameTemplate") if isinstance(manager, dict) else None
            if not isinstance(dep_name, str) or dep_name not in EXPECTED_CUSTOM_MANAGERS:
                continue
            code = dep_name
            for relative in source_text:
                file_patterns = manager.get("managerFilePatterns", [])
                if not any(
                    isinstance(pattern, str)
                    and pattern.startswith("/") and pattern.endswith("/")
                    and re.fullmatch(pattern[1:-1], relative)
                    for pattern in file_patterns
                ):
                    continue
                for pattern in manager.get("matchStrings", []):
                    python_pattern = pattern.replace("(?<currentValue>", "(?P<currentValue>")
                    for match in re.finditer(python_pattern, source_text[relative]):
                        extracted_versions[(code, match.group("currentValue"))] += 1
    if semantic_versions != extracted_versions:
        errors.append("JetBrains version declaration is not extracted by its custom manager")

    return errors


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else Path(__file__).resolve().parents[1]
    if len(sys.argv) > 2:
        raise SystemExit("usage: check-dependency-automation.py [repository]")
    errors = validate(root)
    if errors:
        print(*errors, sep="\n", file=sys.stderr)
        raise SystemExit(1)
    print("dependency automation: OK")


if __name__ == "__main__":
    main()
