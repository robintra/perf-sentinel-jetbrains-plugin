import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-badges.py"
REPO_URL = "https://github.com/robintra/perf-sentinel-jetbrains-plugin"
CANONICAL_LICENSE = (REPOSITORY / "LICENSE").read_bytes()
BADGES = {
    "JetBrains IDEs": (
        "https://img.shields.io/badge/JetBrains%20IDEs-2025.3%20%7C%202026.2-087CFA?logo=jetbrains&logoColor=white",
        f"{REPO_URL}/blob/main/build.gradle.kts",
    ),
    "CI": (
        f"{REPO_URL}/actions/workflows/ci.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/ci.yml",
    ),
    "Security Audit": (
        f"{REPO_URL}/actions/workflows/security-audit.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/security-audit.yml",
    ),
    "CodeQL": (
        f"{REPO_URL}/actions/workflows/codeql.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/codeql.yml",
    ),
    "Qodana": (
        "https://img.shields.io/badge/Qodana-JVM%20%7C%20Rider-000000?logo=qodana&logoColor=white",
        f"{REPO_URL}/actions/workflows/security-audit.yml",
    ),
    "Release": (
        f"{REPO_URL}/actions/workflows/release.yml/badge.svg",
        f"{REPO_URL}/actions/workflows/release.yml",
    ),
    "Signed ZIP": (
        "https://img.shields.io/badge/JetBrains%20ZIP-signature%20configured-lightgrey?logo=jetbrains&logoColor=white",
        f"{REPO_URL}/actions/workflows/release.yml",
    ),
}


def badge(label, image, destination):
    return f'    <a href="{destination}"><img src="{image}" alt="{label}" /></a>'


def marketplace_badges(listing_id):
    destination = f"https://plugins.jetbrains.com/plugin/{listing_id}"
    return {
        "Marketplace version": (
            f"https://img.shields.io/jetbrains/plugin/v/{listing_id}", destination
        ),
        "Marketplace downloads": (
            f"https://img.shields.io/jetbrains/plugin/d/{listing_id}", destination
        ),
    }


def complete_readme(listing_id=None):
    badges = BADGES | (marketplace_badges(listing_id) if listing_id else {})
    lines = ["# Perf Sentinel for JetBrains IDEs", "", '<p align="center">']
    lines.extend(badge(label, *values) for label, values in badges.items())
    lines.append("</p>")
    return "\n".join(lines) + "\n\n"


def write_root(root, readme, *, listing_id=None, missing_evidence=None, license_bytes=CANONICAL_LICENSE):
    (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "LICENSE").write_bytes(license_bytes)
    (root / "qodana.yml").write_text("version: 1.0\n", encoding="utf-8")
    if missing_evidence != "build.gradle.kts":
        (root / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    config = root / "config"
    config.mkdir()
    (config / "release-metadata.json").write_text(
        json.dumps({"schema_version": 1, "marketplace_listing_id": listing_id}),
        encoding="utf-8",
    )
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name in ("ci.yml", "codeql.yml", "security-audit.yml", "release.yml"):
        if name != missing_evidence:
            (workflows / name).write_text(f"name: {name}\n", encoding="utf-8")


def run_checker(readme, **options):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_root(root, readme, **options)
        return subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(root)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )


class BadgeTests(unittest.TestCase):
    def test_accepts_the_canonical_pre_marketplace_badges(self):
        result = run_checker(complete_readme())

        self.assertEqual(0, result.returncode, result.stderr)

    def test_requires_each_evidence_badge_and_exact_destination(self):
        for label, values in BADGES.items():
            with self.subTest(label=label):
                canonical = badge(label, *values)
                missing = complete_readme().replace(canonical + "\n", "")
                decorative = complete_readme().replace(
                    canonical, badge(label, values[0], REPO_URL)
                )
                for readme in (missing, decorative):
                    result = run_checker(readme)
                    self.assertEqual(1, result.returncode)
                    self.assertIn("canonical top badge block", result.stderr)

    def test_rejects_extra_decorative_or_prerelease_badges(self):
        additions = (
            "![Decorative](https://example.test/status.svg)\n",
            f"[![Beta](https://img.shields.io/badge/channel-beta-orange)]({REPO_URL}/releases)\n",
            f"[![EAP](https://img.shields.io/badge/channel-EAP-orange)]({REPO_URL}/releases)\n",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                result = run_checker(complete_readme()[:-1] + addition + "\n")
                self.assertEqual(1, result.returncode)
                self.assertIn("canonical top badge block", result.stderr)

    def test_rejects_any_badge_outside_the_canonical_block(self):
        listing_id = 12345
        cases = (
            (
                complete_readme() + badge(
                    "Decorative",
                    "https://img.shields.io/badge/build-pretty-green",
                    REPO_URL,
                ) + "\n",
                None,
            ),
            (
                complete_readme() + badge(
                    "Marketplace",
                    "https://img.shields.io/badge/Marketplace-configured-lightgrey",
                    "https://plugins.jetbrains.com",
                ) + "\n",
                None,
            ),
            (
                complete_readme(listing_id)
                + badge("Marketplace version", *marketplace_badges(listing_id)["Marketplace version"])
                + "\n",
                listing_id,
            ),
        )
        for readme, metadata_id in cases:
            with self.subTest(metadata_id=metadata_id):
                result = run_checker(readme, listing_id=metadata_id)
                self.assertEqual(1, result.returncode)
                self.assertIn("badge outside", result.stderr)

    def test_rejects_remote_reference_and_html_badges_after_the_block(self):
        additions = (
            "[![Remote](HTTPS://EXAMPLE.TEST/BADGE.SVG)](HTTPS://EXAMPLE.TEST)\n",
            "[![Reference][badge]][evidence]\n[badge]: HTTPS://EXAMPLE.TEST/BADGE.SVG\n[evidence]: HTTPS://EXAMPLE.TEST\n",
            '<a href="https://example.test"><img src="https://example.test/badge.svg"></a>\n',
            "[ ![Marketplace](https://example.test/badge.svg)](https://example.test)\n",
            '<a\thref="https://example.test"><img\tsrc="https://example.test/badge.svg"></a>\n',
            '<a\nhref="https://example.test"><img\nsrc="https://example.test/badge.svg"></a>\n',
        )
        for addition in additions:
            with self.subTest(addition=addition):
                result = run_checker(complete_readme() + addition)
                self.assertEqual(1, result.returncode)
                self.assertIn("badge outside", result.stderr)

        local_image = run_checker(
            complete_readme() + "## Architecture\n\n![Architecture](docs/architecture.png)\n"
        )
        self.assertEqual(0, local_image.returncode, local_image.stderr)

    def test_requires_committed_local_evidence(self):
        result = run_checker(complete_readme(), missing_evidence="security-audit.yml")

        self.assertEqual(1, result.returncode)
        self.assertIn("missing local evidence", result.stderr)

    def test_forbids_marketplace_badges_until_the_real_id_exists(self):
        variants = (
            complete_readme(12345),
            complete_readme() + "![Marketplace](https://img.shields.io/jetbrains/plugin/v/12345)\n",
        )
        for readme in variants:
            with self.subTest(readme=readme[-80:]):
                result = run_checker(readme)
                self.assertEqual(1, result.returncode)
                self.assertIn("Marketplace badges require", result.stderr)

    def test_requires_both_marketplace_badges_with_the_recorded_id(self):
        listing_id = 12345
        canonical = complete_readme(listing_id)
        result = run_checker(canonical, listing_id=listing_id)
        self.assertEqual(0, result.returncode, result.stderr)

        for label, values in marketplace_badges(listing_id).items():
            with self.subTest(label=label):
                missing = canonical.replace(badge(label, *values) + "\n", "")
                wrong = canonical.replace(f"/{listing_id}", "/99999")
                for readme in (missing, wrong):
                    result = run_checker(readme, listing_id=listing_id)
                    self.assertEqual(1, result.returncode)

    def test_rejects_open_or_malformed_release_metadata(self):
        mutations = (
            {"schema_version": True, "marketplace_listing_id": None},
            {"schema_version": 1, "marketplace_listing_id": 0},
            {"schema_version": 1, "marketplace_listing_id": "12345"},
            {"schema_version": 1, "marketplace_listing_id": None, "extra": True},
        )
        for metadata in mutations:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_root(root, complete_readme())
                (root / "config" / "release-metadata.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--root", str(root)],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(1, result.returncode)
                self.assertIn("release metadata", result.stderr)

    def test_readme_documents_the_real_public_contract(self):
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        for fact in (
            "pre-1.0",
            "2025.3",
            "2026.2",
            "optional",
            "Rider",
            "Marketplace adds its own signature",
            "full ZIP hashes differ",
            "plugin entries remain identical",
            "make verify-fast",
            "make security",
            "make release-check VERSION=0.1.0",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, readme)

    def test_binds_the_canonical_license(self):
        result = run_checker(complete_readme(), license_bytes=b"MIT License\n")
        self.assertEqual(1, result.returncode)
        self.assertIn("canonical AGPL", result.stderr)

    def test_make_exposes_and_uses_the_badge_check(self):
        makefile = (REPOSITORY / "Makefile").read_text(encoding="utf-8")
        self.assertIn("badge-check:", makefile)
        self.assertIn("scripts/check-badges.py", makefile)
        self.assertRegex(makefile, r"verify-fast:[^\n]*badge-check")


if __name__ == "__main__":
    unittest.main()
