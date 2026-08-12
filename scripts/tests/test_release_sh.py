import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
RELEASE = REPOSITORY / "scripts" / "release.sh"
CHECKER = REPOSITORY / "scripts" / "check-version.py"


class ReleaseScriptTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RELEASE.is_file(), "scripts/release.sh is missing")
        self.assertTrue(CHECKER.is_file(), "scripts/check-version.py is missing")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.remote = self.root / "origin.git"
        self.signing_key = self.root / "release-key"
        self.allowed_signers = self.root / "allowed_signers"
        self.environment = os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        self.create_repository()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def command(self, *arguments, cwd=None, input_text=None):
        return subprocess.run(arguments, cwd=cwd or self.repository, env=self.environment,
                              input=input_text, text=True, capture_output=True, check=False)

    def git(self, *arguments, cwd=None):
        result = self.command("git", *arguments, cwd=cwd)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def run_release(self, *arguments, input_text=None):
        return self.command(str(self.repository / "scripts/release.sh"), *arguments, input_text=input_text)

    def create_repository(self):
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(RELEASE, self.repository / "scripts/release.sh")
        shutil.copy2(CHECKER, self.repository / "scripts/check-version.py")
        (self.repository / "src/main/resources/META-INF").mkdir(parents=True)
        (self.repository / "gradle.properties").write_text(
            "group=io.github.robintra\nversion=0.1.0\nmarketplaceChannel=default\n", encoding="utf-8")
        (self.repository / "src/main/resources/META-INF/plugin.xml").write_text(
            "<idea-plugin><id>io.github.robintra.perfsentinel</id><version>0.1.0</version></idea-plugin>\n", encoding="utf-8")
        (self.repository / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.1.0] - 2026-08-12\n", encoding="utf-8")
        (self.repository / "build.gradle.kts").write_text(
            'tasks.named<BuildPluginTask>("buildPlugin") { archiveBaseName.set("perf-sentinel") }\n'
            'tasks.named<PublishPluginTask>("publishPlugin") { channels.set(providers.gradleProperty("marketplaceChannel").map { listOf(it) }) }\n',
            encoding="utf-8")
        (self.repository / "Makefile").write_text(
            "release-check:\n\tpython3 scripts/check-version.py v$(VERSION)\n", encoding="utf-8")
        (self.repository / "README.md").write_text("fixture\n", encoding="utf-8")
        self.assertEqual(0, self.command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.signing_key), cwd=self.root).returncode)
        public_key = self.signing_key.with_suffix(".pub").read_text(encoding="ascii").strip()
        self.allowed_signers.write_text(f"release@test {public_key}\n", encoding="ascii")
        self.assertEqual(0, self.command("git", "init", "-q", "-b", "main", str(self.repository), cwd=self.root).returncode)
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@test")
        self.git("config", "gpg.format", "ssh")
        self.git("config", "user.signingkey", str(self.signing_key))
        self.git("config", "gpg.ssh.allowedSignersFile", str(self.allowed_signers))
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.assertEqual(0, self.command("git", "init", "-q", "--bare", "--initial-branch=main", str(self.remote), cwd=self.root).returncode)
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")

    def snapshot(self):
        files = {str(path.relative_to(self.repository)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.repository.rglob("*") if path.is_file() and ".git" not in path.parts}
        refs = self.git("for-each-ref", "--format=%(refname) %(objectname)")
        remote_refs = self.git("-C", str(self.remote), "for-each-ref", "--format=%(refname) %(objectname)")
        return files, self.git("status", "--porcelain=v1", "--untracked-files=all"), refs, remote_refs

    def test_dry_run_is_side_effect_free(self):
        before = self.snapshot()
        result = self.run_release("v0.1.0", "--dry-run")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no repository or remote mutation", result.stdout)
        self.assertEqual(before, self.snapshot())

    def test_rejects_prerelease_dirty_and_wrong_branch(self):
        self.assertIn("stable tag", self.run_release("v0.1.0-beta.1", "--dry-run").stderr)
        (self.repository / "README.md").write_text("dirty\n", encoding="utf-8")
        self.assertIn("working tree", self.run_release("v0.1.0", "--dry-run").stderr)
        self.git("restore", "README.md")
        self.git("switch", "-c", "release-candidate")
        self.assertIn("main", self.run_release("v0.1.0", "--dry-run").stderr)

    def test_rejects_local_main_ahead_of_origin(self):
        (self.repository / "ahead.txt").write_text("ahead\n", encoding="utf-8")
        self.git("add", "ahead.txt")
        self.git("commit", "-m", "ahead")
        self.assertIn("synchronized", self.run_release("v0.1.0", "--dry-run").stderr)

    def test_rejects_local_main_behind_origin(self):
        other = self.root / "other"
        self.assertEqual(0, self.command("git", "clone", "-q", str(self.remote), str(other), cwd=self.root).returncode)
        self.git("config", "user.name", "Other", cwd=other)
        self.git("config", "user.email", "other@example.invalid", cwd=other)
        (other / "remote.txt").write_text("remote\n", encoding="utf-8")
        self.git("add", "remote.txt", cwd=other)
        self.git("commit", "-m", "remote ahead", cwd=other)
        self.git("push", "origin", "main", cwd=other)
        self.assertIn("synchronized", self.run_release("v0.1.0", "--dry-run").stderr)

    def test_rejects_existing_local_and_remote_tags(self):
        self.git("reset", "--hard", "origin/main")
        self.git("tag", "v0.1.0")
        self.assertIn("already exists locally", self.run_release("v0.1.0", "--dry-run").stderr)
        self.git("push", "origin", "v0.1.0")
        self.git("tag", "-d", "v0.1.0")
        self.assertIn("already exists on origin", self.run_release("v0.1.0", "--dry-run").stderr)

    def test_rejects_missing_signer_and_wrong_confirmation(self):
        self.git("config", "--unset", "user.signingkey")
        self.assertIn("signing identity", self.run_release("v0.1.0", "--dry-run").stderr)
        self.git("config", "user.signingkey", str(self.signing_key))
        result = self.run_release("v0.1.0", input_text="yes\n")
        self.assertIn("confirmation", result.stderr)
        self.assertNotEqual(0, result.returncode)

    def test_rejects_a_signer_that_cannot_create_a_verified_tag(self):
        self.git("config", "user.signingkey", str(self.root / "missing-key"))
        result = self.run_release("v0.1.0", "--dry-run")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("signing identity", result.stderr)

    def test_creates_and_pushes_exactly_one_verified_signed_tag(self):
        result = self.run_release("v0.1.0", input_text="v0.1.0\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0, self.command("git", "verify-tag", "v0.1.0").returncode)
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-list", "-n", "1", "v0.1.0"))
        self.assertEqual("refs/tags/v0.1.0", self.git("-C", str(self.remote), "for-each-ref", "--format=%(refname)", "refs/tags"))

    def test_does_not_follow_an_unrelated_annotated_tag(self):
        self.git("tag", "-a", "unrelated", "-m", "unrelated")
        self.git("config", "push.followTags", "true")
        result = self.run_release("v0.1.0", input_text="v0.1.0\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "refs/tags/v0.1.0",
            self.git("-C", str(self.remote), "for-each-ref", "--format=%(refname)", "refs/tags"),
        )

    def test_rechecks_origin_after_the_release_gates(self):
        other = self.root / "gate-advancer"
        advance = self.repository / "advance-origin.sh"
        advance.write_text(
            "#!/bin/sh\n"
            "test -f \"$RELEASE_TEST_ADVANCER/advanced\" && exit 0\n"
            "touch \"$RELEASE_TEST_ADVANCER/advanced\"\n"
            "git -C \"$RELEASE_TEST_ADVANCER\" add advanced\n"
            "git -C \"$RELEASE_TEST_ADVANCER\" commit -q -m advanced\n"
            "git -C \"$RELEASE_TEST_ADVANCER\" push -q origin main\n",
            encoding="utf-8",
        )
        advance.chmod(0o755)
        (self.repository / "Makefile").write_text(
            "release-check:\n\t./advance-origin.sh\n\tpython3 scripts/check-version.py v$(VERSION)\n",
            encoding="utf-8",
        )
        self.git("add", "Makefile", "advance-origin.sh")
        self.git("commit", "-m", "advance origin during gates")
        self.git("push", "origin", "main")
        self.assertEqual(0, self.command("git", "clone", "-q", str(self.remote), str(other), cwd=self.root).returncode)
        self.git("config", "user.name", "Gate Advancer", cwd=other)
        self.git("config", "user.email", "gate@example.invalid", cwd=other)
        self.environment["RELEASE_TEST_ADVANCER"] = str(other)

        result = self.run_release("v0.1.0", input_text="v0.1.0\n")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("synchronized", result.stderr)
        self.assertNotEqual(0, self.command("git", "show-ref", "--verify", "--quiet", "refs/tags/v0.1.0").returncode)

    def test_rejects_a_synchronized_commit_created_during_the_release_gates(self):
        advance = self.repository / "advance-both.sh"
        advance.write_text(
            "#!/bin/sh\n"
            "test -f advanced && exit 0\n"
            "touch advanced\n"
            "git add advanced\n"
            "git commit -q -m advanced\n"
            "git push -q origin main\n",
            encoding="utf-8",
        )
        advance.chmod(0o755)
        (self.repository / "Makefile").write_text(
            "release-check:\n\t./advance-both.sh\n\tpython3 scripts/check-version.py v$(VERSION)\n",
            encoding="utf-8",
        )
        self.git("add", "Makefile", "advance-both.sh")
        self.git("commit", "-m", "advance both during gates")
        self.git("push", "origin", "main")

        result = self.run_release("v0.1.0", input_text="v0.1.0\n")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("verified commit changed", result.stderr)
        self.assertNotEqual(0, self.command("git", "show-ref", "--verify", "--quiet", "refs/tags/v0.1.0").returncode)


if __name__ == "__main__":
    unittest.main()
