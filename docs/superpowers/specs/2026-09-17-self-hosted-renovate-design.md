# Self-hosted Renovate with synchronised pull requests

Status: approved design, not implemented.
Date: 2026-09-17.

## Problem

`Supply Chain Freshness` failed on 17 of its first 34 scheduled runs. Three causes compound:

1. **Renovate never ran.** `.github/renovate.json` has been configured since 2026-08-12, but no
   Renovate pull request, Dependency Dashboard issue or self-hosted workflow exists. Gradle, NuGet,
   JetBrains products and audited tools have had no bot at all: on 2026-09-17, five of the eight
   stale pins had no pull request proposing them.
2. **Bot pull requests cannot pass the gate on their own.** A bot bumps a manifest but not
   `config/supply-chain.json`, lock files or verification metadata. Dependabot pull request #12 has
   sat red for three days for that reason.
3. **Dependabot and the documented policy run weekly**, while the freshness check runs daily.

The drift itself is now tracked in a single issue rather than a red run (commit `6409272`). This
design removes the drift at its source.

## Decisions

| Decision | Choice |
|---|---|
| How Renovate runs | Self-hosted in a scheduled workflow, not the Mend-hosted app |
| Who updates GitHub Actions | Renovate; Dependabot keeps security alerts only |
| Cadence | Daily, weekends included |
| Merging | Automerge for non-major updates, gated by a seven-day release age |
| Excluded from automerge | Rider IDE, Rider SDK and ReSharper SDK, grouped into one pull request |
| Still manual | Major updates, mirrored pins in `scripts/tests`, Rider and RustRover relocks |

## Components

| File | Change | Responsibility |
|---|---|---|
| `.github/workflows/renovate.yml` | new | Daily schedule and `workflow_dispatch`. Mints a GitHub App installation token and runs `renovatebot/github-action`. |
| `.github/renovate-global.json` | new | Self-hosted administration config: target repository, `onboarding: false`, `binarySource: install`, and `allowedCommands` reduced to one anchored regex. |
| `.github/renovate.json` | changed | Adds the `github-actions` manager, daily schedule, automerge rules and the `postUpgradeTasks` hook. |
| `.github/dependabot.yml` | removed | No Dependabot version updates. |
| `scripts/check-dependency-automation.py` and its tests | changed | Enforces the new ownership, automerge and hook policy. |
| `config/supply-chain.json`, `scripts/check-supply-chain.py` | changed | Inventories and requires the two new actions and the Renovate image. |
| `DEPENDENCY-POLICY.md` | changed | States the new policy; written and enforced policy must never diverge. |

## Flow of one run

1. The schedule starts `renovate.yml` on `main`.
2. `actions/create-github-app-token` exchanges the App key for a one-hour installation token scoped
   to this repository.
3. Renovate looks up updates for `gradle`, `gradle-wrapper`, `nuget`, the JetBrains `custom.regex`
   managers and `github-actions`.
4. For each branch it edits the manifest, then runs the hook
   `python3 scripts/sync-supply-chain.py --online`, which rewrites the inventory and, for
   verifier-only JetBrains products, the lock line and the verification-metadata component.
5. Renovate commits the manifest and the files the hook changed, pushes with the App token and opens
   or updates the pull request. Because the push is not made with `GITHUB_TOKEN`, CI runs.

| Outcome at `CI / Gate` | Remaining human work |
|---|---|
| Green: no mirrored pin involved | none for non-major updates, which automerge; merge a major |
| Red on `Workflow security`: a pin mirrored in `scripts/tests` | edit the named line, then merge |
| Red: Rider or RustRover, refused by the hook | relock by hand, as today |

A network failure inside the hook leaves the derived files unwritten; the pull request arrives red
and the next daily run rebases the branch and runs the hook again. Once a human commits to a Renovate
branch, Renovate stops rewriting it, and rebasing onto `main` becomes the human's task, because the
ruleset requires an up-to-date branch.

## Security model

Two identities, two scopes:

| Identity | Lifetime | Rights | Used by |
|---|---|---|---|
| App installation token | one hour, per run | contents, pull requests, issues, checks, commit statuses and workflows write; administration and metadata read | Renovate |
| Workflow `GITHUB_TOKEN` | per job | `contents: read` | the `sync-supply-chain.py` hook |

The App private key is the asset to protect: whoever holds it can rewrite workflows on a branch.

- `renovate.yml` never runs on `pull_request` or `pull_request_target`; no proposed code runs with
  the key in reach.
- The key is an environment secret of `renovate`, whose deployment policy allows `main` only. A
  workflow modified on another branch cannot read it.
- `allowedCommands` admits only the exact hook command. An altered `renovate.json` cannot run
  anything else.
- The hook runs the scripts from `main`: Renovate edits manifests and never `scripts/`.
- Actions are pinned by commit SHA and the Renovate image by digest, both inventoried.
  `harden-runner` audits egress, and checkout sets `persist-credentials: false`.

Trust in upstream data is unchanged from running the hook by hand. A tampered `.sha256` fails the
plugin verifier jobs, which download the real archive, so it never reaches automerge. An upstream
server compromised to serve both the archive and its checksum is what the seven-day release age
covers.

Automerge is bounded four times: non-major only, release at least seven days old, `CI / Gate`
green, Rider group excluded. Merges squash, which `required_linear_history` needs.

## Policy changes enforced by `check-dependency-automation.py`

| Current rule | New rule |
|---|---|
| Renovate managers: `gradle`, `gradle-wrapper`, `nuget`, `custom.regex` | plus `github-actions` |
| `dependabot.yml` must hold a `github-actions` block | `dependabot.yml` must be absent |
| One catch-all rule disables automerge | automerge stays `false` by default; one rule enables it for `minor`, `patch` and `digest` updates |
| `minimumReleaseAge` forbidden everywhere | allowed only inside the automerge rule, only as `"7 days"`, the value of `FRESHNESS_GRACE` |
| `platformAutomerge` forbidden | required to be `true` |
| none | Rider IDE (`RD`), `JetBrains.Rider.SDK` and `JetBrains.ReSharper.SDK.Tests` share one group with automerge `false` |
| none | the hook is exactly one command, `executionMode: "branch"`, and `fileFilters` lists exactly `config/supply-chain.json`, `gradle.lockfile` and `gradle/verification-metadata.xml` |
| none | `renovate-global.json` `allowedCommands` is exactly the anchored hook regex |
| none | `renovate.yml` has no pull request trigger, uses `environment: renovate`, pins every action and runs `harden-runner` |
| none | `rebaseWhen` keeps branches up to date with `main` |
| `lockFileMaintenance` required, enabled every Monday | **disabled** (proposed during spec review, see below) |

The seven days are read from `FRESHNESS_GRACE` by the test, not repeated: changing one without the
other fails the suite.

### Why lock maintenance is disabled

Renovate's lock maintenance regenerates every Gradle lock with a global `--write-locks`. Run by hand
three times in the week of 2026-09-09, that command damaged the locks each time: it dropped every
`bundledModule:intellij-platform-test-runtime` entry of the products it did not resolve, rewrote the
`empty=` line, moved `verifier-cli` to an unpinned release and locked a
`JetBrains.Rider:2026.3-EAP1` prerelease. Every bump since has transplanted only the intended entries
by hand. Automating that command weekly would open a damaged pull request every Monday.

### Immediate eligibility

Releases stay eligible immediately, as `DEPENDENCY-POLICY.md` requires; the sentence
*stable releases are eligible immediately* stays in that document, which the policy check reads. `internalChecksFilter` is
set to `"none"`, so Renovate opens the pull request at once with a pending stability status, and
only automerge waits for the seven days.

## Testing

Test-driven, like the rest of the repository:

- Each row of the policy table gets a failing test first, including the refusals: `minimumReleaseAge`
  outside the automerge rule or with any value but `FRESHNESS_GRACE`, a `pull_request` trigger on
  `renovate.yml`, an unanchored `allowedCommands`.
- `renovate.yml` security invariants are pinned the way `security-audit.yml` pins its own.
- `actionlint` passes, and `zizmor` in auditor persona reports no finding.

## Rollout

1. **Merge in dry run.** `.github/renovate-global.json` sets `dryRun: "full"` for every run,
   scheduled or dispatched. Renovate computes everything and writes nothing.
2. **One manual dry run**, whose logs are reviewed together: branches it would open, whether Python
   installs, whether the hook reaches the network, whether the grouped Rider update is recognised.
3. **Lift the dry run** in a dedicated commit, the only change that enables writing.
4. Close pull request #12 once Renovate opens its first GitHub Actions pull request.

## Verifications during implementation, each with its decision

| Question | If confirmed | If not |
|---|---|---|
| Can `renovatebot/github-action` run the Renovate image pinned by digest? | pin by digest | stop and report before going further: an image pinned by tag alone breaks the supply-chain policy |
| With `platformAutomerge: true`, does Renovate withhold enabling native auto-merge until the release age is met? | keep `platformAutomerge: true` | set `platformAutomerge: false`: Renovate merges on its next run once every check, stability included, passes, at most a day later |
| Does the hook receive the read-only `GITHUB_TOKEN` through the self-hosted environment options? | keep it | the dry run shows GitHub lookups failing; stop and report |
| For an ordinary Gradle library bump, does Renovate update `gradle.lockfile` and `verification-metadata.xml` inside its container without the damage described above? | keep Renovate's artifact update | those pull requests arrive red without derived files and stay manual, as today: no regression, and the gap is reported |

## Already done outside the repository

- GitHub App `perf-sentinel-renovate` (App ID 4980616), webhook disabled, installable on this
  account only, installed on this repository only, with exactly the permissions above.
- Environment `renovate`: deployment limited to `main`, no required reviewer, no wait timer, secrets
  `RENOVATE_APP_ID` and `RENOVATE_APP_PRIVATE_KEY`; no `RENOVATE_*` secret at repository level.
- Repository setting *Allow auto-merge* enabled.

## Out of scope

- Relocking Rider and RustRover, and moving their JetBrains Runtime pins.
- Editing the pins mirrored in `scripts/tests`, a deliberate second edit.
- Changing the drift issue introduced by commit `6409272`.
