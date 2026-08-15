import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]


class VerificationCommandTests(unittest.TestCase):
    def test_verification_metadata_covers_every_hosted_ide_installer(self):
        namespace = {"v": "https://schema.gradle.org/dependency-verification"}
        root = ElementTree.parse(REPOSITORY / "gradle" / "verification-metadata.xml").getroot()
        hosted_groups = {"go", "idea", "python", "ruby", "rustrover", "webide", "webstorm"}
        actual = set()
        for component in root.findall(".//v:component", namespace):
            if component.get("group") not in hosted_groups:
                continue
            for artifact in component.findall("v:artifact", namespace):
                artifact_name = artifact.get("name", "")
                if not artifact_name.endswith((".tar.gz", "-win.zip")):
                    continue
                self.assertEqual(1, len(artifact.findall("v:sha256", namespace)))
                actual.add(
                    (
                        component.get("group"),
                        component.get("name"),
                        component.get("version"),
                        artifact_name,
                    )
                )
        expected = {
            ("idea", "idea", "2025.3.6.1", "idea-2025.3.6.1.tar.gz"),
            ("idea", "idea", "2025.3.6.1", "idea-2025.3.6.1-win.zip"),
            ("idea", "idea", "2026.2.1", "idea-2026.2.1.tar.gz"),
            ("python", "pycharm-professional", "2025.3.6.1", "pycharm-professional-2025.3.6.1.tar.gz"),
            ("python", "pycharm-professional", "2026.2.0.1", "pycharm-professional-2026.2.0.1.tar.gz"),
            ("webide", "PhpStorm", "2025.3.6.1", "PhpStorm-2025.3.6.1.tar.gz"),
            ("webide", "PhpStorm", "2026.2.1", "PhpStorm-2026.2.1.tar.gz"),
            ("rustrover", "RustRover", "2025.3.7", "RustRover-2025.3.7.tar.gz"),
            ("rustrover", "RustRover", "2026.2.1", "RustRover-2026.2.1.tar.gz"),
            ("ruby", "RubyMine", "2025.3.6.1", "RubyMine-2025.3.6.1.tar.gz"),
            ("ruby", "RubyMine", "2026.2.1", "RubyMine-2026.2.1.tar.gz"),
            ("webstorm", "WebStorm", "2025.3.6.1", "WebStorm-2025.3.6.1.tar.gz"),
            ("webstorm", "WebStorm", "2026.2.1", "WebStorm-2026.2.1.tar.gz"),
            ("go", "goland", "2025.3.5.1", "goland-2025.3.5.1.tar.gz"),
            ("go", "goland", "2026.2.1", "goland-2026.2.1.tar.gz"),
        }
        self.assertEqual(expected, actual)

    def test_hosted_workflows_use_the_action_managed_gradle_distribution(self):
        for workflow in sorted((REPOSITORY / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            setup_count = text.count("uses: gradle/actions/setup-gradle@")
            if setup_count == 0:
                continue
            with self.subTest(workflow=workflow.name):
                self.assertEqual(setup_count, text.count('gradle-version: "9.7.0"'))
                self.assertNotIn("./gradlew", text)
                self.assertNotIn("gradlew.bat", text)

    def test_windows_runtime_is_verified_for_hosted_rider_builds(self):
        metadata = (REPOSITORY / "gradle" / "verification-metadata.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'version="jbr_jcef-21.0.11-windows-x64-b1163.116"',
            metadata,
        )
        self.assertIn(
            'value="40a305663ded81fd49f11bb314253026df6a54b18e919bea2fc184c8f72ef23b"',
            metadata,
        )

    def test_heavy_linux_jobs_free_unused_hosted_toolchains_before_gradle(self):
        workflow = (REPOSITORY / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        script = REPOSITORY / "scripts" / "free-hosted-runner-space.sh"
        self.assertTrue(script.is_file())
        self.assertIn("/usr/local/lib/android", script.read_text(encoding="utf-8"))
        for start, end in (
            ("  jvm:\n", "\n  python:\n"),
            ("  python:\n", "\n  php:\n"),
            ("  php:\n", "\n  rust:\n"),
            ("  rust:\n", "\n  ruby:\n"),
            ("  ruby:\n", "\n  javascript:\n"),
            ("  javascript:\n", "\n  go:\n"),
            ("  go:\n", "\n  rider-frontend:\n"),
            ("  rider-frontend:\n", "\n  plugin-verifier:\n"),
            ("  plugin-verifier:\n", "\n  zip:\n"),
            ("  zip:\n", "\n  dependency-review:\n"),
        ):
            with self.subTest(job=start.strip()):
                job = workflow.split(start, 1)[1].split(end, 1)[0]
                cleanup = job.index("scripts/free-hosted-runner-space.sh")
                gradle = job.index("uses: gradle/actions/setup-gradle@")
                self.assertLess(cleanup, gradle)

        codeql = (REPOSITORY / ".github" / "workflows" / "codeql.yml").read_text(
            encoding="utf-8"
        )
        java_job = codeql.split("  java-kotlin:\n", 1)[1].split("\n  csharp:\n", 1)[0]
        self.assertLess(
            java_job.index("scripts/free-hosted-runner-space.sh"),
            java_job.index("uses: gradle/actions/setup-gradle@"),
        )

    @staticmethod
    def dry_run(target, **variables):
        arguments = ["make", "--no-print-directory", "-n", target]
        arguments.extend(f"{key}={value}" for key, value in variables.items())
        return subprocess.run(
            arguments, cwd=REPOSITORY, text=True, capture_output=True, check=False
        )

    def test_verify_fast_wires_strict_cross_language_gates_on_windows(self):
        result = self.dry_run("verify-fast", OS="Windows_NT")
        self.assertEqual(0, result.returncode, result.stderr)
        output = result.stdout
        for expected in (
            "--dependency-verification strict",
            "compileKotlin",
            "testPyCharm253",
            "testPhpStorm253",
            "testRustRover253",
            "testRustRover262",
            "testRubyMine253",
            "testWebStorm253",
            "testGoLand253",
            "koverXmlReport",
            "python3 -B -m unittest discover",
            "dotnet restore",
            "--locked-mode",
            "dotnet test",
            "--configuration Release",
            'XPlat Code Coverage',
            "--logger trx",
            "inspect-plugin-zip.py",
        ):
            self.assertIn(expected, output)
        self.assertNotIn("/usr/local/share/dotnet", output)

    def test_verify_fast_fails_closed_for_rider_outside_windows(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "verify-fast", "OS=", "GRADLE=false"],
            cwd=REPOSITORY,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Rider verification requires Windows", result.stderr)
        self.assertNotIn("No rule", result.stderr)
        self.assertNotIn("unittest discover", result.stdout)

    def test_disk_guard_runs_before_heavy_verification(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "check-disk"],
            cwd=REPOSITORY,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Disk guard:", result.stdout)

    def test_disk_guard_does_not_reject_managed_ci_runners(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "check-disk"],
            cwd=REPOSITORY,
            env={**os.environ, "CI": "true"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("managed CI runner", result.stdout)

    def test_verify_adds_plugin_qodana_lock_and_packaging_gates(self):
        result = self.dry_run("verify", OS="Windows_NT")
        self.assertEqual(0, result.returncode, result.stderr)
        for expected in (
            "verifyPlugin",
            "verifyPluginProjectConfiguration",
            "qodanaScan",
            "qodana scan --config qodana-dotnet.yml",
            "--results-dir",
            "check-supply-chain.py",
            "check-analysis-config.py",
            "git diff HEAD --exit-code",
            "buildPlugin",
        ):
            self.assertIn(expected, result.stdout)
        first_lock_check = result.stdout.index("git diff HEAD --exit-code")
        first_heavy_gate = result.stdout.index("unittest discover")
        self.assertLess(first_lock_check, first_heavy_gate)
        self.assertEqual(2, result.stdout.count("git diff HEAD --exit-code"))

    def test_security_wires_standard_dependency_workflow_and_secret_scanners(self):
        result = self.dry_run("security")
        self.assertEqual(0, result.returncode, result.stderr)
        for expected in (
            "osv-scanner scan source --recursive --licenses='Apache-2.0,Apache-2.0 WITH LLVM-exception,BSD-2-Clause,BSD-3-Clause,CDDL-1.1,EPL-1.0,EPL-2.0,ISC,MIT,MPL-2.0,Unicode-3.0,Zlib' .",
            "dotnet restore",
            "NuGetAuditMode=all",
            "TreatWarningsAsErrors=true",
            "gitleaks git",
            "actionlint",
            "zizmor --offline --strict-collection --collect=workflows",
            "check-supply-chain.py --online",
            "--configuration runtimeClasspath",
            "check-analysis-config.py",
        ):
            self.assertIn(expected, result.stdout)

    def test_release_check_is_versioned_and_has_no_remote_mutation(self):
        result = self.dry_run("release-check", VERSION="0.1.0", OS="Windows_NT")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("release_version='0.1.0'", result.stdout)
        self.assertIn("-Pversion=$release_version", result.stdout)
        self.assertIn("perf-sentinel-$release_version.zip", result.stdout)
        self.assertIn("inspect-plugin-zip.py", result.stdout)
        self.assertNotIn("git push", result.stdout)
        self.assertNotIn("git tag", result.stdout)
        self.assertNotIn("publishPlugin", result.stdout)

    def test_commands_do_not_duplicate_dependency_caches(self):
        environment = os.environ.copy()
        for name in (
            "DOTNET_CLI_HOME",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "ZIZMOR_CACHE_DIR",
            "GRADLE_USER_HOME",
            "NUGET_PACKAGES",
            "NUGET_HTTP_CACHE_PATH",
            "NUGET_PLUGINS_CACHE_PATH",
        ):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            makefile = Path(directory) / "Makefile"
            makefile.write_text(
                f"include {REPOSITORY / 'Makefile'}\nprint-environment:\n\t@env\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["make", "--no-print-directory", "-f", str(makefile), "print-environment"],
                cwd=REPOSITORY,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("DOTNET_CLI_HOME=", result.stdout)
        self.assertNotIn("XDG_CACHE_HOME=", result.stdout)
        self.assertNotIn("XDG_CONFIG_HOME=", result.stdout)
        self.assertNotIn("ZIZMOR_CACHE_DIR=", result.stdout)
        self.assertNotIn("GRADLE_USER_HOME=", result.stdout)
        self.assertNotIn("NUGET_PACKAGES=", result.stdout)
        self.assertNotIn("NUGET_HTTP_CACHE_PATH=", result.stdout)
        self.assertNotIn("NUGET_PLUGINS_CACHE_PATH=", result.stdout)

    def test_rider_build_uses_dotnet_from_path(self):
        build = (REPOSITORY / "build.gradle.kts").read_text(encoding="utf-8")
        self.assertNotIn("/usr/local/share/dotnet/dotnet", build)
        self.assertIn('val dotnetExecutable = "dotnet"', build)

    def test_security_pins_the_patched_bcl_memory_version(self):
        props = (REPOSITORY / "src/dotnet/Directory.Build.props").read_text(encoding="utf-8")
        inventory = (REPOSITORY / "config/supply-chain.json").read_text(encoding="utf-8")
        self.assertIn(
            '<PackageReference Include="Microsoft.Bcl.Memory" Version="9.0.19" NoWarn="NU1608"',
            props,
        )
        self.assertIn('"name": "Microsoft.Bcl.Memory"', inventory)
        self.assertIn('"version": "9.0.19"', inventory)
        self.assertIn('"release": "9.0"', inventory)
        self.assertIn("<NoWarn>MSB3277;NU1603</NoWarn>", props)

    def test_release_check_rejects_missing_or_non_semver_version_before_gates(self):
        for version in (None, "0.1", "0.1.0-SNAPSHOT"):
            with self.subTest(version=version):
                arguments = ["make", "--no-print-directory", "release-check"]
                if version is not None:
                    arguments.append(f"VERSION={version}")
                result = subprocess.run(
                    arguments, cwd=REPOSITORY, text=True, capture_output=True, check=False
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("VERSION must be an exact stable semantic version", result.stderr)
                self.assertNotIn("BUILD SUCCESSFUL", result.stdout)
                self.assertNotIn("Rider verification requires Windows", result.stderr)
                self.assertNotIn("osv-scanner", result.stderr)

    def test_release_version_cannot_inject_a_shell_command(self):
        for payload in ("$$(touch {marker})", "$(shell touch {marker})"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "injected"
                result = subprocess.run(
                    [
                        "make",
                        "--no-print-directory",
                        "release-check",
                        "VERSION=" + payload.format(marker=marker),
                    ],
                    cwd=REPOSITORY,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(marker.exists(), "VERSION executed a Make or shell command before validation")

    def test_lock_drift_detects_staged_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Makefile").write_bytes((REPOSITORY / "Makefile").read_bytes())
            lock = root / "gradle.lockfile"
            lock.write_text("before\n", encoding="utf-8")
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.email", "fixture@example.test"],
                ["git", "config", "user.name", "Fixture"],
                ["git", "add", "Makefile", "gradle.lockfile"],
                ["git", "commit", "-qm", "fixture"],
            ):
                subprocess.run(command, cwd=root, check=True)
            lock.write_text("after\n", encoding="utf-8")
            subprocess.run(["git", "add", "gradle.lockfile"], cwd=root, check=True)
            result = subprocess.run(
                ["make", "--no-print-directory", "check-locks"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("No rule", result.stderr)
        self.assertIn("gradle.lockfile", result.stdout)


if __name__ == "__main__":
    unittest.main()
