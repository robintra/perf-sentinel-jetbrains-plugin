# Contributing

Thank you for improving Perf Sentinel for JetBrains IDEs. Keep each pull
request focused and explain the user-visible behavior it changes.

## Before opening a pull request

Use Java 21, Gradle 9.7, Python 3, and .NET SDK 10.0.302. The dependency locks
and verification metadata are part of the source. Do not update them unless the
pull request intentionally changes dependencies.

On any platform, run the lightweight repository checks:

```shell
python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'
python3 scripts/check-supply-chain.py
python3 scripts/check-analysis-config.py
python3 scripts/check-dependency-automation.py
actionlint
```

Run the JVM and frontend checks with the locked Gradle inputs:

```shell
./gradlew --no-daemon --dependency-verification strict \
  compileKotlin :rider-frontend:compileKotlin test koverXmlReport buildPlugin
python3 scripts/inspect-plugin-zip.py build/distributions/perf-sentinel-0.1.0.zip
```

Rider backend verification requires Windows because the plugin targets the
ReSharper `net472` host. On Windows, run:

```powershell
dotnet restore src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj --locked-mode --configfile src/dotnet/NuGet.Config
dotnet test src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj --configuration Release --no-restore --settings src/dotnet/coverage.runsettings --results-directory build/dotnet/TestResults --collect:"XPlat Code Coverage" --logger trx
```

GitHub runs Plugin Verifier across the supported IDE matrix, Qodana for JVM and
Rider, Sonar for both code surfaces, dependency review, and two independent
`windows-2025` builds. The single required result is `CI / Gate`, which
aggregates those jobs and fails when any applicable check fails. CodeQL runs as
a separate reporting workflow.

## Pull request rules

- Base the change on `main` and use a signed commit.
- Add or update tests before changing behavior.
- Keep generated IDEs, caches, coverage, SARIF, binaries, credentials, and
  local paths out of Git.
- Resolve review conversations before merge.
- Use squash or rebase merge. Force pushes and branch deletion are disabled on
  protected `main`.

Releases are maintainer-only. The signed-tag and protected-environment process
is documented in [RELEASING.md](RELEASING.md).
