<!-- Keep a Changelog guide -> https://keepachangelog.com -->

# Perf Sentinel changelog

## [Unreleased]

### Added

- Optional credential per endpoint, a Hub machine token or a daemon read key, set in **Settings | Tools | Perf Sentinel**. It is kept in the IDE password safe, never in the workspace file, and sent as `Authorization: Bearer` only over HTTPS or to `localhost`, `127.0.0.1` or `[::1]`; elsewhere the plugin sends nothing. With a credential set, a redirect is not followed, and nothing is sent while `sun.net.www.protocol.http.HttpURLConnection` debug logging would write the header to `idea.log`. Apply refuses a credential the plugin would not send. A credential is shared by every project that lists the endpoint. A `401` or `403` now names the credential problem. Needed for a PerfSentinelHub with access control turned on (a release after Hub 0.3.8) and for a perf-sentinel daemon with `[daemon] require_read_key = true` (a release after perf-sentinel 0.26.2). An endpoint without a credential is read as before.

## [0.1.0] - 2026-08-12

### Added

- Read-only findings view for one or more Perf Sentinel daemons.
- Project settings, bilingual English/French UI, source navigation, and editor highlights.
- Optional Java namespace and function resolution.
- Optional Kotlin package, class, object, and top-level function correlation.
- Optional Python module and class method correlation.
- Optional PHP namespaced function and class method correlation.
- Optional Rust module and associated function correlation across the Rust plugin ID migration.
- Optional Ruby module, instance method, and singleton method correlation.
- Optional JavaScript and TypeScript correlation for CommonJS, ESM, classes, and NestJS controllers.
- Optional Go package function and receiver method correlation.
- Java SQL navigation to explicit JPA `@Table` entities, with a safe Spring Data repository fallback for external entities.
- Rider C# file/line and semantic navigation for methods, constructors, property accessors, and local functions.
- Compatibility verification for the stable 2025.3 and 2026.2 releases of all supported JetBrains products.
