<p align="center">
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/blob/main/build.gradle.kts"><img src="https://img.shields.io/badge/JetBrains%20IDEs-2025.3%20%7C%202026.2-087CFA?logo=jetbrains&logoColor=white" alt="JetBrains IDEs" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/blob/main/settings.gradle.kts"><img src="https://img.shields.io/badge/Kotlin-2.4.20-7F52FF?logo=kotlin&logoColor=white" alt="Kotlin" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/blob/main/src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj"><img src="https://img.shields.io/badge/.NET%20Framework-4.7.2-512BD4?logo=dotnet&logoColor=white" alt=".NET" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml"><img src="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/security-audit.yml"><img src="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/security-audit.yml/badge.svg" alt="Security Audit" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/codeql.yml"><img src="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/codeql.yml/badge.svg" alt="CodeQL" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/security-audit.yml"><img src="https://img.shields.io/badge/Qodana-JVM%20%7C%20Rider-000000?logo=qodana&logoColor=white" alt="Qodana" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/supply-chain-freshness.yml"><img src="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/supply-chain-freshness.yml/badge.svg" alt="Supply Chain" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/release.yml"><img src="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/release.yml/badge.svg" alt="Release" /></a>
    <a href="https://github.com/robintra/perf-sentinel-jetbrains-plugin/actions/workflows/release.yml"><img src="https://img.shields.io/badge/JetBrains%20ZIP-signature%20configured-lightgrey?logo=jetbrains&logoColor=white" alt="Signed ZIP" /></a>
</p>

# Perf Sentinel for JetBrains IDEs

CI, the daily audit, Supply Chain, CodeQL and Qodana report observed runs. The
rest do not. Supply Chain turns red when a pinned dependency is no longer the
latest upstream stable, which is a task list rather than a broken commit, so it
runs on a schedule and never on push. Qodana has no status badge of its own, so
that one is a plain link to the workflow the two scans run in, the pattern
JetBrains itself recommends. ZIP signing says `configured` because it happens
only on the release path, and the release badge stays empty until that path runs
for the first time. Marketplace version and download badges are omitted until
JetBrains assigns the real numeric listing ID.

## Overview

Perf Sentinel displays performance findings from a running `perf-sentinel` daemon directly in JetBrains IDEs. It provides a read-only findings tool window, project-local daemon settings with an optional IDE-wide credential per endpoint, generic `filepath:lineno` navigation and highlighting, and optional semantic symbol resolution for Java, Kotlin, Python, PHP, Rust, Ruby, JavaScript, TypeScript, Node.js, Go, and C# in Rider.

For SQL findings that are not attributable to another language, navigation can use the primary SQL table to find a unique project entity with an explicit JPA `@Table` annotation. A reported path decides: it must end in `.java`. With no path, a reported namespace must resolve to a Java class in project sources, and a finding reporting neither stays eligible. An entity that declares no JPA schema still matches schema-qualified SQL, since the schema often comes from configuration; two such entities read as ambiguous and refuse navigation. If the matched entity belongs to a dependency, the plugin can instead navigate to a unique project Spring Data repository bound to it.

The plugin calls:

```text
GET http://127.0.0.1:4318/api/findings?service=<project>&limit=1000&include_acked=true
```

Open **Settings | Tools | Perf Sentinel** to configure more daemon endpoints or override the service name. Findings are fetched once when a project opens and whenever **Refresh** is selected. There is no background polling.

### Credentials

A PerfSentinelHub with access control turned on, or a perf-sentinel daemon with `[daemon] require_read_key = true`, refuses an anonymous read. The same settings page holds one optional credential per endpoint: a Hub machine token, or the daemon's `read_api_key`. A newly added endpoint gets its field once the endpoint list is applied, and emptying a field clears its credential. Apply refuses a credential typed for an endpoint the same change removes, and one the plugin would refuse to send.

- A credential belongs to the IDE, not the project. It is kept in the IDE password safe, keyed by the normalized endpoint, and never in the project's workspace file. Every project that lists the same endpoint uses it, and emptying the field in one project clears it for all of them.
- Removing an endpoint keeps its credential, since another project may still use it. To delete it as well, empty its field in the same Apply that removes the endpoint. **Settings | Appearance & Behavior | System Settings | Passwords** only chooses where the IDE stores secrets; it cannot list or delete one.
- It is sent as `Authorization: Bearer <secret>`, which both the Hub and the daemon read, so the plugin does not need to know which one an endpoint is.
- It is sent only to an `https` endpoint or to `localhost`, `127.0.0.1` or `[::1]`, which covers a port forward. For any other plain-HTTP endpoint the plugin sends nothing and reports "A credential is sent only over HTTPS or to this machine."
- With a credential set, a redirect is not followed, so the secret never reaches another address.
- Debug logging for `sun.net.www.protocol.http.HttpURLConnection` (**Help | Diagnostic Tools | Debug Log Settings**) makes the JDK write every request header to `idea.log`. While it is on, the plugin does not send the credential and says why.
- With a credential set, a `401` reads "Credential missing, expired or revoked for <endpoint>". A `403` reads "No grant on this Hub reaches this credential".

### Signing in with perf-sentinel login

A machine token is meant for CI and dashboards. A developer reading a Hub with access control turned on signs in once with the perf-sentinel CLI instead, and the IDE then reads with that developer's own grants:

```shell
perf-sentinel login --hub https://hub.example.com
```

- The plugin reads the token file the CLI writes: `$XDG_CONFIG_HOME/perf-sentinel/hub-token.json` when `XDG_CONFIG_HOME` is an absolute path, else `~/.config/perf-sentinel/hub-token.json`, or `%APPDATA%\perf-sentinel\hub-token.json` on Windows. The variables are read from the environment the IDE loads, which on macOS is the login shell's, so an `XDG_CONFIG_HOME` exported in a shell profile also applies to an IDE started from the Dock.
- The file holds one Hub at a time: a `perf-sentinel login` to another Hub replaces it. An endpoint for a second Hub needs a credential of its own on the settings page, otherwise signing in to one Hub signs the IDE out of the other.
- The token goes only to the endpoint equal to the file's `hub_url`, compared as the settings normalize an endpoint, and only when that endpoint has no credential of its own. A credential set on the settings page always wins: it was chosen for that endpoint on purpose.
- The file is read with the CLI's checks: a symbolic link or anything but a regular file is refused, on Unix a file other users can read or write (`mode & 0o077 != 0`) is refused, and an expired token is refused before any request. A refused or expired token shows on its endpoint with the command that fixes it, and nothing is sent. A link or a file that does not parse names no Hub, so it is only logged, path and reason, and every endpoint is read without it. The token never appears in a log, a notification or a message.
- The file is read again at every refresh, so after a new `perf-sentinel login` the next **Refresh** uses it; there is no need to restart the IDE.
- The same transport rule applies: the token goes only over HTTPS or to this machine, and a redirect is not followed.
- For an endpoint without a credential of its own, a `401` reads "Not signed in to <endpoint>, or the sign-in expired or was revoked: run `perf-sentinel login --hub <endpoint>`, or set a credential for this endpoint."

Without a token file, or with one issued by another Hub, an endpoint without a credential is read exactly as before.

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
