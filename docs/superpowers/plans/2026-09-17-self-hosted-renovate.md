# Self-hosted Renovate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Renovate from a scheduled workflow so every dependency pull request arrives with its supply-chain inventory and derived files already synchronised, and non-major updates merge themselves once they have matured seven days.

**Architecture:** A `main`-only workflow mints a GitHub App token and runs the digest-pinned Renovate image. Renovate's `postUpgradeTasks` hook runs `sync-supply-chain.py --online` on each branch before pushing. The policy checkers are rewritten so the new ownership, automerge and hook rules are enforced, and the checker learns to validate an image that releases several times a day.

**Tech Stack:** GitHub Actions, Renovate 44 (self-hosted), Python 3 standard library, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-17-self-hosted-renovate-design.md`

## Global Constraints

- Scripts use the Python standard library only and pass `uvx ruff check scripts tools`.
- Every commit leaves these green: `python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'`, `python3 scripts/check-supply-chain.py`, `python3 scripts/check-dependency-automation.py`, `python3 scripts/check-analysis-config.py`, `python3 scripts/check-repository-policy.py --repo robintra/perf-sentinel-jetbrains-plugin`.
- Commit messages are one subject line, no body, no attribution trailer. Stage explicit paths only; never stage `CLAUDE.md`. Commit to `main`.
- Workflow files pass `actionlint` and `uvx zizmor@1.30.0 --offline --no-config --persona=auditor <file>` with no finding.
- Pins, all verified on 2026-09-17:
  - `renovatebot/github-action` `v46.2.6` = `37beffda261423addd537c33f2d126df7f6ffbab`, released `2026-09-07T01:23:33Z` (latest release older than seven days).
  - `actions/create-github-app-token` `v3.2.0` = `bcd2ba49218906704ab6c1aa796996da409d3eb1`, released `2026-05-12T23:31:37Z`.
  - Image `renovate/renovate` (Docker Hub) `44.74.1@sha256:bacd588fd6bdc64c10167a81f17699bf841460a48daa57e4a9a0f8911cf45f9b`; GitHub release `44.74.1` published `2026-09-09T23:48:34Z`.
  - Reused as already pinned in the repository: `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1`, `step-security/harden-runner@e14015d583714f6e62063499dc959a02595150a1`.
- GitHub side, already done: App `perf-sentinel-renovate` (App ID `4980616`) installed on this repository only; environment `renovate` deployable from `main` only; environment secrets `RENOVATE_APP_ID` and `RENOVATE_APP_PRIVATE_KEY`; repository setting *Allow auto-merge* on.
- The release age is `FRESHNESS_GRACE` from `scripts/check-supply-chain.py` (seven days); code derives it, never repeats `7`.

## Planning decisions (refinements of the spec, found while reading the code)

1. **Docker Hub, not GHCR.** Tag `44.74.1` has digest `sha256:bacd588f…` on Docker Hub and `sha256:b5e35387…` on GHCR. `verify_container` validates Docker Hub, so the workflow pulls `renovate/renovate` from Docker Hub.
2. **Image eligibility comes from paginated GitHub releases.** Renovate published 100 releases between 2026-09-05 and 2026-09-17; the Docker Hub scan of 30 tags would never reach a seven-day-old release.
3. **Renovate updates its own image** through a `custom.regex` manager on `renovate.yml`, in its own group, so the pin does not fall behind daily.
4. **Schedule window.** `renovate.json` schedules `after 6:00am and before 10:00am` Europe/Paris and the workflow runs at `05:17` UTC, inside the window in both summer and winter time even if GitHub delays the cron.
5. **Dry run lives in `renovate-global.json` only.** The spec's `workflow_dispatch` input is dropped: one switch, lifted by one commit. Task 3 amends the spec sentence.
6. **Workflow invariants are tested in `scripts/tests/test_security_workflows.py`,** the repository's convention, rather than in the dependency policy checker.
7. **The two App secrets join `config/secret-inventory.json`,** whose set `check-analysis-config.py` enforces exactly.
8. **GitHub Actions keep their grouping** as `ordinary-github-actions`, carried over from Dependabot.

## File map

| File | Task | Responsibility |
|---|---|---|
| `scripts/check-supply-chain.py` | 1, 3 | Validate the Renovate image and require the new actions, tool and declaration |
| `scripts/sync-supply-chain.py` | 2 | Refresh the image entry and split its `tag@digest` declaration |
| `.github/workflows/renovate.yml` | 3 | Run Renovate from `main` with the App token |
| `.github/renovate-global.json` | 3, 4, 6 | Self-hosted administration config |
| `config/supply-chain.json`, `config/secret-inventory.json` | 3 | Inventory the new pins and secrets |
| `scripts/check-analysis-config.py` | 3 | Accept the two App secrets for the `renovate` job |
| `scripts/check-dependency-automation.py` | 4, 6 | Enforce ownership, automerge, hook and global config |
| `.github/renovate.json` | 4 | Managers, schedule, automerge, hook, self-updating image |
| `.github/dependabot.yml` | 4 | Deleted |
| `DEPENDENCY-POLICY.md` | 4 | The written policy |
| tests under `scripts/tests/` | 1–4, 6 | One failing test before each change |

---

### Task 1: Validate a digest-pinned image that releases several times a day

**Files:**
- Modify: `scripts/check-supply-chain.py` (maps after `CONTAINER_REPOSITORIES`, `OPTIONAL_DIRECT_DECLARATIONS`, `declared_versions`, `check_declarations`, `verify_container`)
- Test: `scripts/tests/test_check_supply_chain.py`

**Interfaces:**
- Produces: `CONTAINER_REPOSITORIES["Renovate image"] == "renovate/renovate"`; `CONTAINER_RELEASE_REPOS == {"Renovate image": "renovatebot/renovate"}`; `IMAGE_DECLARATIONS == {"qodana.yml#linter", ".github/workflows/renovate.yml#renovate-version"}`; `github_release_candidates(client, repo: str, now: datetime) -> list[tuple[str, datetime]]`; `declared_versions(root, ".github/workflows/renovate.yml#renovate-version") -> ["<tag>@sha256:<digest>"]`.

- [ ] **Step 1: Write the failing tests**

Add at the top of `scripts/tests/test_check_supply_chain.py`, next to the existing imports:

```python
from datetime import UTC, datetime
```

Add this class after `ExitStatusTest`:

```python
class RenovateImageTest(unittest.TestCase):
    """Renovate ships several releases a day, faster than one page of 100 covers seven days."""

    NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    DIGEST = "sha256:" + "b" * 64

    def setUp(self):
        self.checker = load_checker()

    @staticmethod
    def release(tag, published):
        return {"tag_name": tag, "published_at": published, "draft": False, "prerelease": False}

    def client(self):
        pages = [
            [self.release("44.80.0", "2026-09-16T10:00:00Z"), self.release("44.79.0", "2026-09-12T10:00:00Z")],
            [self.release("44.74.1", "2026-09-09T23:48:34Z"), self.release("44.70.0", "2026-09-05T08:00:00Z")],
        ]
        digest = self.DIGEST

        class Client:
            def __init__(self):
                self.urls = []

            def json(self, url):
                self.urls.append(url)
                if url.startswith("https://hub.docker.com/"):
                    return {"digest": digest}
                page = int(url.rsplit("page=", 1)[1])
                return pages[page - 1] if page <= len(pages) else []

        return Client()

    def image(self, release, released_at):
        return {
            "name": "Renovate image", "kind": "container", "version": self.DIGEST,
            "release": release, "releasedAt": released_at,
            "source": "https://hub.docker.com/r/renovate/renovate",
        }

    def test_the_workflow_declares_the_image_as_tag_at_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "renovate.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(f"          renovate-version: 44.74.1@{self.DIGEST}\n", encoding="utf-8")
            self.assertEqual(
                [f"44.74.1@{self.DIGEST}"],
                self.checker.declared_versions(root, ".github/workflows/renovate.yml#renovate-version"),
            )

    def test_the_latest_eligible_release_is_found_past_the_first_page(self):
        client = self.client()
        self.checker.verify_container(client, self.image("44.74.1", "2026-09-09T23:48:34Z"), self.NOW)
        self.assertTrue(any(url.endswith("page=2") for url in client.urls))
        self.assertFalse(any(url.endswith("page=3") for url in client.urls))

    def test_an_image_behind_the_latest_eligible_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"not latest eligible stable container \(44\.74\.1\)"):
            self.checker.verify_container(self.client(), self.image("44.70.0", "2026-09-05T08:00:00Z"), self.NOW)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_check_supply_chain.RenovateImageTest -v`
Expected: 3 failures or errors — `declared_versions` returns `[]` and `verify_container` raises `KeyError: 'Renovate image'`.

- [ ] **Step 3: Implement**

In `scripts/check-supply-chain.py`, replace

```python
CONTAINER_REPOSITORIES = {
    "Qodana JVM Community image": "jetbrains/qodana-jvm-community",
}
```

with

```python
CONTAINER_REPOSITORIES = {
    "Qodana JVM Community image": "jetbrains/qodana-jvm-community",
    "Renovate image": "renovate/renovate",
}
# Images whose tags are GitHub releases. Their digest still comes from Docker Hub,
# but eligibility is read from the releases, which carry an exact publication time.
CONTAINER_RELEASE_REPOS = {
    "Renovate image": "renovatebot/renovate",
}
# Declarations that pin an image as `tag@digest`, compared as `release@version`.
IMAGE_DECLARATIONS = {"qodana.yml#linter", ".github/workflows/renovate.yml#renovate-version"}
```

Replace

```python
OPTIONAL_DIRECT_DECLARATIONS = {
    "gradle/libs.versions.toml#kover",
```

with

```python
OPTIONAL_DIRECT_DECLARATIONS = {
    ".github/workflows/renovate.yml#renovate-version",
    "gradle/libs.versions.toml#kover",
```

In `declared_versions`, just before its final `return []`, add:

```python
    if relative == ".github/workflows/renovate.yml":
        match = re.search(r'^\s*renovate-version:\s*"?([0-9][0-9.]*)@(sha256:[0-9a-f]{64})"?\s*$', text, re.M)
        return [f"{match.group(1)}@{match.group(2)}"] if match else []
```

In `check_declarations`, replace `if declaration == "qodana.yml#linter":` with `if declaration in IMAGE_DECLARATIONS:`.

Add this function directly above `def verify_container`:

```python
def github_release_candidates(client, repo, now):
    """Stable releases, read back until one is old enough to be eligible."""
    candidates = []
    for page in range(1, 6):
        releases = client.json(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
        stable = [
            (item["tag_name"].lstrip("v"), parse_instant(item["published_at"]))
            for item in releases
            if not item["draft"] and not item["prerelease"] and item.get("published_at")
        ]
        candidates += stable
        if not releases or any(published <= now - FRESHNESS_GRACE for _, published in stable):
            break
    return candidates
```

Replace the body of `verify_container` with:

```python
def verify_container(client, dependency, now):
    repository = CONTAINER_REPOSITORIES[dependency["name"]]
    data = client.json(f"https://hub.docker.com/v2/repositories/{repository}/tags/{dependency['release']}")
    if dependency["version"] != data.get("digest"):
        raise ValueError("container digest mismatch")
    release_repository = CONTAINER_RELEASE_REPOS.get(dependency["name"])
    if release_repository:
        candidates = github_release_candidates(client, release_repository, now)
    else:
        tags = client.json(f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=30")["results"]
        candidates = [
            (item["name"], parse_instant(item["last_updated"]))
            for item in tags
            if re.fullmatch(r"\d{4}\.\d+", item["name"])
        ]
    tag_dependency = dict(dependency, version=dependency["release"])
    validate_release(tag_dependency, candidates, now, "container")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_check_supply_chain.RenovateImageTest -v && python3 -B -m unittest discover -s scripts/tests -p 'test_*.py' && python3 scripts/check-supply-chain.py && uvx ruff check scripts tools`
Expected: all OK; `Supply-chain inputs are locked, complete, and stable.`

- [ ] **Step 5: Commit**

```bash
git add scripts/check-supply-chain.py scripts/tests/test_check_supply_chain.py
git commit -m "fix(supply-chain): validate a digest-pinned image that releases several times a day"
```

---

### Task 2: Let the sync script maintain the Renovate image entry

**Files:**
- Modify: `scripts/sync-supply-chain.py` (`declared_fields`, `declaration_changes`, `online_metadata`)
- Test: `scripts/tests/test_sync_supply_chain.py`

**Interfaces:**
- Consumes: `checker.IMAGE_DECLARATIONS`, `checker.CONTAINER_RELEASE_REPOS` (Task 1).
- Produces: `declared_fields(checker, declaration: str, actual: str) -> dict[str, str]`; `online_metadata` returns `{"releasedAt": <published_at>}` for a container listed in `CONTAINER_RELEASE_REPOS`.

- [ ] **Step 1: Write the failing tests**

Add to `SyncSupplyChainTest` in `scripts/tests/test_sync_supply_chain.py`:

```python
    def test_an_image_declaration_splits_into_release_and_digest(self):
        module = load_sync()
        digest = "sha256:" + "c" * 64
        self.assertEqual(
            {"release": "44.74.1", "version": digest},
            module.declared_fields(module.load_checker(), ".github/workflows/renovate.yml#renovate-version", f"44.74.1@{digest}"),
        )

    def test_the_renovate_image_release_date_comes_from_its_github_release(self):
        module = load_sync()
        client = FeedClient({"tag_name": "44.74.1", "published_at": "2026-09-09T23:48:34Z"})
        dependency = {
            "name": "Renovate image", "kind": "container", "version": "sha256:" + "c" * 64,
            "release": "44.74.1", "source": "https://hub.docker.com/r/renovate/renovate",
        }

        metadata = module.online_metadata(client, module.load_checker(), dependency)

        self.assertEqual({"releasedAt": "2026-09-09T23:48:34Z"}, metadata)
        self.assertEqual(["https://api.github.com/repos/renovatebot/renovate/releases/tags/44.74.1"], client.urls)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_sync_supply_chain -v`
Expected: the first test errors with `TypeError` (three arguments), the second raises `ValueError: no refresh route for kind container`.

- [ ] **Step 3: Implement**

In `scripts/sync-supply-chain.py`, replace

```python
def declared_fields(declaration: str, actual: str) -> dict[str, str]:
    """The fields check-supply-chain compares for one declaration."""
    if declaration == "qodana.yml#linter":
```

with

```python
def declared_fields(checker, declaration: str, actual: str) -> dict[str, str]:
    """The fields check-supply-chain compares for one declaration."""
    if declaration in checker.IMAGE_DECLARATIONS:
```

In `declaration_changes`, replace `declared_fields(declaration, distinct.pop())` with `declared_fields(checker, declaration, distinct.pop())`.

In `online_metadata`, directly after the `jetbrains-product` branch, add:

```python
    if kind == "container" and name in checker.CONTAINER_RELEASE_REPOS:
        release = client.json(
            f"https://api.github.com/repos/{checker.CONTAINER_RELEASE_REPOS[name]}/releases/tags/{dependency['release']}"
        )
        return {"releasedAt": instant(release["published_at"])}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -B -m unittest discover -s scripts/tests -p 'test_*.py' && uvx ruff check scripts tools`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync-supply-chain.py scripts/tests/test_sync_supply_chain.py
git commit -m "fix(supply-chain): keep a digest-pinned release image in step with its declaration"
```

---

### Task 3: Run Renovate from main in dry run

**Files:**
- Create: `.github/workflows/renovate.yml`, `.github/renovate-global.json`
- Modify: `scripts/check-supply-chain.py` (`REQUIRED_ACTIONS`, `REQUIRED_TOOLS`, declaration moves from optional to direct), `config/supply-chain.json`, `config/secret-inventory.json`, `scripts/check-analysis-config.py` (`expected_scopes`), `docs/superpowers/specs/2026-09-17-self-hosted-renovate-design.md` (rollout step 1)
- Test: `scripts/tests/test_security_workflows.py`, `scripts/tests/test_check_supply_chain.py` (fixture), `scripts/tests/test_check_analysis_config.py` (fixture)

**Interfaces:**
- Consumes: Task 1 declaration parser and container validation.
- Produces: job id `renovate`; admin config at `.github/renovate-global.json` containing `"dryRun": "full"`; hook command string `python3 scripts/sync-supply-chain.py --online`.

- [ ] **Step 1: Write the failing workflow tests**

In `scripts/tests/test_security_workflows.py`, change the imports to:

```python
import json
import re
import unittest
from pathlib import Path
```

Add this class before `class CodeQLWorkflowTests`:

```python
class RenovateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "renovate.yml").read_text(encoding="utf-8")
        cls.triggers = cls.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        cls.global_config = json.loads((REPOSITORY / ".github/renovate-global.json").read_text(encoding="utf-8"))

    def test_never_runs_on_a_pull_request(self):
        self.assertIn("schedule:", self.triggers)
        self.assertIn("workflow_dispatch:", self.triggers)
        self.assertNotIn("pull_request", self.triggers)

    def test_the_app_key_is_reachable_only_through_the_renovate_environment(self):
        self.assertIn("    environment: renovate\n", self.text)
        self.assertEqual({"RENOVATE_APP_ID", "RENOVATE_APP_PRIVATE_KEY"}, set(re.findall(r"secrets\.(\w+)", self.text)))
        self.assertNotIn(": write", self.text)

    def test_runs_the_digest_pinned_image_behind_harden_runner(self):
        self.assertIn("renovate-image: renovate/renovate\n", self.text)
        self.assertRegex(self.text, r"renovate-version: \d+\.\d+\.\d+@sha256:[0-9a-f]{64}\n")
        self.assertIn("step-security/harden-runner@", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_the_hook_gets_the_read_only_workflow_token_not_the_app_token(self):
        self.assertIn("""RENOVATE_CUSTOM_ENV_VARIABLES: '{"GITHUB_TOKEN": "${{ github.token }}"}'""", self.text)
        self.assertIn("token: ${{ steps.app-token.outputs.token }}", self.text)

    def test_global_config_allows_only_the_sync_hook_and_starts_in_dry_run(self):
        self.assertEqual(["^python3 scripts/sync-supply-chain\\.py --online$"], self.global_config["allowedCommands"])
        self.assertEqual("full", self.global_config["dryRun"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_security_workflows.RenovateWorkflowTests -v`
Expected: ERROR, `FileNotFoundError` for `renovate.yml`.

- [ ] **Step 3: Create the workflow and the admin config**

Create `.github/workflows/renovate.yml`:

```yaml
name: Renovate

# Self-hosted Renovate. It runs from main only and never on a pull request, so no
# proposed change can reach the App key the renovate environment holds. Renovate
# pushes with the App token, which lets CI run on its branches; the sync hook only
# reads public release data and gets the read-only workflow token instead.

on:
  schedule:
    # 05:17 UTC stays inside the 06:00-10:00 Europe/Paris window of renovate.json
    # in summer and winter time, even when GitHub starts the job late.
    - cron: '17 5 * * *'
  workflow_dispatch:

permissions:
  contents: read  # the hook reads public release metadata; Renovate writes with the App token

concurrency:
  group: renovate
  cancel-in-progress: false

jobs:
  renovate:
    name: Propose dependency updates
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    environment: renovate
    steps:
      - uses: step-security/harden-runner@e14015d583714f6e62063499dc959a02595150a1
        with: {egress-policy: audit}
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with: {persist-credentials: false}
      - id: app-token
        uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1
        with:
          app-id: ${{ secrets.RENOVATE_APP_ID }}
          private-key: ${{ secrets.RENOVATE_APP_PRIVATE_KEY }}
      - uses: renovatebot/github-action@37beffda261423addd537c33f2d126df7f6ffbab
        env:
          RENOVATE_CUSTOM_ENV_VARIABLES: '{"GITHUB_TOKEN": "${{ github.token }}"}'
        with:
          configurationFile: .github/renovate-global.json
          token: ${{ steps.app-token.outputs.token }}
          renovate-image: renovate/renovate
          renovate-version: 44.74.1@sha256:bacd588fd6bdc64c10167a81f17699bf841460a48daa57e4a9a0f8911cf45f9b
```

Create `.github/renovate-global.json`:

```json
{
  "platform": "github",
  "repositories": [
    "robintra/perf-sentinel-jetbrains-plugin"
  ],
  "onboarding": false,
  "requireConfig": "required",
  "binarySource": "install",
  "allowedCommands": [
    "^python3 scripts/sync-supply-chain\\.py --online$"
  ],
  "dryRun": "full"
}
```

- [ ] **Step 4: Run the workflow tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_security_workflows.RenovateWorkflowTests -v`
Expected: 5 tests OK.

- [ ] **Step 5: Require and inventory the new pins**

In `scripts/check-supply-chain.py`, in `REQUIRED_ACTIONS`, replace the line `google/osv-scanner-action gitleaks/gitleaks-action zizmorcore/zizmor-action gradle/actions` with `google/osv-scanner-action gitleaks/gitleaks-action zizmorcore/zizmor-action gradle/actions renovatebot/github-action actions/create-github-app-token`.

In `REQUIRED_TOOLS`, replace `"Qodana CLI", "Qodana JVM Community image",` with `"Qodana CLI", "Qodana JVM Community image", "Renovate image",`.

Move `.github/workflows/renovate.yml#renovate-version` from `OPTIONAL_DIRECT_DECLARATIONS` into `DIRECT_DECLARATIONS`: delete the line added in Task 1, and in `DIRECT_DECLARATIONS` replace `;qodana.yml#linter` with `;qodana.yml#linter;.github/workflows/renovate.yml#renovate-version`.

In `scripts/tests/test_check_supply_chain.py`, in the `required_actions` tuple, replace `"gradle/actions",` with `"gradle/actions", "renovatebot/github-action", "actions/create-github-app-token",`.

Add the three inventory entries:

```bash
python3 - <<'PY'
import json, pathlib
path = pathlib.Path("config/supply-chain.json")
inventory = json.loads(path.read_text(encoding="utf-8"))
inventory["dependencies"] += [
    {"name": "renovatebot/github-action", "kind": "github-action",
     "version": "37beffda261423addd537c33f2d126df7f6ffbab", "release": "v46.2.6",
     "releasedAt": "2026-09-07T01:23:33Z",
     "source": "https://github.com/renovatebot/github-action/releases/tag/v46.2.6"},
    {"name": "actions/create-github-app-token", "kind": "github-action",
     "version": "bcd2ba49218906704ab6c1aa796996da409d3eb1", "release": "v3.2.0",
     "releasedAt": "2026-05-12T23:31:37Z",
     "source": "https://github.com/actions/create-github-app-token/releases/tag/v3.2.0"},
    {"name": "Renovate image", "kind": "container",
     "version": "sha256:bacd588fd6bdc64c10167a81f17699bf841460a48daa57e4a9a0f8911cf45f9b",
     "release": "44.74.1", "releasedAt": "2026-09-09T23:48:34Z",
     "source": "https://hub.docker.com/r/renovate/renovate",
     "declaration": ".github/workflows/renovate.yml#renovate-version"},
]
path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
```

- [ ] **Step 6: Inventory the two App secrets**

In `scripts/check-analysis-config.py`, in `expected_scopes`, replace `"QODANA_TOKEN": ["qodana-jvm"],` with:

```python
        "QODANA_TOKEN": ["qodana-jvm"],
        "RENOVATE_APP_ID": ["renovate"],
        "RENOVATE_APP_PRIVATE_KEY": ["renovate"],
```

In `scripts/tests/test_check_analysis_config.py`, in the fixture's `"secrets"` list, directly after the `QODANA_TOKEN` dict, add:

```python
                *[
                    {
                        "name": name,
                        "owner": "Maintainers",
                        "trustedJobScope": ["renovate"],
                        "purpose": f"Let the scheduled Renovate job mint its installation token from {name}.",
                        "rotationProcedure": f"Regenerate {name} on the GitHub App, replace it in the renovate environment, and run Renovate once by hand.",
                    }
                    for name in ("RENOVATE_APP_ID", "RENOVATE_APP_PRIVATE_KEY")
                ],
```

Then add the real entries:

```bash
python3 - <<'PY'
import json, pathlib
path = pathlib.Path("config/secret-inventory.json")
inventory = json.loads(path.read_text(encoding="utf-8"))
inventory["secrets"] += [
    {"name": "RENOVATE_APP_ID", "owner": "Maintainers", "trustedJobScope": ["renovate"],
     "purpose": "Identify the perf-sentinel-renovate GitHub App whose installation token the scheduled Renovate job mints.",
     "rotationProcedure": "Replace the environment secret only if the App is recreated, then run Renovate once by hand."},
    {"name": "RENOVATE_APP_PRIVATE_KEY", "owner": "Maintainers", "trustedJobScope": ["renovate"],
     "purpose": "Sign the installation token request of the perf-sentinel-renovate GitHub App, only from main through the renovate environment.",
     "rotationProcedure": "Generate a new key on the App, replace the renovate environment secret, delete the old key on the App, and run Renovate once by hand."},
]
path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
```

- [ ] **Step 7: Amend the spec's rollout step**

In `docs/superpowers/specs/2026-09-17-self-hosted-renovate-design.md`, replace

```
1. **Merge in dry run.** The scheduled run forces `dryRun: "full"`; `workflow_dispatch` accepts the
   same input. Renovate computes everything and writes nothing.
```

with

```
1. **Merge in dry run.** `.github/renovate-global.json` sets `dryRun: "full"` for every run,
   scheduled or dispatched. Renovate computes everything and writes nothing.
```

- [ ] **Step 8: Verify everything**

Run:

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'
python3 scripts/check-supply-chain.py
GITHUB_TOKEN=$(gh auth token) python3 scripts/check-supply-chain.py --online; echo "exit=$?"
python3 scripts/check-analysis-config.py
python3 scripts/check-repository-policy.py --repo robintra/perf-sentinel-jetbrains-plugin
actionlint .github/workflows/renovate.yml
uvx zizmor@1.30.0 --offline --no-config --persona=auditor .github/workflows/renovate.yml
uvx ruff check scripts tools
```

Expected: tests OK; offline check `Supply-chain inputs are locked, complete, and stable.`; the online check exits `0` or `2`; if a line names `renovatebot/github-action`, `actions/create-github-app-token` or `Renovate image` as behind, move that pin to the named version (for the image, take the digest from `https://hub.docker.com/v2/repositories/renovate/renovate/tags/<version>` and the date from the GitHub release) in the workflow and the inventory, and rerun; `analysis configuration: OK`; `repository policy: OK`; actionlint silent; zizmor `No findings to report.`; ruff `All checks passed!`. If zizmor reports a finding, fix it in the workflow before committing.

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/renovate.yml .github/renovate-global.json scripts/check-supply-chain.py config/supply-chain.json config/secret-inventory.json scripts/check-analysis-config.py docs/superpowers/specs/2026-09-17-self-hosted-renovate-design.md scripts/tests/test_security_workflows.py scripts/tests/test_check_supply_chain.py scripts/tests/test_check_analysis_config.py
git commit -m "ci(renovate): run self-hosted Renovate from main in dry run"
```

---

### Task 4: Switch the dependency policy to Renovate with matured automerge

This task changes the checker, the configuration and the written policy together: the checker validates all three against each other, so no earlier commit could stay green.

**Files:**
- Modify: `scripts/check-dependency-automation.py`, `.github/renovate.json`, `DEPENDENCY-POLICY.md`
- Delete: `.github/dependabot.yml`
- Test: `scripts/tests/test_dependency_automation.py`

**Interfaces:**
- Consumes: `FRESHNESS_GRACE` from `scripts/check-supply-chain.py`; `.github/renovate-global.json` from Task 3.
- Produces: `RELEASE_AGE == "7 days"`; `EXPECTED_POST_UPGRADE_TASKS`; `EXPECTED_GLOBAL_CONFIG` (with `"dryRun": "full"`, removed in Task 6).

- [ ] **Step 1: Rewrite the policy tests**

In `scripts/tests/test_dependency_automation.py`:

1. Replace `DEPENDABOT = REPOSITORY / ".github/dependabot.yml"` with `GLOBAL = REPOSITORY / ".github/renovate-global.json"`.
2. In `make_fixture`, replace `Path(".github/dependabot.yml")` with `Path(".github/renovate-global.json")`.
3. Delete `test_dependabot_owns_only_github_actions` and `test_checker_rejects_dependabot_limit_or_label_drift`.
4. In `test_renovate_owns_gradle_nuget_and_every_jetbrains_version_surface`, replace the expected manager set with `{"gradle", "gradle-wrapper", "nuget", "custom.regex", "github-actions"}`, and replace its last two lines with:

```python
        self.assertIn("github-actions", config["enabledManagers"])
        self.assertEqual(1, encoded.count("minimumReleaseAge"))
```

5. Replace `test_policy_is_stable_only_without_release_delay_or_auto_merge` with:

```python
    def test_policy_is_stable_only_and_merges_matured_non_major_updates(self):
        policy = POLICY.read_text(encoding="utf-8")
        for expected in (
            "stable releases are eligible immediately",
            "Renovate owns Gradle",
            "Renovate also owns GitHub Actions",
            "merge on their own once seven days old",
        ):
            self.assertIn(expected, policy)
        self.assertFalse((REPOSITORY / ".github/dependabot.yml").exists())
        config = RENOVATE.read_text(encoding="utf-8")
        for forbidden in ("stabilityDays", "cooldown", "automergeType"):
            self.assertNotIn(forbidden, config)
```

6. In `test_checker_rejects_prerelease_and_ordinary_update_delay`, replace `self.assertIn("stable releases must be immediate", result.stderr)` with `self.assertIn("release delay is allowed only", result.stderr)`.
7. Replace `test_checker_rejects_duplicate_ownership_and_auto_merge` with:

```python
    def test_checker_rejects_automerge_outside_its_rule_and_a_returning_dependabot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            config = json.loads((root / ".github/renovate.json").read_text(encoding="utf-8"))
            config["automerge"] = True
            (root / ".github/renovate.json").write_text(json.dumps(config), encoding="utf-8")
            (root / ".github/dependabot.yml").write_text('{"version": 2, "updates": []}', encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("auto-merge is allowed only", result.stderr)
            self.assertIn("Dependabot version updates must be absent", result.stderr)
```

8. In `test_checker_rejects_non_object_roots_without_traceback`, replace `Path(".github/dependabot.yml")` with `Path(".github/renovate-global.json")`.
9. In `test_security_alerts_have_one_owner`, replace `"GitHub-native security alerts only"` with `"GitHub-native security alerts"`.
10. Add:

```python
    def test_matured_non_major_updates_merge_after_the_freshness_grace(self):
        spec = importlib.util.spec_from_file_location("check_supply_chain", REPOSITORY / "scripts/check-supply-chain.py")
        supply = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supply)
        rules = json.loads(RENOVATE.read_text(encoding="utf-8"))["packageRules"]
        automerge = [rule for rule in rules if rule.get("automerge") is True]
        self.assertEqual(1, len(automerge))
        self.assertEqual(["minor", "patch", "digest"], automerge[0]["matchUpdateTypes"])
        self.assertEqual(f"{supply.FRESHNESS_GRACE.days} days", automerge[0]["minimumReleaseAge"])

    def test_checker_rejects_policy_switches_drifting(self):
        for key, value, message in (
            ("lockFileMaintenance", {"enabled": True}, "lock maintenance"),
            ("platformAutomerge", False, "native auto-merge"),
            ("postUpgradeTasks", {"commands": ["bash -c env"]}, "sync hook"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                result = run_with_mutation(directory, key, value)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)

    def test_checker_rejects_a_global_config_allowing_more_than_the_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_fixture(directory)
            path = root / ".github/renovate-global.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config["allowedCommands"].append(".*")
            path.write_text(json.dumps(config), encoding="utf-8")
            result = run_checker(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("global configuration", result.stderr)

    def test_rider_ide_and_sdks_move_together_by_hand(self):
        rules = json.loads(RENOVATE.read_text(encoding="utf-8"))["packageRules"]
        self.assertEqual(
            {
                "description": "Move the Rider IDE and SDKs together, merged by hand",
                "matchPackageNames": ["JetBrains.ReSharper.SDK.Tests", "JetBrains.Rider.SDK", "RD"],
                "groupName": "rider-ide-and-sdk",
                "automerge": False,
            },
            rules[-1],
        )
```

Add `import importlib.util` to the imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_dependency_automation -v`
Expected: several FAIL — the repository still has `dependabot.yml`, a weekly schedule and no automerge rule.

- [ ] **Step 3: Rewrite the checker constants**

In `scripts/check-dependency-automation.py`:

Add after the imports:

```python
import importlib.util


def load_supply_chain_checker():
    path = Path(__file__).resolve().parent / "check-supply-chain.py"
    spec = importlib.util.spec_from_file_location("check_supply_chain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The automerge rule waits exactly as long as the freshness check does before
# calling a pin behind, so the two windows can never drift apart.
RELEASE_AGE = f"{load_supply_chain_checker().FRESHNESS_GRACE.days} days"
HOOK_COMMAND = "python3 scripts/sync-supply-chain.py --online"
```

Replace:

- `EXPECTED_MANAGERS = {"gradle", "gradle-wrapper", "nuget", "custom.regex"}` → `EXPECTED_MANAGERS = {"gradle", "gradle-wrapper", "nuget", "custom.regex", "github-actions"}`
- `FORBIDDEN_DELAY_KEYS = {"cooldown", "minimumReleaseAge", "stabilityDays"}` → `FORBIDDEN_DELAY_KEYS = {"cooldown", "stabilityDays"}`
- `FORBIDDEN_AUTOMERGE_KEYS = {"automergeType", "platformAutomerge"}` → `FORBIDDEN_AUTOMERGE_KEYS = {"automergeType"}`
- In `RENOVATE_KEYS`, replace `"$schema", "customDatasources", "customManagers", "dependencyDashboard", "enabledManagers",` with `"$schema", "automergeStrategy", "customDatasources", "customManagers", "dependencyDashboard", "enabledManagers", "internalChecksFilter",` and replace `"osvVulnerabilityAlerts", "packageRules", "prConcurrentLimit",` with `"osvVulnerabilityAlerts", "packageRules", "platformAutomerge", "postUpgradeTasks", "prConcurrentLimit", "rebaseWhen",`.
- Delete the `DEPENDABOT_KEYS = …` line.

Replace the first two entries of `EXPECTED_PACKAGE_RULES` (the catch-all and the ordinary Gradle/NuGet group) with:

```python
    {
        "description": "Disable inherited auto-merge for every dependency",
        "matchPackageNames": ["*"],
        "automerge": False,
    },
    {
        "description": "Group ordinary non-major Gradle and NuGet updates",
        "matchManagers": ["gradle", "gradle-wrapper", "nuget", "custom.regex"],
        "matchUpdateTypes": ["minor", "patch"],
        "groupName": "ordinary-build-dependencies",
    },
    {
        "description": "Group ordinary non-major GitHub Actions updates",
        "matchManagers": ["github-actions"],
        "matchUpdateTypes": ["minor", "patch", "digest"],
        "groupName": "ordinary-github-actions",
    },
    {
        "description": "Keep the Renovate image update on its own",
        "matchPackageNames": ["renovate/renovate"],
        "groupName": "renovate-image",
    },
    {
        "description": "Merge non-major updates on their own once they have matured",
        "matchUpdateTypes": ["minor", "patch", "digest"],
        "minimumReleaseAge": RELEASE_AGE,
        "automerge": True,
    },
```

and append as the last entry of `EXPECTED_PACKAGE_RULES`:

```python
    {
        "description": "Move the Rider IDE and SDKs together, merged by hand",
        "matchPackageNames": ["JetBrains.ReSharper.SDK.Tests", "JetBrains.Rider.SDK", "RD"],
        "groupName": "rider-ide-and-sdk",
        "automerge": False,
    },
```

Add to `EXPECTED_CUSTOM_MANAGERS`:

```python
    "renovate/renovate": {"files": ["/^\\.github/workflows/renovate\\.yml$/"], "patterns": ["renovate-version:\\s*\"?(?<currentValue>[0-9.]+)@(?<currentDigest>sha256:[0-9a-f]{64})\"?"], "datasource": "docker", "versioning": "docker"},
```

Add after `EXPECTED_DATASOURCE`:

```python
EXPECTED_POST_UPGRADE_TASKS = {
    "commands": [HOOK_COMMAND],
    "fileFilters": ["config/supply-chain.json", "gradle.lockfile", "gradle/verification-metadata.xml"],
    "executionMode": "branch",
}
EXPECTED_GLOBAL_CONFIG = {
    "platform": "github",
    "repositories": ["robintra/perf-sentinel-jetbrains-plugin"],
    "onboarding": False,
    "requireConfig": "required",
    "binarySource": "install",
    "allowedCommands": ["^python3 scripts/sync-supply-chain\\.py --online$"],
    "dryRun": "full",
}
```

- [ ] **Step 4: Rewrite `validate`**

In `validate`, replace the block loading `dependabot` (from `try:` / `dependabot = load_json(root / ".github/dependabot.yml")` through its `return [f"Dependabot configuration is invalid: {error}"]`) with:

```python
    try:
        global_config = load_json(root / ".github/renovate-global.json")
    except (OSError, ValueError, TypeError) as error:
        return [f"Renovate global configuration is invalid: {error}"]
    if (root / ".github/dependabot.yml").exists() or (root / ".github/dependabot.yaml").exists():
        errors.append("Dependabot version updates must be absent: Renovate owns GitHub Actions")
```

Replace

```python
    if not isinstance(dependabot, dict):
        return ["Dependabot configuration schema is not closed"]
    if set(dependabot) != {"version", "updates"} or type(dependabot.get("version")) is not int or dependabot.get("version") != 2:
        errors.append("Dependabot configuration schema is not closed")
```

with

```python
    if not isinstance(global_config, dict):
        return ["Renovate global configuration schema is not closed"]
    if global_config != EXPECTED_GLOBAL_CONFIG:
        errors.append("Renovate global configuration must allow only the sync hook")
    elif not re.fullmatch(global_config["allowedCommands"][0], HOOK_COMMAND):
        errors.append("Renovate global configuration must allow only the sync hook")
```

Delete the two lines `if "github-actions" in manager_set:` / `errors.append("duplicate ownership: Renovate must not own GitHub Actions")`.

Delete the whole `updates = dependabot.get("updates")` block, through the line `errors.append("Dependabot pull request policy is not canonical")`.

Replace

```python
    keys = set(walk_keys(renovate)) | set(walk_keys(dependabot))
    if keys & FORBIDDEN_DELAY_KEYS:
        errors.append("stable releases must be immediate; release delays are forbidden")
    if keys & FORBIDDEN_AUTOMERGE_KEYS:
        errors.append("dependency auto-merge is forbidden")
    automerge_values = [value for key, value in walk_key_values(renovate) if key == "automerge"]
    if automerge_values != [False]:
        errors.append("dependency auto-merge must be disabled explicitly")
```

with

```python
    keys = set(walk_keys(renovate))
    if keys & FORBIDDEN_DELAY_KEYS:
        errors.append("stable releases must be immediate; release delays are forbidden")
    if keys & FORBIDDEN_AUTOMERGE_KEYS:
        errors.append("auto-merge is allowed only through the matured non-major rule")
    ages = [value for key, value in walk_key_values(renovate) if key == "minimumReleaseAge"]
    if ages != [RELEASE_AGE]:
        errors.append(f"a release delay is allowed only on the automerge rule, at {RELEASE_AGE}")
    automerge_values = [value for key, value in walk_key_values(renovate) if key == "automerge"]
    if automerge_values != [False, True, False]:
        errors.append("auto-merge is allowed only through the matured non-major rule")
    if renovate.get("platformAutomerge") is not True or renovate.get("automergeStrategy") != "squash":
        errors.append("matured updates must merge through native auto-merge, squashed")
    if renovate.get("internalChecksFilter") != "none" or renovate.get("rebaseWhen") != "behind-base-branch":
        errors.append("pull requests must open immediately and stay up to date with main")
    if renovate.get("postUpgradeTasks") != EXPECTED_POST_UPGRADE_TASKS:
        errors.append("the sync hook must be exactly the supply-chain synchronisation")
```

Replace

```python
    if renovate.get("schedule") != ["after 6:00am and before 7:00am on monday"]:
        errors.append("Renovate schedule is not canonical")
    maintenance = renovate.get("lockFileMaintenance")
    if maintenance != {"enabled": True, "schedule": ["after 6:00am and before 7:00am on monday"]}:
        errors.append("Renovate lock maintenance is not canonical")
```

with

```python
    if renovate.get("schedule") != ["after 6:00am and before 10:00am"]:
        errors.append("Renovate schedule is not canonical")
    # A global relock drops bundled-module entries and locks prerelease IDEs; see the design.
    if renovate.get("lockFileMaintenance") != {"enabled": False}:
        errors.append("Renovate lock maintenance must stay disabled")
```

Run `grep -n "dependabot" scripts/check-dependency-automation.py`. Expected: only the two lines of the absence check remain.

- [ ] **Step 5: Rewrite `renovate.json`**

```bash
python3 - <<'PY'
import importlib.util, json, pathlib
spec = importlib.util.spec_from_file_location("policy", "scripts/check-dependency-automation.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
path = pathlib.Path(".github/renovate.json")
config = json.loads(path.read_text(encoding="utf-8"))
config["enabledManagers"] = ["gradle", "gradle-wrapper", "nuget", "custom.regex", "github-actions"]
config["schedule"] = ["after 6:00am and before 10:00am"]
config["lockFileMaintenance"] = {"enabled": False}
config["platformAutomerge"] = True
config["automergeStrategy"] = "squash"
config["internalChecksFilter"] = "none"
config["rebaseWhen"] = "behind-base-branch"
config["postUpgradeTasks"] = policy.EXPECTED_POST_UPGRADE_TASKS
config["packageRules"] = policy.EXPECTED_PACKAGE_RULES
config["customManagers"].append({
    "customType": "regex",
    "managerFilePatterns": ["/^\\.github/workflows/renovate\\.yml$/"],
    "matchStrings": ["renovate-version:\\s*\"?(?<currentValue>[0-9.]+)@(?<currentDigest>sha256:[0-9a-f]{64})\"?"],
    "depNameTemplate": "renovate/renovate",
    "datasourceTemplate": "docker",
    "versioningTemplate": "docker",
})
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
git rm .github/dependabot.yml
```

- [ ] **Step 6: Rewrite `DEPENDENCY-POLICY.md`**

Replace the first two sections, from `# Dependency update policy` up to (not including) `## The JDK pin`, with:

```markdown
# Dependency update policy

Renovate owns Gradle dependencies and plugins, the Gradle wrapper, JetBrains IDE and SDK versions,
RDGen, Rider NuGet packages, lock files, and Gradle verification metadata. Renovate also owns GitHub
Actions, the JDK build recorded in `.java-version`, and its own image in
`.github/workflows/renovate.yml`. Dependabot keeps GitHub-native security alerts; it opens no version
update. Renovate runs self-hosted from `main` in `.github/workflows/renovate.yml`, never on a pull
request, with a GitHub App token held by the `renovate` environment.

## Update rules

Renovate checks every day between 06:00 and 10:00 in `Europe/Paris`. Ordinary minor and patch updates
may be grouped within their manager. Major updates remain separate. The Rider IDE and the Rider and
ReSharper SDKs move together in one pull request, because the IDE and the SDK must match.

Only stable releases are eligible, and stable releases are eligible immediately: Renovate opens their
pull request at once. Minor, patch and digest updates merge on their own once seven days old, the
same window after which the freshness audit calls a pin behind, and only when `CI / Gate` is green.
The seven days are the freshness grace in `scripts/check-supply-chain.py`, read by the policy check
rather than repeated. Major updates and the Rider group are always merged by a maintainer.
Prereleases such as alpha, beta, RC, EAP, preview, nightly, and snapshot builds are rejected unless a
separate compatibility decision changes the declared product matrix.

JetBrains IDE and SDK updates stay within the declared 2025.3 or 2026.2 compatibility line. The
Rider test collector stays below Coverlet 7 because the project still uses JetBrains' `net472` test
host; newer stable collectors target modern .NET only and cannot run there.

Lock file maintenance is disabled. It regenerates every Gradle lock at once, which drops the bundled
module entries of products it did not resolve and can lock a prerelease IDE.

## Review

Before pushing a branch, Renovate runs `python3 scripts/sync-supply-chain.py --online`, the only
command its global configuration allows. The script rewrites the supply-chain inventory and, for a
JetBrains product only the plugin verifier uses, the lock line and the verification metadata with
the checksums JetBrains publishes. The pins mirrored in `scripts/tests` stay a deliberate second
edit: a pull request that moves one fails `Workflow security` until a maintainer edits the named
line. A product a test IDE resolves, such as Rider or RustRover, still needs a Gradle relock by hand.

Renovate's custom JetBrains manager reads the official JetBrains product release service for every
IDE version embedded in the Gradle build. Its NuGet manager covers SDK-style project files and
`packages.lock.json`; its Gradle managers cover `settings.gradle.kts`, `gradle/libs.versions.toml`,
RDGen, plugins, `gradle/wrapper/gradle-wrapper.properties`, Gradle locks, and verification metadata.
```

In the section `## Bringing the inventory back in step`, replace exactly

```markdown
Neither bot writes `config/supply-chain.json`, so a pull request that bumps a manifest fails
`check-supply-chain.py` until the matching entry is rewritten. `make sync-supply-chain` performs
that rewrite:
```

with

```markdown
Renovate runs `sync-supply-chain` on its own branches. A manifest bumped by hand fails
`check-supply-chain.py` until the matching entry is rewritten, and `make sync-supply-chain` performs
that rewrite:
```

and replace exactly

```markdown
`sync-supply-chain` owns the inventory only, so those two stay
manual,
```

with

```markdown
`sync-supply-chain` writes the inventory and a verifier-only JetBrains product's lock and
verification entries, not these, so those two stay manual,
```

- [ ] **Step 7: Validate the Renovate configuration with the pinned image**

Run:

```bash
IMAGE=renovate/renovate:44.74.1@sha256:bacd588fd6bdc64c10167a81f17699bf841460a48daa57e4a9a0f8911cf45f9b
docker run --rm -v "$PWD":/work -w /work "$IMAGE" renovate-config-validator --strict .github/renovate.json
docker run --rm -v "$PWD":/work -w /work -e RENOVATE_CONFIG_FILE=/work/.github/renovate-global.json "$IMAGE" renovate-config-validator --strict
```

Expected: both print `Config validated successfully`. A reported invalid or deprecated option is fixed in both `renovate.json` and the matching expected constant before continuing.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python3 -B -m unittest discover -s scripts/tests -p 'test_*.py' && python3 scripts/check-dependency-automation.py && python3 scripts/check-supply-chain.py && uvx ruff check scripts tools`
Expected: all OK; `dependency automation: OK`.

- [ ] **Step 9: Commit**

```bash
git add scripts/check-dependency-automation.py scripts/tests/test_dependency_automation.py .github/renovate.json DEPENDENCY-POLICY.md .github/dependabot.yml
git commit -m "build(deps): let Renovate own every update and merge matured non-major ones"
```

---

### Task 5: Push and review one dry run

**Files:** none changed unless a verification fails.

- [ ] **Step 1: Push and wait for CI**

Run: `git push origin main`, then watch the push's `CI` and `Security Audit` runs.
Expected: both green.

- [ ] **Step 2: Dispatch Renovate**

Run: `gh workflow run renovate.yml --ref main && sleep 10 && id=$(gh run list --workflow=renovate.yml --limit 1 --json databaseId --jq '.[0].databaseId') && gh run watch "$id" && gh run view "$id" --log > renovate-dry-run.log`.
Expected: the job succeeds; the log shows `DRY-RUN` lines.

- [ ] **Step 3: Resolve the spec's verifications from the log**

| Look for | Decision |
|---|---|
| The token step succeeded and Renovate authenticated as `perf-sentinel-renovate[bot]` | if not, stop and report: the App or its secrets are wrong |
| `Would create branch` / `Would create PR` lines for the pins of issue #13 | if absent, stop and report |
| The hook: `python3 scripts/sync-supply-chain.py --online` executed, Python installed, no `command not allowed` | if Python is missing, add `"installTools": {"python": {}}` to the hook and to `EXPECTED_POST_UPGRADE_TASKS`, then repeat Task 4 steps 7–9 and this task |
| The hook's GitHub lookups succeeded (no `403` / rate limit) | if not, stop and report: `RENOVATE_CUSTOM_ENV_VARIABLES` did not reach the hook |
| For a Gradle library update, lock or verification-metadata artifact errors | none: keep; errors: those updates stay manual, report the gap |
| Automerge with `platformAutomerge` waiting for the release age (Renovate docs for the pinned version) | confirmed: keep; not confirmed: set `platformAutomerge: false` in `renovate.json` and in the checker, repeat Task 4 steps 7–9 |
| The grouped `rider-ide-and-sdk` update recognised | if split, report |

- [ ] **Step 4: Report the dry run to the maintainer and wait for approval to lift it.**

---

### Task 6: Lift the dry run

**Files:**
- Modify: `.github/renovate-global.json`, `scripts/check-dependency-automation.py`, `scripts/tests/test_security_workflows.py`

- [ ] **Step 1: Write the failing test change**

In `RenovateWorkflowTests.test_global_config_allows_only_the_sync_hook_and_starts_in_dry_run`, rename it to `test_global_config_allows_only_the_sync_hook_and_writes` and replace `self.assertEqual("full", self.global_config["dryRun"])` with `self.assertNotIn("dryRun", self.global_config)`.

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest scripts.tests.test_security_workflows.RenovateWorkflowTests -v`
Expected: FAIL, `'dryRun' unexpectedly found`.

- [ ] **Step 3: Remove the dry run**

In `.github/renovate-global.json`, delete the `"dryRun": "full"` entry and the comma before it. In `scripts/check-dependency-automation.py`, delete `"dryRun": "full",` from `EXPECTED_GLOBAL_CONFIG`.

- [ ] **Step 4: Run everything**

Run: `python3 -B -m unittest discover -s scripts/tests -p 'test_*.py' && python3 scripts/check-dependency-automation.py`
Expected: OK.

- [ ] **Step 5: Commit and push**

```bash
git add .github/renovate-global.json scripts/check-dependency-automation.py scripts/tests/test_security_workflows.py
git commit -m "ci(renovate): let Renovate open pull requests"
git push origin main
```

- [ ] **Step 6: Dispatch once and watch the first pull requests**

Run: `gh workflow run renovate.yml --ref main`.
Expected: pull requests open for the pins listed in issue #13, CI runs on them, and none is merged before its release is seven days old.

---

### Task 7: Retire Dependabot's leftover pull request

- [ ] **Step 1:** Once Renovate's `ordinary-github-actions` pull request exists, run `gh pr close 12 --comment "Superseded by Renovate, which now owns GitHub Actions."`
