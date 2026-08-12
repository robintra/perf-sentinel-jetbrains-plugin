import hashlib
import json
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_plugin_signature import signer_jar, write_zip


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "verify-release.py"
RELEASE_WORKFLOW = REPOSITORY / ".github/workflows/release.yml"
VERIFY_WORKFLOW = REPOSITORY / ".github/workflows/release-verification.yml"
ALLOWED_SIGNERS = REPOSITORY / "config/release-allowed-signers"
RUNBOOK = REPOSITORY / "RELEASING.md"


class ReleaseVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.key = cls.root / "key.pem"
        cls.certificate = cls.root / "perf-sentinel-0.1.0-certificate-chain.pem"
        cls.unsigned = cls.root / "perf-sentinel-0.1.0.zip"
        cls.signed = cls.root / "perf-sentinel-0.1.0-signed.zip"
        cls.marketplace_directory = cls.root / "marketplace"
        cls.marketplace_directory.mkdir()
        cls.marketplace = cls.marketplace_directory / "perf-sentinel-0.1.0-signed.zip"
        cls.marketplace_key = cls.root / "marketplace-key.pem"
        cls.marketplace_certificate = cls.root / "marketplace-certificate.pem"
        cls.sbom = cls.root / "perf-sentinel-0.1.0.spdx.json"
        cls.manifest = cls.root / "perf-sentinel-0.1.0-release.json"
        cls.identity = cls.root / "identity.json"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "2", "-subj", "/CN=Perf Sentinel Test Signer",
                "-keyout", str(cls.key), "-out", str(cls.certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "2", "-subj", "/CN=Marketplace Test Signer",
                "-keyout", str(cls.marketplace_key), "-out", str(cls.marketplace_certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        write_zip(cls.unsigned)
        subprocess.run(
            [
                "java", "-jar", str(signer_jar()), "sign",
                "-in", str(cls.unsigned), "-out", str(cls.signed),
                "-cert-file", str(cls.certificate), "-key-file", str(cls.key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "java", "-jar", str(signer_jar()), "sign",
                "-in", str(cls.signed), "-out", str(cls.marketplace),
                "-cert-file", str(cls.marketplace_certificate), "-key-file", str(cls.marketplace_key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        der = ssl.PEM_cert_to_DER_cert(cls.certificate.read_text(encoding="ascii"))
        cls.identity.write_text(json.dumps({
            "schemaVersion": 1,
            "provider": "JetBrains Marketplace ZIP Signer",
            "timestampPolicy": "unsupported-by-format",
            "certificate": {
                "status": "active",
                "commonName": "Perf Sentinel Test Signer",
                "sha256Fingerprint": hashlib.sha256(der).hexdigest(),
            },
        }), encoding="utf-8")
        cls.sbom.write_text(json.dumps({
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "perf-sentinel-0.1.0-signed.zip",
            "packages": [{"SPDXID": "SPDXRef-Package", "name": "perf-sentinel"}],
        }), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.create_manifest()

    def command(self, mode, *, marketplace_distributed=False, **overrides):
        values = {
            "version": "0.1.0",
            "unsigned": self.unsigned,
            "signed": self.signed,
            "github_zip": self.signed,
            "marketplace_zip": self.signed,
            "sbom": self.sbom,
            "certificate": self.certificate,
            "identity": self.identity,
            "signer_jar": signer_jar(),
            "manifest": self.manifest,
        }
        values.update(overrides)
        arguments = [sys.executable, str(CHECKER), mode]
        for key, value in values.items():
            if mode == "create" and key in {"github_zip", "marketplace_zip"}:
                continue
            if mode == "verify" and key == "unsigned":
                continue
            arguments.extend((f"--{key.replace('_', '-')}", str(value)))
        if marketplace_distributed:
            arguments.append("--marketplace-distributed")
        return subprocess.run(arguments, text=True, capture_output=True, check=False)

    def create_manifest(self):
        result = self.command("create")
        self.assertEqual(0, result.returncode, result.stderr)

    def assert_rejected(self, message, **overrides):
        result = self.command("verify", **overrides)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_creates_and_verifies_the_closed_release(self):
        result = self.command("verify", marketplace_distributed=True, marketplace_zip=self.marketplace)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual("default", value["channel"])
        self.assertEqual(hashlib.sha256(self.signed.read_bytes()).hexdigest(), value["published"]["zip"]["sha256"])
        self.assertNotEqual(self.signed.read_bytes(), self.marketplace.read_bytes())

    def test_public_verification_requires_the_second_marketplace_signature(self):
        self.assert_rejected(
            "second JetBrains signature",
            marketplace_distributed=True,
            marketplace_zip=self.signed,
        )

    def test_rejects_marketplace_or_manifest_byte_drift(self):
        changed = self.root / "marketplace-changed.zip"
        changed.write_bytes(self.signed.read_bytes() + b"changed")
        self.assert_rejected("Marketplace ZIP", marketplace_zip=changed)
        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        value["published"]["zip"]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(value), encoding="utf-8")
        self.assert_rejected("signed ZIP digest")

    def test_rejects_a_validly_double_signed_marketplace_zip_with_changed_plugin_entries(self):
        directory = self.root / "changed-marketplace"
        directory.mkdir(exist_ok=True)
        changed_unsigned = directory / "changed-unsigned.zip"
        changed_author = directory / "changed-author.zip"
        changed_marketplace = directory / "perf-sentinel-0.1.0-signed.zip"
        write_zip(changed_unsigned, payload=b"changed-plugin")
        subprocess.run(
            [
                "java", "-jar", str(signer_jar()), "sign",
                "-in", str(changed_unsigned), "-out", str(changed_author),
                "-cert-file", str(self.certificate), "-key-file", str(self.key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "java", "-jar", str(signer_jar()), "sign",
                "-in", str(changed_author), "-out", str(changed_marketplace),
                "-cert-file", str(self.marketplace_certificate), "-key-file", str(self.marketplace_key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assert_rejected("plugin entries differ", marketplace_zip=changed_marketplace)

    def test_rejects_missing_extra_or_malformed_release_data(self):
        mutations = (
            lambda value: value.update({"extra": True}),
            lambda value: value["published"].pop("sbom"),
            lambda value: value.update({"channel": "beta"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.create_manifest()
                value = json.loads(self.manifest.read_text(encoding="utf-8"))
                mutate(value)
                self.manifest.write_text(json.dumps(value), encoding="utf-8")
                self.assert_rejected("manifest")

    def test_rejects_missing_or_malformed_spdx(self):
        self.sbom.write_text('{"spdxVersion":"SPDX-2.3","packages":[]}', encoding="utf-8")
        self.assert_rejected("SBOM")


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_runbook_covers_activation_rotation_and_rollback(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for expected in (
            "jetbrains-release",
            "CERTIFICATE_CHAIN",
            "PRIVATE_KEY",
            "PRIVATE_KEY_PASSWORD",
            "PUBLISH_TOKEN",
            "initial manual upload",
            "reviewed author-signed stable ZIP manually",
            "scripts/release.sh v0.",
            "Rotation",
            "Rollback",
            "do not move or reuse the tag",
            "No Cosign",
            "No SLSA",
        ):
            self.assertIn(expected, text)

    def test_release_verifies_the_approved_signed_tag_before_building(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertTrue(ALLOWED_SIGNERS.is_file())
        allowed = ALLOWED_SIGNERS.read_text(encoding="ascii")
        self.assertIn("robin.trassard@gmail.com ssh-ed25519 ", allowed)
        validate = text.split("  validate-tag:\n", 1)[1].split("\n  windows-build:\n", 1)[0]
        self.assertIn('verify-tag "$GITHUB_REF_NAME"', validate)
        self.assertIn("config/release-allowed-signers", validate)
        self.assertIn('git rev-list -n 1 "$GITHUB_REF_NAME"', validate)
        windows = text.split("  windows-build:\n", 1)[1].split("\n  compare:\n", 1)[0]
        self.assertIn("needs: validate-tag", windows)

    def test_release_uses_two_windows_builds_and_one_protected_signing_job(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("tags: ['v0.*.*']", text)
        self.assertIn("runs-on: windows-2025", text)
        self.assertIn("- build: a", text)
        self.assertIn("- build: b", text)
        self.assertIn("scripts/compare-plugin-builds.py", text)
        self.assertIn("environment: jetbrains-release", text)
        protected = text.split("  jetbrains-release:\n", 1)[1]
        self.assertIn("scripts/check-plugin-signature.py", protected)
        self.assertIn("publishPlugin", protected)
        self.assertEqual(1, protected.count("signPlugin verifyPluginSignature"))
        self.assertIn("publishPlugin -x signPlugin -x verifyPluginSignature", protected)
        self.assertIn("gh release create", protected)
        self.assertNotIn('$version = "${{ github.ref_name }}"', text)
        build_step = text.split("      - name: Build and test the unsigned plugin\n", 1)[1]
        build_step = build_step.split("      - uses:", 1)[0]
        self.assertNotIn("${{", build_step)
        for forbidden in ("buildPlugin", "compileKotlin", "rdgen", "slsa", "cosign"):
            self.assertNotIn(forbidden, protected.lower())

    def test_protected_release_job_disables_the_gradle_cache(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        protected = text.split("  jetbrains-release:\n", 1)[1]
        setup = protected.split("      - uses: gradle/actions/setup-gradle@", 1)[1]
        setup = setup.split("      - name:", 1)[0]
        self.assertIn("cache-disabled: true", setup)
        self.assertNotIn("cache-read-only:", setup)

    def test_protected_job_rechecks_the_remote_tag_and_scopes_secrets_to_steps(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        protected = text.split("  jetbrains-release:\n", 1)[1]
        header = protected.split("    steps:\n", 1)[0]
        for secret in ("CERTIFICATE_CHAIN", "PRIVATE_KEY", "PRIVATE_KEY_PASSWORD", "PUBLISH_TOKEN"):
            self.assertNotIn(secret, header)
        recheck = protected.index('git fetch --force --no-tags origin "refs/tags/$GITHUB_REF_NAME:refs/tags/$GITHUB_REF_NAME"')
        verify = protected.index('verify-tag "$GITHUB_REF_NAME"', recheck)
        sign = protected.index("      - id: signed\n")
        self.assertLess(recheck, verify)
        self.assertLess(verify, sign)
        self.assertGreaterEqual(protected.count('git fetch --force --no-tags origin "refs/tags/$GITHUB_REF_NAME:refs/tags/$GITHUB_REF_NAME"'), 3)
        sign_block = protected[sign:protected.index("      - uses: anchore/sbom-action", sign)]
        for secret in ("CERTIFICATE_CHAIN", "PRIVATE_KEY", "PRIVATE_KEY_PASSWORD"):
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", sign_block)
        publish = protected.split("      - name: Publish the exact signed ZIP to Marketplace\n", 1)[1]
        publish = publish.split("      - name:", 1)[0]
        self.assertIn("PUBLISH_TOKEN: ${{ secrets.PUBLISH_TOKEN }}", publish)

    def test_signing_creates_its_fresh_output_directory_before_writing(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        signing = text.split("      - id: signed\n", 1)[1].split("      - uses: anchore/sbom-action", 1)[0]
        create = signing.index("mkdir -p build/distributions")
        write = signing.index("printf '%s' \"$CERTIFICATE_CHAIN\"")
        self.assertLess(create, write)

    def test_release_publishes_the_exact_closed_asset_set(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        for asset in (
            "perf-sentinel-$VERSION-signed.zip",
            "perf-sentinel-$VERSION-release.json",
            "perf-sentinel-$VERSION.spdx.json",
            "perf-sentinel-$VERSION-certificate-chain.pem",
        ):
            self.assertIn(asset, text)
        self.assertNotIn("attest-build-provenance", text)
        self.assertNotIn("slsa-github-generator", text)

    def test_github_upload_path_is_the_exact_path_verified_by_the_manifest(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('PUBLIC_SIGNED="build/release/perf-sentinel-$VERSION-signed.zip"', text)
        self.assertIn('--github-zip "$PUBLIC_SIGNED"', text)
        self.assertIn('"$PUBLIC_SIGNED" \\', text)

    def test_release_binds_the_downloaded_file_to_the_artifact_digest(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        binding = text.split("      - name: Bind downloaded artifact to the comparison result\n", 1)[1]
        binding = binding.split("      - name:", 1)[0]
        self.assertIn("sha256sum", binding)
        self.assertIn("EXPECTED_ARTIFACT_DIGEST#sha256:", binding)
        self.assertIn("perf-sentinel-$VERSION.zip", binding)

    def test_sbom_uses_explicit_outputs_from_the_signing_step(self):
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("      - id: signed\n", text)
        self.assertIn('echo "signed=$SIGNED"', text)
        self.assertIn('echo "version=$VERSION"', text)
        self.assertIn("file: ${{ steps.signed.outputs.signed }}", text)
        self.assertIn("output-file: build/release/perf-sentinel-${{ steps.signed.outputs.version }}.spdx.json", text)
        self.assertNotIn("file: ${{ env.SIGNED }}", text)

    def test_daily_verification_is_public_secret_free_and_exact(self):
        text = VERIFY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("https://plugins.jetbrains.com/plugin/download?pluginId=io.github.robintra.perfsentinel&version=$VERSION", text)
        self.assertIn('build/marketplace/perf-sentinel-$VERSION-signed.zip', text)
        self.assertIn("scripts/verify-release.py verify", text)
        self.assertIn("--marketplace-distributed", text)
        self.assertIn("expected exactly four GitHub release assets", text)
        self.assertIn("--max-filesize 419430400", text)
        self.assertIn("release asset set or size is invalid", text)
        resolve = text.index("./gradlew --no-daemon --dependency-verification strict help")
        verify = text.index("scripts/verify-release.py verify")
        self.assertLess(resolve, verify)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("cosign", text.lower())


if __name__ == "__main__":
    unittest.main()
