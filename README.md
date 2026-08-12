# Perf Sentinel for JetBrains IDEs

[![CI](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml)
[![Sonar JVM](https://sonarcloud.io/api/project_badges/measure?project=robintrassard_perf-sentinel-jetbrains-plugin-jvm&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=robintrassard_perf-sentinel-jetbrains-plugin-jvm)
[![Sonar Rider](https://sonarcloud.io/api/project_badges/measure?project=robintrassard_perf-sentinel-jetbrains-plugin-rider&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=robintrassard_perf-sentinel-jetbrains-plugin-rider)
[![Qodana](https://img.shields.io/badge/Qodana-configured-lightgrey)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml)
[![CodeQL](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/codeql.yml/badge.svg)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/codeql.yml)
[![Daily audit](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/security-audit.yml/badge.svg)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/security-audit.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/robintra/perf-sentinel-jetbrains-plugin/badge)](https://securityscorecards.dev/viewer/?uri=github.com/robintra/perf-sentinel-jetbrains-plugin)
[![Latest release](https://img.shields.io/github/v/release/robintra/perf-sentinel-jetbrains-plugin?display_name=tag&sort=semver)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/releases/latest)
[![JetBrains compatibility](https://img.shields.io/badge/JetBrains-2025.3%20%7C%202026.2-087CFA)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml)
[![Signed ZIP](https://img.shields.io/badge/JetBrains%20ZIP%20signature-configured-lightgrey)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/robintra/perf-sentinel-jetbrains-plugin)](https://github.com/robintra/perf-sentinel-jetbrains-plugin/blob/main/LICENSE)

The badges link to workflows, analysis projects, release destinations, and
committed evidence. They become observed public evidence only after public
activation. Qodana and ZIP signing deliberately say `configured` until then.
Marketplace version and download badges are omitted until JetBrains assigns the
real numeric listing ID.

## Overview

Perf Sentinel displays performance findings from a running `perf-sentinel` daemon directly in JetBrains IDEs. It provides a read-only findings tool window, project-local daemon settings, generic `filepath:lineno` navigation and highlighting, and optional semantic symbol resolution for Java, Kotlin, Python, PHP, Rust, Ruby, JavaScript, TypeScript, Node.js, Go, and C# in Rider.

For SQL findings that are not attributable to another language, navigation can use the primary SQL table to find a unique project entity with an explicit JPA `@Table` annotation. A reported path decides: it must end in `.java`. With no path, a reported namespace must resolve to a Java class in project sources, and a finding reporting neither stays eligible. An entity that declares no JPA schema still matches schema-qualified SQL, since the schema often comes from configuration; two such entities read as ambiguous and refuse navigation. If the matched entity belongs to a dependency, the plugin can instead navigate to a unique project Spring Data repository bound to it.

The plugin calls:

```text
GET http://127.0.0.1:4318/api/findings?service=<project>&limit=1000&include_acked=true
```

Open **Settings | Tools | Perf Sentinel** to configure more daemon endpoints or override the service name. Findings are fetched once when a project opens and whenever **Refresh** is selected. There is no background polling.

Perf Sentinel defines no default keyboard shortcut; open it from the tool window bar or **Find Action** and assign a shortcut only if desired.

## Development

- JDK 21
- IntelliJ Platform 2025.3.6.1 with `since-build` 253
- Kotlin with the IDE-bundled standard library and coroutines
- Qodana JVM Community

Product compatibility is verified against the stable 2025.3 and 2026.2 releases of IntelliJ IDEA, Rider, PyCharm, PhpStorm, RustRover, RubyMine, WebStorm, and GoLand. Language integrations are optional. The generated ZIP contains only Perf Sentinel code and loads without unavailable language plugins.

The stable local entry points are:

```text
make verify-fast
make security
make release-check VERSION=0.1.0
```

The Rider part of `verify-fast` and the release check requires Windows. CI is
the authoritative cross-platform execution environment.

For the Rider smoke test, serve the tracked C# finding and launch the Rider development instance:

```text
python3 tools/rider-fixture-daemon.py
./gradlew runRider
```

Open `src/test/resources/rider-smoke` in that instance. The default fixture targets `Program.cs:18`. Pass `src/test/resources/rider-smoke/semantic-symbols.json` to verify semantic C# navigation for methods, constructors, property accessors, and local functions. Overloads without an argument signature remain visible but intentionally unresolved.

The installable ZIP is generated in `build/distributions/` by `buildPlugin`.

## Releases

Stable releases use `0.MINOR.PATCH` versions. GitHub builds the plugin twice on
Windows, compares the unsigned archives byte for byte, signs the verified ZIP
with the native JetBrains format, and uploads the same author-signed bytes to
JetBrains Marketplace and GitHub. Marketplace adds its own signature. Stable
`0.x.y` releases have no prerelease suffix, but they still indicate pre-1.0
maturity and may change compatibility between minor versions.

Public verification checks the author certificate and every plugin entry.
Marketplace appends a second JetBrains signature, so the full ZIP hashes differ
while the plugin entries remain identical.
See [RELEASING.md](RELEASING.md) for the activation checklist, release steps,
public verification, rotation, and rollback procedures.

## Current limits

- Findings are read-only. Acknowledgements are displayed but cannot be edited.
- Rider resolves C# `namespace:function` locations through ReSharper when direct `filepath:lineno` data is unavailable. Metadata symbols and overloads without an argument signature are intentionally unresolved.
- Java SQL navigation does not infer implicit JPA table names or guess targets for ambiguous SQL, ambiguous entities, other ORMs, or repositories with incomplete generic types.
- Split Mode remains disabled.

## License

Perf Sentinel for JetBrains IDEs is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).
