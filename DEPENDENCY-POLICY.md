# Dependency update policy

Renovate owns Gradle dependencies and plugins, the Gradle wrapper, JetBrains IDE and SDK versions,
RDGen, Rider NuGet packages, lock files, and Gradle verification metadata. Dependabot owns GitHub
Actions and GitHub-native security alerts only. The two services must never manage the same file or
ecosystem.

## Update rules

Both services check on Monday at 06:00 in `Europe/Paris`. Ordinary minor and patch updates may be
grouped within their owner. Major updates remain separate. Security updates remain isolated from
ordinary groups. A catch-all Renovate rule explicitly disables automatic merging, including any
setting inherited from an organization preset.

Only stable releases are eligible, and stable releases are eligible immediately. The repository
does not impose a three-day or 72-hour waiting period. Prereleases such as alpha, beta, RC, EAP,
preview, nightly, and snapshot builds are rejected unless a separate compatibility decision changes
the declared product matrix.

JetBrains IDE and SDK updates stay within the declared 2025.3 or 2026.2 compatibility line. The
Rider test collector stays below Coverlet 7 because the project still uses JetBrains' `net472` test
host; newer stable collectors target modern .NET only and cannot run there.

## Review

Every dependency pull request must update the supply-chain inventory, lock files, verification
metadata, and immutable action pins together when applicable. The full CI gate, vulnerability audit,
CodeQL, SonarQube, Qodana, and reproducibility checks must pass before a maintainer merges manually.

Renovate's custom JetBrains manager reads the official JetBrains product release service for every
IDE version embedded in the Gradle build. Its NuGet manager covers SDK-style project files and locks;
its Gradle managers cover `settings.gradle.kts`, `gradle/libs.versions.toml`, RDGen, plugins,
`gradle/wrapper/gradle-wrapper.properties`, Gradle locks, and verification metadata. Its NuGet
manager updates SDK-style project files and `packages.lock.json`. Dependabot is deliberately limited
to `.github/workflows` action references.
