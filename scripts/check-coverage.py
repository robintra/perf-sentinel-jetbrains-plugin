#!/usr/bin/env python3
"""Enforce independent Kover and Rider line-coverage baselines."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPOSITORY / "config" / "coverage-baseline.json"
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_BASELINE_BYTES = 64 * 1024
MAX_CHANGED_LINES_BYTES = 64 * 1024 * 1024
MAX_LINE_RECORDS = 2_000_000
NEW_CODE_MINIMUM = Decimal(80)
POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]{0,9}$")
NONNEGATIVE_INTEGER = re.compile(r"^(?:0|[1-9][0-9]{0,9})$")
TIMESTAMP_INTEGER = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
DRIVE_PATH = re.compile(r"^[A-Za-z]:/")
XML_DECLARATION = re.compile(
    r'^<\?xml\s+version=["\']1\.0["\']\s+encoding=["\']utf-8["\']\s*\?>',
    re.IGNORECASE,
)
FORBIDDEN_XML = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class CoverageError(ValueError):
    pass


@dataclass(frozen=True)
class CoverageReport:
    lines: dict[tuple[str, int], bool]
    missed: int
    covered: int


def read_utf8(path: Path, maximum: int, label: str) -> str:
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
    except OSError as error:
        raise CoverageError(f"{path}: unable to read {label}") from error
    if not payload or len(payload) > maximum:
        raise CoverageError(f"{path}: {label} size must be between 1 and {maximum} bytes")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CoverageError(f"{path}: {label} must be strict UTF-8 without BOM")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CoverageError(f"{path}: {label} must be strict UTF-8 without BOM") from error


def parse_xml(path: Path, format_name: str) -> ElementTree.Element:
    text = read_utf8(path, MAX_REPORT_BYTES, "coverage report")
    if FORBIDDEN_XML.search(text):
        raise CoverageError(f"{path}: DTD and entity declarations are forbidden")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise CoverageError(f"{path}: unable to parse {format_name} XML") from error
    if XML_DECLARATION.match(text) is None:
        raise CoverageError(f"{path}: coverage report must declare strict UTF-8 without BOM")
    if any(not isinstance(node.tag, str) or "}" in node.tag or node.tag.startswith("{") for node in root.iter()):
        raise CoverageError(f"{path}: XML namespaces are forbidden")
    return root


def parse_integer(value: object, *, positive: bool, label: str) -> int:
    pattern = POSITIVE_INTEGER if positive else NONNEGATIVE_INTEGER
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CoverageError(label)
    return int(value)


def normalize_component(value: object, *, allow_empty: bool = False, allow_pathmap: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 4_096 or "\x00" in value:
        raise CoverageError("source path is not stable")
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if any(ord(character) < 32 for character in value):
        raise CoverageError("source path is not stable")
    if allow_pathmap and re.match(r"^/+_(?:/+|$)", value):
        value = re.sub(r"^/+_(?:/+|$)", "", value, count=1)
    if not value and allow_empty:
        return ""
    if not value or posixpath.isabs(value) or DRIVE_PATH.match(value):
        raise CoverageError("source path is not stable")
    if ".." in value.split("/"):
        raise CoverageError("source path is not stable")
    normalized = posixpath.normpath(value)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or posixpath.isabs(normalized)
        or DRIVE_PATH.match(normalized)
    ):
        raise CoverageError("source path is not stable")
    return normalized


def line_key(path: str, number: int) -> tuple[str, int]:
    return (path.casefold(), number)


def merge_lines(target: dict[tuple[str, int], bool], source: dict[tuple[str, int], bool]) -> None:
    duplicate = set(target).intersection(source)
    if duplicate:
        raise CoverageError("duplicate executable line after path normalization")
    target.update(source)
    if len(target) > MAX_LINE_RECORDS:
        raise CoverageError(f"report exceeds {MAX_LINE_RECORDS} executable line records")


def line_counter(element: ElementTree.Element, format_name: str) -> tuple[int, int]:
    counters = [child for child in element if child.tag == "counter"]
    types: set[str] = set()
    line: tuple[int, int] | None = None
    for counter in counters:
        if set(counter.attrib) != {"type", "missed", "covered"} or list(counter):
            raise CoverageError(f"{format_name} counter is malformed")
        counter_type = counter.get("type")
        if not counter_type or counter_type in types:
            raise CoverageError(f"duplicate {format_name} counter")
        types.add(counter_type)
        missed = parse_integer(counter.get("missed"), positive=False, label=f"{format_name} counter is malformed")
        covered = parse_integer(counter.get("covered"), positive=False, label=f"{format_name} counter is malformed")
        if counter_type == "LINE":
            line = missed, covered
    if line is None:
        raise CoverageError(f"{format_name} LINE counter is missing")
    return line


def check_source_line_counter(element: ElementTree.Element, lines: dict[tuple[str, int], bool]) -> tuple[int, int]:
    missed, covered = line_counter(element, "Kover")
    if missed < len(lines) - sum(lines.values()) or covered < sum(lines.values()):
        raise CoverageError("Kover LINE counter differs from executable line records")
    return missed, covered


def parse_kover_method(method: ElementTree.Element) -> tuple[int, int]:
    if set(method.attrib) != {"name", "desc"} or any(child.tag != "counter" for child in method):
        raise CoverageError("Kover method structure is invalid")
    if not method.get("name") or not method.get("desc"):
        raise CoverageError("Kover method structure is invalid")
    return line_counter(method, "Kover")


def parse_kover_class(class_element: ElementTree.Element) -> tuple[str, int, int]:
    if set(class_element.attrib) != {"name", "sourcefilename"} or any(
        child.tag not in {"method", "counter"} for child in class_element
    ):
        raise CoverageError("Kover class structure is invalid")
    if not class_element.get("name"):
        raise CoverageError("Kover class structure is invalid")
    source = normalize_component(class_element.get("sourcefilename"))
    method_counts: list[tuple[int, int]] = []
    method_keys: set[tuple[str, str]] = set()
    for method in (child for child in class_element if child.tag == "method"):
        key = tuple(unicodedata.normalize("NFC", method.get(field, "")) for field in ("name", "desc"))
        if key in method_keys:
            raise CoverageError("duplicate Kover method")
        method_keys.add(key)
        method_counts.append(parse_kover_method(method))
    missed, covered = line_counter(class_element, "Kover")
    if (missed, covered) != (
        sum(count[0] for count in method_counts),
        sum(count[1] for count in method_counts),
    ):
        raise CoverageError("Kover class LINE counter differs from method counters")
    return source, missed, covered


def parse_kover_sourcefile(package: str, source: ElementTree.Element) -> tuple[str, CoverageReport]:
    if set(source.attrib) != {"name"} or any(child.tag not in {"line", "counter"} for child in source):
        raise CoverageError("Kover sourcefile structure is invalid")
    filename = normalize_component(source.get("name"))
    path = posixpath.join(package, filename) if package else filename
    lines: dict[tuple[str, int], bool] = {}
    for line in (child for child in source if child.tag == "line"):
        if set(line.attrib) != {"nr", "mi", "ci", "mb", "cb"} or list(line):
            raise CoverageError("Kover line record is malformed")
        number = parse_integer(line.get("nr"), positive=True, label="Kover line number is invalid")
        missed = parse_integer(line.get("mi"), positive=False, label="Kover instruction count is invalid")
        covered = parse_integer(line.get("ci"), positive=False, label="Kover instruction count is invalid")
        parse_integer(line.get("mb"), positive=False, label="Kover branch count is invalid")
        parse_integer(line.get("cb"), positive=False, label="Kover branch count is invalid")
        if missed + covered == 0:
            continue
        key = line_key(path, number)
        if key in lines:
            raise CoverageError("duplicate executable line after path normalization")
        lines[key] = covered > 0
    missed, covered = check_source_line_counter(source, lines)
    return filename, CoverageReport(lines, missed, covered)


def parse_kover_container(container: ElementTree.Element) -> CoverageReport:
    allowed = {"package", "group", "counter"}
    if container.tag == "package":
        allowed |= {"class", "sourcefile"}
    elif container.tag == "report":
        allowed.add("sessioninfo")
    if any(child.tag not in allowed for child in container):
        raise CoverageError("Kover report structure is invalid")
    package = ""
    if container.tag == "package":
        if set(container.attrib) != {"name"}:
            raise CoverageError("Kover package structure is invalid")
        package = normalize_component(container.get("name"), allow_empty=True)
    elif container.tag == "group":
        if set(container.attrib) != {"name"} or not container.get("name"):
            raise CoverageError("Kover group structure is invalid")
    for session in (child for child in container if child.tag == "sessioninfo"):
        if set(session.attrib) != {"id", "start", "dump"} or list(session) or not session.get("id"):
            raise CoverageError("Kover session structure is invalid")
        if any(TIMESTAMP_INTEGER.fullmatch(session.get(field, "")) is None for field in ("start", "dump")):
            raise CoverageError("Kover session structure is invalid")
    class_counts: dict[str, tuple[int, int]] = {}
    if container.tag == "package":
        class_names: set[str] = set()
        for class_element in (child for child in container if child.tag == "class"):
            class_name = unicodedata.normalize("NFC", class_element.get("name", "")).casefold()
            if class_name in class_names:
                raise CoverageError("duplicate Kover class")
            class_names.add(class_name)
            source, class_missed, class_covered = parse_kover_class(class_element)
            key = source.casefold()
            missed, covered = class_counts.get(key, (0, 0))
            class_counts[key] = missed + class_missed, covered + class_covered
    lines: dict[tuple[str, int], bool] = {}
    missed = 0
    covered = 0
    source_counts: dict[str, tuple[int, int]] = {}
    for child in container:
        if child.tag in {"package", "group"}:
            report = parse_kover_container(child)
            merge_lines(lines, report.lines)
            missed += report.missed
            covered += report.covered
        elif child.tag == "sourcefile":
            source, report = parse_kover_sourcefile(package, child)
            key = source.casefold()
            if key in source_counts:
                raise CoverageError("duplicate Kover sourcefile after path normalization")
            source_counts[key] = report.missed, report.covered
            merge_lines(lines, report.lines)
            missed += report.missed
            covered += report.covered
    if container.tag == "package" and source_counts != class_counts:
        raise CoverageError("Kover sourcefile LINE counter differs from class counters")
    if line_counter(container, "Kover") != (missed, covered):
        raise CoverageError("Kover LINE counter differs from child counters")
    return CoverageReport(lines, missed, covered)


def read_kover(path: Path) -> CoverageReport:
    root = parse_xml(path, "Kover")
    if root.tag != "report":
        raise CoverageError(f"{path}: Kover root must be report")
    if set(root.attrib) != {"name"}:
        raise CoverageError("Kover report attributes are invalid")
    lines = parse_kover_container(root)
    if not lines.lines:
        raise CoverageError(f"{path}: report contains no executable lines")
    return lines


def direct_children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if child.tag == name]


def validate_cobertura_line(line: ElementTree.Element) -> tuple[int, int]:
    branch = line.get("branch")
    expected = {"number", "hits", "branch"}
    if branch == "True":
        expected.add("condition-coverage")
        conditions = direct_children(line, "conditions")
        if [child.tag for child in line] != ["conditions"] or set(conditions[0].attrib) or any(
            child.tag != "condition" for child in conditions[0]
        ):
            raise CoverageError("Cobertura structure is invalid")
        for condition in conditions[0]:
            if set(condition.attrib) != {"number", "type", "coverage"} or list(condition):
                raise CoverageError("Cobertura structure is invalid")
    elif branch == "False":
        if list(line):
            raise CoverageError("Cobertura structure is invalid")
    else:
        raise CoverageError("Cobertura structure is invalid")
    if set(line.attrib) != expected:
        raise CoverageError("Cobertura structure is invalid")
    return (
        parse_integer(line.get("number"), positive=True, label="Cobertura line number is invalid"),
        parse_integer(line.get("hits"), positive=False, label="Cobertura line hits are invalid"),
    )


def validate_cobertura_methods(methods: ElementTree.Element) -> None:
    if set(methods.attrib) or any(child.tag != "method" for child in methods):
        raise CoverageError("Cobertura structure is invalid")
    for method in methods:
        if set(method.attrib) != {"name", "signature", "line-rate", "branch-rate", "complexity"}:
            raise CoverageError("Cobertura structure is invalid")
        method_lines = direct_children(method, "lines")
        if len(method_lines) != 1 or any(child.tag != "lines" for child in method):
            raise CoverageError("Cobertura structure is invalid")
        if set(method_lines[0].attrib):
            raise CoverageError("Cobertura structure is invalid")
        for line in method_lines[0]:
            if line.tag != "line":
                raise CoverageError("Cobertura structure is invalid")
            validate_cobertura_line(line)


def read_cobertura(path: Path) -> CoverageReport:
    root = parse_xml(path, "Cobertura")
    if root.tag != "coverage":
        raise CoverageError(f"{path}: Cobertura root must be coverage")
    if set(root.attrib) != {
        "line-rate",
        "branch-rate",
        "version",
        "timestamp",
        "lines-covered",
        "lines-valid",
        "branches-covered",
        "branches-valid",
    } or [child.tag for child in root] != ["sources", "packages"]:
        raise CoverageError("Cobertura root structure is invalid")
    packages = direct_children(root, "packages")
    sources = direct_children(root, "sources")
    if len(packages) != 1 or len(sources) != 1 or set(packages[0].attrib) or set(sources[0].attrib):
        raise CoverageError("Cobertura packages structure is required")
    if any(source.tag != "source" or set(source.attrib) or list(source) for source in sources[0]):
        raise CoverageError("Cobertura structure is invalid")
    lines: dict[tuple[str, int], bool] = {}
    for package in packages[0]:
        if package.tag != "package" or set(package.attrib) != {
            "name",
            "line-rate",
            "branch-rate",
            "complexity",
        }:
            raise CoverageError("Cobertura package structure is invalid")
        classes = direct_children(package, "classes")
        if len(classes) != 1 or any(child.tag != "classes" for child in package) or set(classes[0].attrib):
            raise CoverageError("Cobertura classes structure is required")
        for class_element in classes[0]:
            if class_element.tag != "class":
                raise CoverageError("Cobertura class structure is invalid")
            if set(class_element.attrib) != {
                "name",
                "filename",
                "line-rate",
                "branch-rate",
                "complexity",
            }:
                raise CoverageError("Cobertura structure is invalid")
            class_lines = direct_children(class_element, "lines")
            methods = direct_children(class_element, "methods")
            if (
                len(class_lines) != 1
                or len(methods) != 1
                or [child.tag for child in class_element] != ["methods", "lines"]
                or set(class_lines[0].attrib)
            ):
                raise CoverageError("Cobertura class lines structure is required")
            validate_cobertura_methods(methods[0])
            filename = normalize_component(class_element.get("filename"), allow_pathmap=True)
            for line in class_lines[0]:
                if line.tag != "line":
                    raise CoverageError("Cobertura line structure is invalid")
                number, hits = validate_cobertura_line(line)
                key = line_key(filename, number)
                if key in lines:
                    raise CoverageError("duplicate executable line after path normalization")
                lines[key] = hits > 0
                if len(lines) > MAX_LINE_RECORDS:
                    raise CoverageError(f"report exceeds {MAX_LINE_RECORDS} executable line records")
    if not lines:
        raise CoverageError(f"{path}: report contains no executable lines")
    valid = parse_integer(root.get("lines-valid"), positive=False, label="Cobertura root line counts are invalid")
    covered = parse_integer(root.get("lines-covered"), positive=False, label="Cobertura root line counts are invalid")
    if valid != len(lines) or covered != sum(lines.values()):
        raise CoverageError("Cobertura root line counts differ from executable line records")
    return CoverageReport(lines, valid - covered, covered)


def percentage(lines: dict[tuple[str, int], bool]) -> Decimal:
    return Decimal(sum(lines.values())) * 100 / Decimal(len(lines))


def total_percentage(report: CoverageReport) -> Decimal:
    return Decimal(report.covered) * 100 / Decimal(report.missed + report.covered)


def display(value: Decimal) -> str:
    return f"{value:.2f}%"


def read_changed_lines(path: Path, surface: str) -> set[tuple[str, int]]:
    text = read_utf8(path, MAX_CHANGED_LINES_BYTES, "changed-lines manifest")
    if not text.endswith("\n"):
        raise CoverageError(f"{path}: changed-lines manifest must end with a newline")
    records = text.splitlines()
    if not records or records[0] != f"coverage-changed-lines-v1\t{surface}":
        raise CoverageError(f"{path}: changed-lines manifest header is invalid")
    result: set[tuple[str, int]] = set()
    for record in records[1:]:
        fields = record.split("\t")
        if len(fields) != 2:
            raise CoverageError(f"{path}: changed-lines record is malformed")
        repository_path = normalize_component(fields[0])
        if surface == "jvm":
            prefixes = ("src/main/kotlin/", "rider-frontend/src/main/kotlin/")
            prefix = next((candidate for candidate in prefixes if repository_path.startswith(candidate)), None)
            if prefix is None or not repository_path.endswith(".kt"):
                raise CoverageError(f"{path}: changed path is outside the {surface} coverage surface")
            normalized = repository_path[len(prefix) :]
        else:
            if not repository_path.startswith("src/dotnet/") or not repository_path.endswith(".cs"):
                raise CoverageError(f"{path}: changed path is outside the {surface} coverage surface")
            normalized = repository_path
        number = parse_integer(fields[1], positive=True, label=f"{path}: changed line number is invalid")
        key = line_key(normalized, number)
        if key in result:
            raise CoverageError(f"{path}: duplicate changed line after path normalization")
        result.add(key)
        if len(result) > MAX_LINE_RECORDS:
            raise CoverageError(f"changed-lines manifest exceeds {MAX_LINE_RECORDS} records")
    return result


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_baseline(path: Path) -> dict[str, object]:
    def reject_constant(value: str):
        raise ValueError(f"non-finite number {value}")

    try:
        data = json.loads(
            read_utf8(path, MAX_BASELINE_BYTES, "coverage baseline"),
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
        if not isinstance(data, dict) or set(data) != {"schema_version", "new_code_minimum", "surfaces"}:
            raise ValueError("baseline root is not closed")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("schema_version must be integer 1")
        if type(data["new_code_minimum"]) is not int or data["new_code_minimum"] != 80:
            raise ValueError("new_code_minimum must be integer 80")
        surfaces = data["surfaces"]
        if not isinstance(surfaces, dict) or set(surfaces) != {"jvm", "rider"}:
            raise ValueError("surface set is not exact")
        for name in ("jvm", "rider"):
            surface = surfaces[name]
            if not isinstance(surface, dict):
                raise ValueError(f"{name} baseline must be an object")
            if name == "rider" and surface == {"status": "pending_windows"}:
                continue
            if set(surface) != {"total_line_coverage"}:
                raise ValueError(f"{name} baseline fields are invalid")
            value = surface["total_line_coverage"]
            if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                raise ValueError(f"{name} baseline must be numeric")
            value = Decimal(value)
            if not value.is_finite() or value < 0 or value > 100:
                raise ValueError(f"{name} baseline is outside 0..100")
            surface["total_line_coverage"] = value
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, CoverageError, ValueError) as error:
        raise CoverageError(f"{path}: unable to parse coverage baseline: {error}") from error


def baseline_value(data: dict[str, object], surface: str) -> Decimal:
    value = data["surfaces"][surface]
    if surface == "rider" and value == {"status": "pending_windows"}:
        raise CoverageError("Rider baseline is pending genuine Windows coverage")
    return Decimal(value["total_line_coverage"])


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def write_baseline(path: Path, jvm: Decimal, rider: Decimal | None) -> None:
    rider_text = (
        '{"status": "pending_windows"}'
        if rider is None
        else f'{{"total_line_coverage": {decimal_text(rider)}}}'
    )
    payload = (
        '{\n  "schema_version": 1,\n  "new_code_minimum": 80,\n  "surfaces": {\n'
        f'    "jvm": {{"total_line_coverage": {decimal_text(jvm)}}},\n'
        f'    "rider": {rider_text}\n'
        "  }\n}\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    except OSError as error:
        raise CoverageError(f"{path}: unable to write coverage baseline") from error


def establish(surface: str, report_path: Path, baseline_path: Path) -> None:
    if surface == "rider" and sys.platform != "win32":
        raise CoverageError("Rider baseline establishment requires Windows")
    reader = read_kover if surface == "jvm" else read_cobertura
    total = total_percentage(reader(report_path))
    if surface == "jvm":
        rider = None
        if baseline_path.is_file():
            existing = load_baseline(baseline_path)
            rider_entry = existing["surfaces"]["rider"]
            if "total_line_coverage" in rider_entry:
                rider = Decimal(rider_entry["total_line_coverage"])
        write_baseline(baseline_path, total, rider)
    else:
        if not baseline_path.is_file():
            raise CoverageError("establish the JVM baseline first")
        existing = load_baseline(baseline_path)
        write_baseline(baseline_path, baseline_value(existing, "jvm"), total)
    print(f"established {surface} total line coverage baseline: {display(total)}")


def check(
    surface: str,
    current_path: Path,
    reference_path: Path,
    baseline_path: Path,
    changed_lines_path: Path,
) -> None:
    baseline = load_baseline(baseline_path)
    configured = baseline_value(baseline, surface)
    reader = read_kover if surface == "jvm" else read_cobertura
    reference = reader(reference_path)
    current = reader(current_path)
    reference_total = total_percentage(reference)
    if reference_total != configured:
        raise CoverageError(
            f"baseline report is {display(reference_total)}, configured baseline is {display(configured)}"
        )
    current_total = total_percentage(current)
    if current_total < configured:
        raise CoverageError(
            f"total line coverage regressed: {display(current_total)} < {display(configured)}"
        )
    changed = read_changed_lines(changed_lines_path, surface)
    new_lines = {key: current.lines[key] for key in changed if key in current.lines}
    if not new_lines:
        print(
            f"{surface} total line coverage: {display(current_total)} "
            f"(baseline {display(configured)}); new-code line coverage: not applicable "
            "(no changed executable lines)"
        )
        return
    new_total = percentage(new_lines)
    if new_total < NEW_CODE_MINIMUM:
        raise CoverageError(
            f"new-code line coverage is {display(new_total)}; required {display(NEW_CODE_MINIMUM)}"
        )
    print(
        f"{surface} total line coverage: {display(current_total)} "
        f"(baseline {display(configured)}); new-code line coverage: {display(new_total)}"
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=("jvm", "rider"), required=True)
    parser.add_argument("--current-report", type=Path, required=True)
    parser.add_argument("--baseline-file", type=Path, default=DEFAULT_BASELINE)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--establish-baseline", action="store_true")
    mode.add_argument("--baseline-report", type=Path)
    parser.add_argument(
        "--changed-lines-file",
        type=Path,
        help="trusted SCM-derived changed-line manifest for the selected surface",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.establish_baseline:
            if arguments.changed_lines_file is not None:
                raise CoverageError("changed lines are only valid when checking coverage")
            establish(arguments.surface, arguments.current_report, arguments.baseline_file)
        else:
            if arguments.changed_lines_file is None:
                raise CoverageError("coverage checking requires --changed-lines-file")
            check(
                arguments.surface,
                arguments.current_report,
                arguments.baseline_report,
                arguments.baseline_file,
                arguments.changed_lines_file,
            )
    except CoverageError as error:
        print(f"coverage check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
