# Dependency update policy

Renovate owns Gradle dependencies and plugins, the Gradle wrapper, JetBrains IDE and SDK versions,
RDGen, Rider NuGet packages, lock files, and Gradle verification metadata. Dependabot owns GitHub
Actions and GitHub-native security alerts only. The two services must never manage the same file or
ecosystem. Renovate also owns the JDK build recorded in `.java-version`.

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
CodeQL, Qodana, and reproducibility checks must pass before a maintainer merges manually.

Renovate's custom JetBrains manager reads the official JetBrains product release service for every
IDE version embedded in the Gradle build. Its NuGet manager covers SDK-style project files and locks;
its Gradle managers cover `settings.gradle.kts`, `gradle/libs.versions.toml`, RDGen, plugins,
`gradle/wrapper/gradle-wrapper.properties`, Gradle locks, and verification metadata. Its NuGet
manager updates SDK-style project files and `packages.lock.json`. Dependabot is deliberately limited
to `.github/workflows` action references.

## The JDK pin

Every `actions/setup-java` step reads `java-version-file: .java-version`, so one file holds the
build the whole matrix compiles with. The pin is not cosmetic: the IntelliJ Platform Gradle Plugin
stamps the resolved JVM into `Build-JVM` in each jar manifest, so a floating `java-version: "21"`
makes two builds of the same commit differ and the Windows reproducibility comparison fail.

The pinned value is an Adoptium semver string, `21.0.12+8.0.LTS`, not the release name
`21.0.12+8`: for Temurin, `setup-java` resolves against the same namespace Renovate's `java-version`
datasource reads, so the two agree without any transform. Adoptium folds an interim rebuild into the
build metadata, publishing `21.0.12.1+1` as `21.0.12+101.0.LTS`, which is why the manager declares
`loose` versioning rather than `semver`. Semver ordering ignores build metadata and would call those
two releases equal, exactly the drift that broke the reproducibility comparison in the first place.
A package rule holds the JDK on the Java 21 line the IntelliJ Platform targets.

## Bringing the inventory back in step

Neither bot writes `config/supply-chain.json`, so a pull request that bumps a manifest fails
`check-supply-chain.py` until the matching entry is rewritten. `make sync-supply-chain` performs
that rewrite: it resolves every declaration through `check-supply-chain.py` itself, so the writer
and the gate cannot disagree, and it follows the commit SHA the workflows pin for each action.
`make sync-supply-chain ONLINE=1` also refreshes the release dates, tags, source URLs and Gradle
checksums that no file in the working tree can prove, which is what the `--online` gate compares.

A Gradle bump reaches further than the wrapper: the hosted `gradle-version` inputs in the
workflows and the `gradle-<version>-src.zip` checksum in `gradle/verification-metadata.xml`, which
Qodana downloads, both move with it. `sync-supply-chain` owns the inventory only, so those two stay
manual, and so do the pins mirrored in `scripts/tests`, which exist precisely so a pin cannot move
without a second, conscious edit. The command lists them on every run that changes something.
