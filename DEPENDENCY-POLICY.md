# Dependency update policy

Renovate owns Gradle dependencies and plugins, the Gradle wrapper, JetBrains IDE and SDK versions,
RDGen, Rider NuGet packages, lock files, and Gradle verification metadata. Renovate also owns GitHub
Actions, the JDK build recorded in `.java-version`, and its own image in
`.github/workflows/renovate.yml`. Dependabot keeps GitHub-native security alerts; it opens no version
update. Renovate runs self-hosted from `main` in `.github/workflows/renovate.yml`, never on a pull
request, with a GitHub App token held by the `renovate` environment.

## Update rules

Renovate checks once a day, whenever GitHub starts the workflow's cron; it has no time window of its
own, because GitHub starts this repository's crons hours late. Ordinary minor and patch updates
are grouped by what can finish them: NuGet and tool updates together, since they merge unattended,
and Gradle updates apart, since their locks and verification metadata need a relock Renovate cannot
run. Every JetBrains IDE gets its own pull request: a test IDE needs that relock too, a verifier-only
one does not. Major updates remain separate. The Rider IDE and the Rider and
ReSharper SDKs move together in one pull request, because the IDE and the SDK must match.

GitHub Actions updates carry the same automerge eligibility as ordinary Gradle and NuGet updates.
The tests in `scripts/tests` check that each action is pinned to a full commit SHA, never which one:
that is Renovate's to move and `check-supply-chain.py`'s to hold against the inventory, so a matured
bump merges unattended once `CI / Gate` is green. `step-security/harden-runner` and
`actions/create-github-app-token` are the exception: they run inside the Renovate job itself,
holding or minting the App key, and `CI / Gate` never exercises that workflow, so a dedicated rule
keeps them off automerge entirely.

Only stable releases are eligible, and stable releases are eligible immediately: Renovate opens their
pull request at once, held by a `renovate/stability-days` pending check that Renovate itself posts —
the platform does not provide it — rather than a delayed pull request. Minor,
patch and digest updates merge on their own once seven days old, and only when `CI / Gate` is
green: Renovate merges the pull request itself, on its next scheduled run after both conditions
hold, at most a day later. GitHub's native auto-merge is deliberately unused for this — it merges as
soon as required checks pass and cannot be made to wait on the seven-day stability check, so
`platformAutomerge` stays `false`. The freshness audit is the backstop: its grace in
`scripts/check-supply-chain.py` is 21 days, so it only reports a release Renovate failed to merge,
or one a maintainer left waiting. Major updates are always merged by a maintainer, as are the Rider group, the
Renovate image, and the actions that run inside the Renovate job itself — `renovatebot/github-action`,
`step-security/harden-runner`, `actions/create-github-app-token` — the dependencies excluded from
automerge above. Prereleases such as alpha, beta, RC, EAP, preview, nightly, and snapshot builds are
rejected unless a separate compatibility decision changes the declared product matrix.

JetBrains IDE and SDK updates stay within the declared 2025.3 or 2026.2 compatibility line. The
Rider test collector stays below Coverlet 7 because the project still uses JetBrains' `net472` test
host; newer stable collectors target modern .NET only and cannot run there.

Lock file maintenance is disabled. It regenerates every Gradle lock at once, which drops the bundled
module entries of products it did not resolve and can lock a prerelease IDE.

## Review

Before pushing a branch, Renovate runs `python3 scripts/sync-supply-chain.py --online`, the only
command its global configuration allows. The script rewrites the supply-chain inventory and, for a
JetBrains product only the plugin verifier uses, the lock line and the verification metadata with
the checksums JetBrains publishes, and, for a workflow download whose URL moved, the SHA-256 GitHub
publishes for the release asset. A product a test IDE resolves, such as Rider or RustRover, still
needs a Gradle relock by hand, and so does a Gradle library whose bump changes the lock: relocking
runs the Gradle wrapper, which the global configuration does not allow.

Renovate's custom JetBrains manager reads the official JetBrains product release service for every
IDE version embedded in the Gradle build. Its NuGet manager covers SDK-style project files and
`packages.lock.json`; its Gradle managers cover `settings.gradle.kts`, `gradle/libs.versions.toml`,
RDGen, plugins, and `gradle/wrapper/gradle-wrapper.properties`. Custom managers move the Qodana image
in `qodana.yml` and every audited tool: the scanners and linters the workflows configure or
download, and the tools only `config/supply-chain.json` records, whose entry is their declaration.

The default branch ruleset requires signed commits, and Renovate's branches satisfy it only because
`platformCommit` defaults to `auto` and promotes itself to signed for a GitHub App installation
token; if that default ever regressed, every Renovate branch would be rejected at push, and a dry run
cannot reveal it, since it never pushes.

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

Renovate runs `sync-supply-chain` on its own branches. A manifest bumped by hand fails
`check-supply-chain.py` until the matching entry is rewritten, and `make sync-supply-chain` performs
that rewrite: it resolves every declaration through `check-supply-chain.py` itself, so the writer
and the gate cannot disagree, and it follows the commit SHA the workflows pin for each action.
`make sync-supply-chain ONLINE=1` also refreshes the release dates, tags, source URLs and Gradle
checksums that no file in the working tree can prove, which is what the `--online` gate compares.

A Gradle bump reaches further than the wrapper: the hosted `gradle-version` inputs in the
workflows and the `gradle-<version>-src.zip` checksum in `gradle/verification-metadata.xml`, which
Qodana downloads, both move with it. `sync-supply-chain` writes the inventory and a verifier-only
JetBrains product's lock and verification entries, not these, so those two stay manual. The command
also lists any pin a test in `scripts/tests` still repeats, since Renovate cannot move that copy.

A test IDE bump reaches just as far: the platform arrives without a runtime, so `com.jetbrains:jbr`
moves with it, and `build.gradle.kts` keeps that coordinate out of dependency locking. Pin the new
archives' SHA-256 as `origin="JetBrains Runtime repository"`, confirming each download against the
SHA-512 that `cache-redirector.jetbrains.com/intellij-jbr` publishes beside it; `TEST_IDE_RUNTIMES`
in `scripts/tests/test_verification_commands.py` stays red until they land.
