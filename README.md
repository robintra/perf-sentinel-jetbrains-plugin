# Perf Sentinel for JetBrains IDEs

## Overview

Perf Sentinel displays performance findings from a running `perf-sentinel` daemon directly in JetBrains IDEs. This first increment provides a read-only findings tool window, project-local daemon settings, generic `filepath:lineno` navigation and highlighting, and optional Java symbol resolution.

The plugin calls:

```text
GET http://127.0.0.1:4318/api/findings?service=<project>&limit=1000&include_acked=true
```

Open **Settings | Tools | Perf Sentinel** to configure more daemon endpoints or override the service name. Findings are fetched once when a project opens and whenever **Refresh** is selected. There is no background polling.

## Development

- JDK 21
- IntelliJ Platform 2025.3.5 with `since-build` 253
- Kotlin with the IDE-bundled standard library and coroutines
- Qodana JVM Community

Run the complete local verification:

```text
./gradlew check buildPlugin verifyPluginStructure verifyPluginProjectConfiguration verifyPlugin
./gradlew qodanaScan
```

The installable ZIP is generated in `build/distributions/` by `buildPlugin`.

## Current limits

- Findings are read-only. Acknowledgements are displayed but cannot be edited.
- Java is the first optional PSI adapter. Other languages use direct file and line locations.
- Split Mode, Rider/ReSharper support, signing, and Marketplace publishing are deferred.

## License

Perf Sentinel for JetBrains IDEs is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).
