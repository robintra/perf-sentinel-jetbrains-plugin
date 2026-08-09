# Perf Sentinel for JetBrains IDEs

## Overview

Perf Sentinel displays performance findings from a running `perf-sentinel` daemon directly in JetBrains IDEs. This first increment provides a read-only findings tool window, project-local daemon settings, generic `filepath:lineno` navigation and highlighting, optional Java and Kotlin symbol resolution, and verified direct file/line correlation for C# projects in Rider.

The plugin calls:

```text
GET http://127.0.0.1:4318/api/findings?service=<project>&limit=1000&include_acked=true
```

Open **Settings | Tools | Perf Sentinel** to configure more daemon endpoints or override the service name. Findings are fetched once when a project opens and whenever **Refresh** is selected. There is no background polling.

## Development

- JDK 21
- IntelliJ Platform 2025.3.6.1 with `since-build` 253
- Kotlin with the IDE-bundled standard library and coroutines
- Qodana JVM Community

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

Open `src/test/resources/rider-smoke` in that instance. The default fixture targets `Program.cs:12`. Pass `src/test/resources/rider-smoke/symbol-only.json` to the fixture daemon to verify that unresolved C# symbols remain visible without a false diagnostic.

The installable ZIP is generated in `build/distributions/` by `buildPlugin`.

## Current limits

- Findings are read-only. Acknowledgements are displayed but cannot be edited.
- Java and Kotlin have optional PSI adapters. Python, PHP, Rust, Ruby, JavaScript, TypeScript, and Go still use direct file and line locations.
- Rider supports direct C# `filepath:lineno` correlation through the shared IntelliJ frontend. C# `namespace:function` and semantic workspace correlation remain deferred to a ReSharper backend.
- Split Mode, signing, and Marketplace publishing are deferred.

## License

Perf Sentinel for JetBrains IDEs is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).
