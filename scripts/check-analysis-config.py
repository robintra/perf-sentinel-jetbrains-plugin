#!/usr/bin/env python3
"""Validate the closed Qodana and analysis-secret contracts."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import unicodedata
import xml.etree.ElementTree as ElementTree
from pathlib import Path


MAX_CONFIG_BYTES = 256 * 1024
MAX_INVENTORY_BYTES = 1024 * 1024
MAX_WORKFLOW_BYTES = 1024 * 1024
MAX_LINE_LENGTH = 4096
JVM_DIGEST = "sha256:8ff36b5cebc0a6d720f77dcf3e0a94a03c39b4c42c3724a99ce5f7e462e42f99"
DOTNET_DIGEST = "sha256:083e222c54d976b29a3118036559340a18e804f82d30947548468443ca60de59"
IMAGE = re.compile(r"^(jetbrains/[a-z0-9-]+):(\d{4}\.\d+)@(sha256:[0-9a-f]{64})$")
PROPERTY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
YAML_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(?: (.*))?$")
DRIVE_PATH = re.compile(r"^[A-Za-z]:/")
STATIC_SECRET = re.compile(r"\s*secrets\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*", re.IGNORECASE)
SECRET_VALUE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|[A-Fa-f0-9]{40,})")
FORBIDDEN_XML = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class AnalysisError(ValueError):
    pass


class DuplicateKey(ValueError):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKey(f"duplicate key {key}")
        value[key] = item
    return value


def read_utf8(path: Path, maximum: int, label: str) -> str:
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
    except OSError as error:
        raise AnalysisError(f"{label} is missing") from error
    if not payload or len(payload) > maximum:
        raise AnalysisError(f"{label} violates the {maximum}-byte size bound")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise AnalysisError(f"{label} must use strict UTF-8 without BOM")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AnalysisError(f"{label} must use strict UTF-8 without BOM") from error
    if any(ord(character) < 32 and character != "\n" for character in text):
        raise AnalysisError(f"{label} contains a forbidden control character")
    if any(len(line) > MAX_LINE_LENGTH for line in text.splitlines()):
        raise AnalysisError(f"{label} contains an invalid or oversized line")
    return text


def read_json(path: Path, maximum: int, label: str):
    try:
        return json.loads(read_utf8(path, maximum, label), object_pairs_hook=unique_object)
    except DuplicateKey as error:
        raise AnalysisError(f"{label} contains a duplicate key") from error
    except json.JSONDecodeError as error:
        raise AnalysisError(f"{label} is not valid JSON") from error


def fields(value, expected: set[str], label: str) -> None:
    if type(value) is not dict:
        raise AnalysisError(f"{label} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise AnalysisError(f"{label} has unknown field(s): {', '.join(sorted(unknown))}")
    if missing:
        raise AnalysisError(f"{label} is missing field(s): {', '.join(sorted(missing))}")


def scalar(value: str):
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise AnalysisError("invalid quoted YAML scalar") from error
        if type(parsed) is not str:
            raise AnalysisError("quoted YAML scalar must be a string")
        return parsed
    if value in {"true", "false"}:
        return value == "true"
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        return int(value)
    if not value or value[0] in "'[{&*!|>" or " #" in value:
        raise AnalysisError("unsupported YAML scalar")
    return value


def split_yaml_entry(content: str) -> tuple[str, str]:
    match = YAML_KEY.fullmatch(content)
    if match is None:
        raise AnalysisError("unsupported YAML mapping entry")
    return match.group(1), match.group(2) or ""


def parse_mapping(tokens, index: int, indent: int, initial=None):
    result = {} if initial is None else initial
    while index < len(tokens):
        line_indent, content = tokens[index]
        if line_indent < indent:
            break
        if line_indent != indent or content.startswith("- "):
            raise AnalysisError("invalid YAML mapping indentation")
        key, raw = split_yaml_entry(content)
        if key in result:
            raise AnalysisError(f"duplicate YAML key {key}")
        index += 1
        if raw:
            result[key] = scalar(raw)
        else:
            if index >= len(tokens) or tokens[index][0] != indent + 2:
                raise AnalysisError(f"YAML key {key} has no nested value")
            result[key], index = parse_block(tokens, index, indent + 2)
    return result, index


def parse_list(tokens, index: int, indent: int):
    result = []
    while index < len(tokens):
        line_indent, content = tokens[index]
        if line_indent < indent:
            break
        if line_indent != indent or not content.startswith("- "):
            raise AnalysisError("invalid YAML list indentation")
        raw = content[2:]
        index += 1
        if YAML_KEY.fullmatch(raw):
            key, value = split_yaml_entry(raw)
            item = {key: scalar(value)} if value else {}
            if not value:
                if index >= len(tokens) or tokens[index][0] != indent + 2:
                    raise AnalysisError(f"YAML key {key} has no nested value")
                item[key], index = parse_block(tokens, index, indent + 2)
            if index < len(tokens) and tokens[index][0] == indent + 2 and not tokens[index][1].startswith("- "):
                item, index = parse_mapping(tokens, index, indent + 2, item)
            result.append(item)
        else:
            result.append(scalar(raw))
    return result, index


def parse_block(tokens, index: int, indent: int):
    if tokens[index][0] != indent:
        raise AnalysisError("invalid YAML indentation")
    if tokens[index][1].startswith("- "):
        return parse_list(tokens, index, indent)
    return parse_mapping(tokens, index, indent)


def parse_yaml(path: Path, label: str) -> tuple[dict, str]:
    text = read_utf8(path, MAX_CONFIG_BYTES, label)
    tokens = []
    for line in text.splitlines():
        if "\t" in line or line.rstrip() != line:
            raise AnalysisError(f"{label} has unstable whitespace")
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent % 2:
            raise AnalysisError(f"{label} indentation must use two spaces")
        tokens.append((indent, stripped))
    if not tokens or tokens[0][0] != 0:
        raise AnalysisError(f"{label} has no root mapping")
    value, index = parse_block(tokens, 0, 0)
    if index != len(tokens) or type(value) is not dict:
        raise AnalysisError(f"{label} has trailing or non-mapping YAML")
    return value, text


def normalized_path(value, label: str) -> str:
    if type(value) is not str or not value or "\\" in value or any(ord(char) < 32 for char in value):
        raise AnalysisError(f"{label} is not a stable repository path")
    value = unicodedata.normalize("NFC", value)
    plain = value.replace("**", "x").replace("*", "x")
    if (
        posixpath.isabs(plain)
        or DRIVE_PATH.match(plain)
        or ".." in plain.split("/")
        or posixpath.normpath(plain).startswith("../")
        or posixpath.normpath(value) != value
    ):
        raise AnalysisError(f"{label} is not a stable repository path")
    return value


def suppression_comments(text: str) -> dict[str, str]:
    result = {}
    pending = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(stripped[1:].strip())
            continue
        match = re.fullmatch(r"- name: ([A-Za-z0-9]+)", stripped)
        if match:
            result[match.group(1)] = " ".join(pending)
        pending = []
    return result


def parse_image(value, repository: str, release: str, digest: str) -> None:
    match = IMAGE.fullmatch(value) if type(value) is str else None
    if match is None or match.groups() != (repository, release, digest):
        raise AnalysisError("configuration requires an immutable eligible Qodana image")


def validate_jvm_qodana(config: dict, text: str) -> None:
    fields(config, {"version", "linter", "profile", "failureConditions", "exclude"}, "JVM Qodana config")
    if config["version"] != "1.0":
        raise AnalysisError("JVM Qodana version must be string 1.0")
    parse_image(config["linter"], "jetbrains/qodana-jvm-community", "2026.2", JVM_DIGEST)
    if config["profile"] != {"path": ".qodana/profiles/plugin.yaml"}:
        raise AnalysisError("JVM Qodana must use the local plugin profile")
    conditions = config["failureConditions"]
    if type(conditions) is not dict or set(conditions) != {"severityThresholds"}:
        raise AnalysisError("JVM Qodana failure conditions are not closed")
    thresholds = conditions["severityThresholds"]
    if type(thresholds) is not dict or set(thresholds) != {"critical", "high"} or any(
        type(thresholds.get(name)) is not int or thresholds[name] != 0 for name in ("critical", "high")
    ):
        raise AnalysisError("JVM Qodana critical and high thresholds must be integer zero")
    exclusions = config["exclude"]
    if type(exclusions) is not list:
        raise AnalysisError("JVM Qodana exclusions must be an array")
    expected = {
        "All": ["build", "protocol/build", "rider-frontend/build"],
        "PluginXmlValidity": ["src/main/resources/META-INF/plugin.xml", "src/main/resources/META-INF/perf-sentinel-rider.xml"],
        "SpellCheckingInspection": ["src/dotnet/PerfSentinel.Rider/CSharpSymbolResolver.cs", "src/dotnet/PerfSentinel.Rider.Tests/CSharpSymbolResolverTests.cs"],
        "UnusedSymbol": ["protocol/src/main/kotlin/model/rider/PerfSentinelModel.kt"],
    }
    actual = {}
    for index, exclusion in enumerate(exclusions):
        fields(exclusion, {"name", "paths"}, f"JVM Qodana exclusion[{index}]")
        name, paths = exclusion["name"], exclusion["paths"]
        if type(name) is not str or name in actual or type(paths) is not list or any(type(path) is not str for path in paths):
            raise AnalysisError("JVM Qodana exclusions must have unique string names and paths")
        for path in paths:
            normalized_path(path, "Qodana exclusion")
        actual[name] = paths
    if actual.get("All") != expected["All"]:
        raise AnalysisError("JVM Qodana All exclusion is broader than generated/build outputs")
    if actual != expected:
        raise AnalysisError("JVM Qodana narrow suppression set has drifted")
    comments = suppression_comments(text)
    markers = {"PluginXmlValidity": "Plugin Verifier", "SpellCheckingInspection": "CLR", "UnusedSymbol": "RDGen"}
    if any(marker not in comments.get(name, "") for name, marker in markers.items()):
        raise AnalysisError("JVM Qodana narrow suppression rationale is missing")


def validate_dotnet_qodana(config: dict) -> None:
    fields(config, {"version", "linter", "withinDocker", "profile", "onlyDirectory", "dotnet", "failThreshold"}, "Qodana .NET config")
    if config["linter"] != "qodana-dotnet" or type(config["withinDocker"]) is not bool or config["withinDocker"]:
        raise AnalysisError("Qodana .NET net472 analysis must use native mode")
    expected = {
        "version": "1.0",
        "profile": {"name": "qodana.recommended"},
        "onlyDirectory": "src/dotnet",
        "dotnet": {
            "project": "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj",
            "configuration": "Release",
        },
    }
    if any(config[name] != value for name, value in expected.items()):
        raise AnalysisError("Qodana .NET must analyze only src/dotnet through the Release test project")
    if type(config["failThreshold"]) is not int or config["failThreshold"] != 0:
        raise AnalysisError("Qodana .NET failThreshold must be integer zero")


def validate_secret_inventory(inventory) -> set[str]:
    if type(inventory) is dict:
        for secret in inventory.get("secrets", []):
            if type(secret) is dict and any(key.casefold() in {"value", "password", "credential", "tokenvalue", "apikey", "privatekey"} for key in secret):
                raise AnalysisError("secret inventory contains a value-like field")
    fields(inventory, {"schemaVersion", "secrets"}, "secret inventory")
    if type(inventory["schemaVersion"]) is not int or inventory["schemaVersion"] != 1:
        raise AnalysisError("secret inventory schemaVersion must be integer 1")
    if type(inventory["secrets"]) is not list:
        raise AnalysisError("secret inventory secrets must be an array")
    expected_scopes = {
        "CERTIFICATE_CHAIN": ["jetbrains-release"],
        "PRIVATE_KEY": ["jetbrains-release"],
        "PRIVATE_KEY_PASSWORD": ["jetbrains-release"],
        "PUBLISH_TOKEN": ["jetbrains-release"],
        "QODANA_TOKEN": ["qodana-jvm", "qodana-rider"],
    }
    declared_names = [item.get("name") for item in inventory["secrets"] if type(item) is dict]
    if set(declared_names) != set(expected_scopes) or len(declared_names) != len(expected_scopes):
        raise AnalysisError("secret inventory must contain the exact secret set")
    names = []
    for index, secret in enumerate(inventory["secrets"]):
        fields(secret, {"name", "owner", "trustedJobScope", "purpose", "rotationProcedure"}, f"secret[{index}]")
        for key in ("name", "owner", "purpose", "rotationProcedure"):
            if type(secret[key]) is not str or not secret[key].strip() or len(secret[key]) > 1024:
                raise AnalysisError(f"secret[{index}].{key} must be a non-empty string")
        if secret["owner"] != "Maintainers":
            raise AnalysisError(f"secret[{index}] owner must be Maintainers")
        scope = secret["trustedJobScope"]
        if type(scope) is not list or any(type(item) is not str for item in scope):
            raise AnalysisError(f"secret[{index}] trustedJobScope must be a string array")
        if scope != expected_scopes.get(secret["name"]):
            raise AnalysisError(f"secret[{index}] trusted-job scope has drifted")
        for key, value in secret.items():
            if key != "name" and isinstance(value, str) and SECRET_VALUE.search(value):
                raise AnalysisError("secret inventory contains a secret-like value")
        names.append(secret["name"])
    if set(names) != set(expected_scopes) or len(names) != len(expected_scopes):
        raise AnalysisError("secret inventory must contain the exact secret set")
    return set(names)


def workflow_expressions(text: str, label: str):
    cursor = 0
    while True:
        start = text.find("${{", cursor)
        if start < 0:
            return
        index = start + 3
        quoted = False
        code = []
        while index < len(text):
            if quoted:
                if text[index] == "'":
                    if index + 1 < len(text) and text[index + 1] == "'":
                        index += 2
                        continue
                    quoted = False
                index += 1
                continue
            if text[index] == "'":
                quoted = True
                code.append(" ")
                index += 1
                continue
            if text.startswith("}}", index):
                yield text[start + 3:index], "".join(code)
                cursor = index + 2
                break
            code.append(text[index])
            index += 1
        else:
            raise AnalysisError(f"workflow expression in {label} is unterminated")


def validate_workflow_secrets(root: Path, inventory_names: set[str]) -> None:
    workflow_root = root / ".github" / "workflows"
    if not workflow_root.is_dir():
        return
    total = 0
    for path in sorted((*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml"))):
        text = read_utf8(path, MAX_WORKFLOW_BYTES, f"workflow {path.name}")
        total += len(text.encode("utf-8"))
        if total > 4 * MAX_WORKFLOW_BYTES:
            raise AnalysisError("workflow configuration exceeds aggregate size bound")
        if re.search(r"\bsecrets\s*:\s*inherit\b", text, re.IGNORECASE):
            raise AnalysisError(f"workflow secret reference in {path.name} must be static and inventoried")
        references = set()
        for expression, code in workflow_expressions(text, path.name):
            if re.search(r"\bsecrets\b", code, re.IGNORECASE) is None:
                continue
            static = STATIC_SECRET.fullmatch(expression)
            if static is None:
                raise AnalysisError(f"workflow secret reference in {path.name} must be static and inventoried")
            references.add(static.group(1))
        unknown = references - inventory_names
        if unknown:
            raise AnalysisError(f"workflow secret reference is absent from inventory: {', '.join(sorted(unknown))}")
        references = list(re.finditer(r"(?<![\w.-])(qodana-dotnet\.yml|qodana\.yml)(?![\w.-])", text))
        categories = {"qodana.yml": "qodana-jvm", "qodana-dotnet.yml": "qodana-rider"}
        for index, reference in enumerate(references):
            end = references[index + 1].start() if index + 1 < len(references) else len(text)
            segment = text[reference.end():end]
            uploads = list(re.finditer(
                r"(?m)^(?P<indent>[ \t]*)-[ \t]+uses:[ \t]+github/codeql-action/upload-sarif@[0-9a-f]{40}[ \t]*$",
                segment,
            ))
            if len(uploads) != 1:
                raise AnalysisError("Qodana JVM and Rider uploads require distinct Qodana SARIF categories")
            upload = uploads[0]
            tail = segment[upload.end():]
            next_step = re.search(rf"(?m)^{re.escape(upload.group('indent'))}-[ \t]+", tail)
            upload_block = tail[:next_step.start()] if next_step else tail
            lines = []
            for line in upload_block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "\t" in line:
                    continue
                indentation = len(line) - len(line.lstrip(" "))
                if indentation > len(upload.group("indent")):
                    lines.append((indentation, stripped))
            direct_indent = min((indentation for indentation, _ in lines), default=-1)
            with_indexes = [
                index for index, (indentation, value) in enumerate(lines)
                if indentation == direct_indent and value == "with:"
            ]
            if len(with_indexes) != 1:
                raise AnalysisError("Qodana JVM and Rider uploads require distinct Qodana SARIF categories")
            with_values = []
            for indentation, value in lines[with_indexes[0] + 1:]:
                if indentation <= direct_indent:
                    break
                with_values.append((indentation, value))
            child_indent = min((indentation for indentation, _ in with_values), default=-1)
            found_categories = [
                value.split(":", 1)[1].strip()
                for indentation, value in with_values
                if indentation == child_indent and value.startswith("category:")
            ]
            if found_categories != [categories[reference.group(1)]]:
                raise AnalysisError("Qodana JVM and Rider uploads require distinct Qodana SARIF categories")


def xml_root(path: Path, label: str):
    text = read_utf8(path, MAX_CONFIG_BYTES, label)
    if FORBIDDEN_XML.search(text):
        raise AnalysisError(f"{label} forbids DTD/entity declarations")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise AnalysisError(f"{label} is invalid XML") from error
    if any(not isinstance(node.tag, str) or "}" in node.tag for node in root.iter()):
        raise AnalysisError(f"{label} forbids XML namespaces")
    return root


def validate_rider_contract(root: Path) -> None:
    props = xml_root(root / "src/dotnet/Directory.Build.props", "Rider build properties")
    if props.findtext(".//RestoreLockedMode") != "true":
        raise AnalysisError("Rider build must enforce NuGet locked mode")
    project = xml_root(root / "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj", "Rider project")
    generated = [node.get("Include", "").replace("\\", "/") for node in project.findall(".//Compile")]
    if not any(value.endswith("build/generated/rd/csharp/**/*.cs") for value in generated):
        raise AnalysisError("Rider project must compile the generated C# model")
    tests = xml_root(root / "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj", "Rider test project")
    references = [node.get("Include", "").replace("\\", "/") for node in tests.findall(".//ProjectReference")]
    if not any(value.endswith("PerfSentinel.Rider/PerfSentinel.Rider.csproj") for value in references):
        raise AnalysisError("Rider test project must exercise the production project")
    settings = xml_root(root / "src/dotnet/coverage.runsettings", "Rider coverage settings")
    collector = settings.find('.//DataCollector[@friendlyName="XPlat Code Coverage"]')
    if collector is None or collector.findtext(".//Format") != "cobertura":
        raise AnalysisError("Rider coverage settings must emit Cobertura")
    if collector.findtext(".//ExcludeByFile") != "**/build/generated/rd/csharp/**/*.cs":
        raise AnalysisError("Rider coverage may exclude only generated C#")
    if collector.findtext(".//DeterministicReport") != "true":
        raise AnalysisError("Rider coverage report must be deterministic")


def validate_supply_bindings(inventory, jvm_linter: str) -> None:
    dependencies = inventory.get("dependencies") if type(inventory) is dict else None
    if type(dependencies) is not list:
        raise AnalysisError("supply-chain dependencies must be an array")
    by_name = {}
    for dependency in dependencies:
        if type(dependency) is dict and type(dependency.get("name")) is str:
            if dependency["name"] in by_name:
                raise AnalysisError(f"duplicate supply-chain dependency {dependency['name']}")
            by_name[dependency["name"]] = dependency
    match_jvm = IMAGE.fullmatch(jvm_linter)
    expected = {
        "Qodana JVM Community image": {
            "kind": "container", "version": match_jvm.group(3), "release": match_jvm.group(2),
            "source": "https://hub.docker.com/r/jetbrains/qodana-jvm-community", "declaration": "qodana.yml#linter",
        },
        "Qodana .NET image": {
            "kind": "container", "version": DOTNET_DIGEST, "release": "2026.2",
            "source": "https://hub.docker.com/r/jetbrains/qodana-dotnet",
        },
    }
    for name, contract in expected.items():
        dependency = by_name.get(name)
        if dependency is None or any(dependency.get(key) != value for key, value in contract.items()):
            raise AnalysisError(f"{name} inventory binding is missing or divergent")


def check(root: Path) -> None:
    jvm_qodana, jvm_text = parse_yaml(root / "qodana.yml", "JVM Qodana config")
    dotnet_qodana, _ = parse_yaml(root / "qodana-dotnet.yml", "Qodana .NET config")
    validate_jvm_qodana(jvm_qodana, jvm_text)
    validate_dotnet_qodana(dotnet_qodana)
    secret_inventory = read_json(root / "config/secret-inventory.json", MAX_CONFIG_BYTES, "secret inventory")
    inventory_names = validate_secret_inventory(secret_inventory)
    validate_workflow_secrets(root, inventory_names)
    validate_rider_contract(root)
    supply = read_json(root / "config/supply-chain.json", MAX_INVENTORY_BYTES, "supply-chain inventory")
    validate_supply_bindings(supply, jvm_qodana["linter"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except AnalysisError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("analysis configuration: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
