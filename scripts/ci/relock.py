#!/usr/bin/env python3
"""Relock an update whose lock or verification checksums Renovate cannot move.

Gradle's --write-verification-metadata resolves every configuration of the build whatever task
runs, which for this plugin means downloading and unpacking every IDE it declares: close to 50 GB
for a single run, more than a hosted runner holds. So nothing here writes metadata through Gradle.
Each group of configurations (a project's own, then each IDE task's, named _<task> in the locks)
resolves in its own run under lenient verification, then the plugin verifier's installers, one
target at a time through a dry run of verifyPlugin. After each run the artifacts Gradle reports
without a checksum are hashed from the cache, and the cache is emptied before the next.

An artifact that already has a checksum is never rewritten: a mismatch stays CI's to fail on.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Lenient verification lists a single failure inline and several as a bulleted list.
FAILED = re.compile(
    r"(?:^\s+- |One artifact failed verification: )(\S+) \(([^():\s]+):([^():\s]+):([^()\s]+)\) from repository ",
    re.M,
)
RESOLVED = re.compile(r"^relock-artifact ([^:\s]+):([^:\s]+):(\S+) (.+)$", re.M)
COMPONENT = re.compile(r'      <component group="[^"]+" name="[^"]+" version="[^"]+">\n.*?      </component>\n', re.S)
COMPONENT_KEY = re.compile(r'      <component group="([^"]+)" name="([^"]+)" version="([^"]+)">')
ARTIFACT = re.compile(r'         <artifact name="([^"]+)">\n.*?         </artifact>\n', re.S)

INIT_SCRIPT = """\
allprojects {
    tasks.register("resolveRelockGroup") {
        def suffix = providers.gradleProperty("relockGroup").getOrElse("")
        def configurations = project.configurations
        def log = logger
        doLast {
            configurations.findAll { c ->
                c.canBeResolved && (suffix ? c.name.endsWith("_" + suffix) : !c.name.contains("_"))
            }.each { c ->
                def view = c.incoming.artifactView { lenient(true) }.artifacts
                view.artifacts.each { a ->
                    def id = a.id.componentIdentifier
                    if (id instanceof org.gradle.api.artifacts.component.ModuleComponentIdentifier) {
                        println "relock-artifact ${id.group}:${id.module}:${id.version} ${a.file.absolutePath}"
                    }
                }
                view.failures.each { log.warn("relock: ${c.name}: ${it.message}") }
            }
        }
    }
}
"""


def missing_artifacts(output: str) -> list[tuple[str, str, str, str]]:
    """(group, name, version, file) for every artifact Gradle could not verify."""
    return [(group, name, version, file) for file, group, name, version in FAILED.findall(output)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate(home: Path, output: str, group: str, name: str, version: str, file: str) -> Path | None:
    """The downloaded copy, else the local one a platform repository resolved it from."""
    cached = sorted((home / "caches" / "modules-2" / "files-2.1" / group / name / version).glob(f"*/{file}"))
    if cached:
        return cached[0]
    for found_group, found_name, found_version, path in RESOLVED.findall(output):
        if (found_group, found_name, found_version) == (group, name, version) and Path(path).name == file:
            return Path(path)
    return None


def collect(home: Path, output: str, checksums: dict) -> None:
    for group, name, version, file in missing_artifacts(output):
        path = locate(home, output, group, name, version, file)
        if path is None or not path.is_file():
            print(f"relock: cannot find {file} ({group}:{name}:{version}), left for CI to report", flush=True)
            continue
        checksums.setdefault((group, name, version), {})[file] = sha256(path)


def version_key(version: str):
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[.\-]", version)]


def insert(metadata: Path, checksums: dict) -> int:
    """Add the new checksums in place, keeping every existing entry and its order."""
    text = metadata.read_text(encoding="utf-8")
    head, rest = text.split("   <components>\n", 1)
    body, tail = rest.split("   </components>\n", 1)
    blocks = COMPONENT.findall(body)
    if "".join(blocks) != body:
        raise SystemExit("relock: verification metadata is not in the expected layout")
    def key(block: str) -> tuple[str, str, str]:
        return COMPONENT_KEY.match(block).groups()

    def order(gnv: tuple[str, str, str]):
        return gnv[0], gnv[1], version_key(gnv[2])

    def artifact(file: str, digest: str) -> str:
        return (f'         <artifact name="{file}">\n'
                f'            <sha256 value="{digest}" origin="Generated by Gradle"/>\n'
                f"         </artifact>\n")

    added = 0
    index = {key(block): position for position, block in enumerate(blocks)}
    for gnv, files in checksums.items():
        if gnv in index:
            block = blocks[index[gnv]]
            entries = {match.group(1): match.group(0) for match in ARTIFACT.finditer(block)}
            fresh = {file: artifact(file, digest) for file, digest in files.items() if file not in entries}
            if fresh:
                entries.update(fresh)
                added += len(fresh)
                opening = block.split("\n", 1)[0] + "\n"
                blocks[index[gnv]] = opening + "".join(entries[file] for file in sorted(entries)) + "      </component>\n"
    for gnv in sorted((gnv for gnv in checksums if gnv not in index), key=order):
        files = checksums[gnv]
        position = next((i for i, block in enumerate(blocks) if order(key(block)) > order(gnv)), len(blocks))
        group, name, version = gnv
        blocks.insert(position, f'      <component group="{group}" name="{name}" version="{version}">\n'
                                + "".join(artifact(file, files[file]) for file in sorted(files))
                                + "      </component>\n")
        added += len(files)
    metadata.write_text(head + "   <components>\n" + "".join(blocks) + "   </components>\n" + tail, encoding="utf-8")
    return added


def groups(root: Path) -> list[tuple[str, str]]:
    """(project path, configuration suffix) for each project's own group and each IDE task's."""
    # The projects settings.gradle.kts includes, not every directory holding a lock: a checkout of
    # this tooling next to the build carries one too.
    settings = (root / "settings.gradle.kts").read_text(encoding="utf-8")
    included = re.findall(r'"(:[^"]+)"', " ".join(re.findall(r"include\(([^)]*)\)", settings)))
    result = []
    for project in ["", *included]:
        lock = root / project.lstrip(":").replace(":", "/") / "gradle.lockfile"
        if not lock.is_file():
            continue
        configurations = {name for line in lock.read_text(encoding="utf-8").splitlines() if "=" in line
                          for name in line.split("=", 1)[1].split(",")}
        result.append((project, ""))
        result += [(project, suffix) for suffix in sorted({name.rsplit("_", 1)[1] for name in configurations if "_" in name})]
    return result


def verifier_targets(root: Path) -> list[str]:
    return re.findall(r'verifyTarget\("([^"]+)"\)', (root / "build.gradle.kts").read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modules", required=True, help="group:name pairs, comma-separated, whose lock may move")
    parser.add_argument("--gradle", default="gradle", help="Gradle command (./gradlew locally)")
    parser.add_argument("--gradle-home", type=Path, required=True, help="Gradle user home, emptied between runs")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root, home = args.root.resolve(), args.gradle_home.resolve()
    init = home.parent / "relock.init.gradle"
    init.write_text(INIT_SCRIPT, encoding="utf-8")
    env = {"GRADLE_USER_HOME": str(home)}

    checksums: dict = {}

    def run(*task_args: str) -> None:
        command = [args.gradle, "--no-daemon", "--console=plain", "--dependency-verification", "lenient", *task_args]
        print("relock:", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=root, env={**os.environ, **env},
                                capture_output=True, text=True, check=False)
        print(result.stdout[-4000:], result.stderr[-4000:], sep="\n", flush=True)
        if result.returncode:
            raise SystemExit(f"relock: Gradle failed with {result.returncode}")
        collect(home, result.stdout + result.stderr, checksums)
        shutil.rmtree(home / "caches" / "modules-2", ignore_errors=True)
        for transforms in home.glob("caches/*/transforms"):
            shutil.rmtree(transforms, ignore_errors=True)
        print(f"relock: {shutil.disk_usage(home).free // 2**30} GiB free", flush=True)

    for project, suffix in groups(root):
        run("--update-locks", args.modules, "--init-script", str(init),
            f"{project}:resolveRelockGroup", f"-PrelockGroup={suffix}")
    for target in verifier_targets(root):
        run("--dry-run", "verifyPlugin", f"-PpluginVerifierTarget={target}")
    (root / "settings-gradle.lockfile").unlink(missing_ok=True)
    added = insert(root / "gradle" / "verification-metadata.xml", checksums)
    print(f"relock: added {added} checksum(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
