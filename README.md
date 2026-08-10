# Perf Sentinel for JetBrains IDEs

## Overview

Perf Sentinel displays performance findings from a running `perf-sentinel` daemon directly in JetBrains IDEs. It provides a read-only findings tool window, project-local daemon settings, generic `filepath:lineno` navigation and highlighting, and optional semantic symbol resolution for Java, Kotlin, Python, PHP, Rust, Ruby, JavaScript, TypeScript, Node.js, Go, and C# in Rider.

For SQL findings with positive Java provenance (a `.java` path or a namespace resolving to a Java class), navigation can use the primary SQL table to find a unique project entity with an explicit JPA `@Table` annotation. Schema-qualified SQL additionally requires an explicit matching JPA schema. If that entity belongs to a dependency, the plugin can instead navigate to a unique project Spring Data repository bound to it.

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

Run the complete local verification:

```text
./gradlew check buildPlugin verifyPluginStructure verifyPluginProjectConfiguration verifyPlugin
./gradlew qodanaScan
```

For the Rider smoke test, serve the tracked C# finding and launch the Rider development instance:

```text
python3 tools/rider-fixture-daemon.py
./gradlew runRider
```

Open `src/test/resources/rider-smoke` in that instance. The default fixture targets `Program.cs:18`. Pass `src/test/resources/rider-smoke/semantic-symbols.json` to verify semantic C# navigation for methods, constructors, property accessors, and local functions. Overloads without an argument signature remain visible but intentionally unresolved.

The installable ZIP is generated in `build/distributions/` by `buildPlugin`.

## Current limits

- Findings are read-only. Acknowledgements are displayed but cannot be edited.
- Rider resolves C# `namespace:function` locations through ReSharper when direct `filepath:lineno` data is unavailable. Metadata symbols and overloads without an argument signature are intentionally unresolved.
- Java SQL navigation does not infer implicit JPA table names or guess targets for ambiguous SQL, ambiguous entities, other ORMs, or repositories with incomplete generic types.
- Split Mode, signing, and Marketplace publishing are deferred.

## License

Perf Sentinel for JetBrains IDEs is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).
