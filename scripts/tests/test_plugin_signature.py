import hashlib
import importlib.util
import json
import os
import ssl
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY / "scripts" / "check-plugin-signature.py"


def load_spec(name, path):
    """Both spec_from_file_location and its loader are Optional, narrow them once."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    return spec, spec.loader
SIGNER_GLOB = Path.home() / ".gradle/caches/modules-2/files-2.1/org.jetbrains/marketplace-zip-signer/0.1.43"


def signer_jar():
    jars = sorted(SIGNER_GLOB.glob("*/marketplace-zip-signer-0.1.43-cli.jar"))
    if len(jars) != 1:
        raise unittest.SkipTest("the locked JetBrains Marketplace ZIP signer is not cached")
    return jars[0]


def write_zip(path, payload=b"plugin"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("perf-sentinel/lib/plugin.jar", (1980, 2, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)


def sign_zip(source, target, certificate, key):
    """Sign a ZIP with the Marketplace ZIP Signer."""
    subprocess.run(
        [
            "java", "-jar", str(signer_jar()), "sign",
            "-in", str(source), "-out", str(target),
            "-cert-file", str(certificate), "-key-file", str(key),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class PluginSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.key = cls.root / "key.pem"
        cls.certificate = cls.root / "certificate.pem"
        cls.unsigned = cls.root / "unsigned.zip"
        cls.signed = cls.root / "signed.zip"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "2", "-subj", "/CN=Perf Sentinel Test Signer/O=Perf Sentinel Test",
                "-keyout", str(cls.key), "-out", str(cls.certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        write_zip(cls.unsigned)
        sign_zip(cls.unsigned, cls.signed, cls.certificate, cls.key)
        der = ssl.PEM_cert_to_DER_cert(cls.certificate.read_text(encoding="ascii"))
        cls.fingerprint = hashlib.sha256(der).hexdigest()
        cls.identity = cls.root / "identity.json"
        cls.write_identity(cls.fingerprint)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.write_identity(self.fingerprint)

    @classmethod
    def write_identity(cls, fingerprint, *, status="active", common_name="Perf Sentinel Test Signer", extra=None):
        value = {
            "schemaVersion": 1,
            "provider": "JetBrains Marketplace ZIP Signer",
            "timestampPolicy": "unsupported-by-format",
            "certificate": {
                "status": status,
                "commonName": common_name,
                "sha256Fingerprint": fingerprint,
            },
        }
        if extra:
            value.update(extra)
        cls.identity.write_text(json.dumps(value), encoding="utf-8")

    def run_checker(self, unsigned=None, signed=None, certificate=None, identity=None):
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--unsigned", str(unsigned or self.unsigned),
                "--signed", str(signed or self.signed),
                "--certificate", str(certificate or self.certificate),
                "--identity", str(identity or self.identity),
                "--signer-jar", str(signer_jar()),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message, **kwargs):
        result = self.run_checker(**kwargs)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_accepts_official_signature_without_payload_changes(self):
        result = self.run_checker()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(hashlib.sha256(b"plugin").hexdigest(), hashlib.sha256(
            zipfile.ZipFile(self.signed).read("perf-sentinel/lib/plugin.jar")
        ).hexdigest())
        self.assertNotIn(str(self.key), result.stdout + result.stderr)

    def test_rejects_changed_payload(self):
        changed_unsigned = self.root / "changed-unsigned.zip"
        changed_signed = self.root / "changed-signed.zip"
        write_zip(changed_unsigned, b"changed")
        subprocess.run(
            [
                "java", "-jar", str(signer_jar()), "sign",
                "-in", str(changed_unsigned), "-out", str(changed_signed),
                "-cert-file", str(self.certificate), "-key-file", str(self.key),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assert_rejected("payload", signed=changed_signed)

    def test_rejects_unsigned_eocd_offset_that_disagrees_with_the_archive(self):
        changed = self.root / "changed-eocd.zip"
        value = bytearray(self.unsigned.read_bytes())
        end_record = value.rfind(b"PK\x05\x06")
        central_offset = struct.unpack_from("<I", value, end_record + 16)[0]
        struct.pack_into("<I", value, end_record + 16, central_offset - 1)
        changed.write_bytes(value)
        self.assert_rejected("unsigned ZIP central directory offset", unsigned=changed)

    def test_signature_block_is_inspected_with_bounded_reads(self):
        spec, loader = load_spec("plugin_signature", CHECKER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        requested = []

        class RecordingFile:
            def __init__(self, stream):
                self.stream = stream

            def read(self, size=-1):
                requested.append(size)
                return self.stream.read(size)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

        try:
            loader.exec_module(module)
            unsigned = self.unsigned.read_bytes()
            end_record = unsigned.rfind(b"PK\x05\x06")
            central_offset = struct.unpack_from("<I", unsigned, end_record + 16)[0]
            gap_size = 2 * 1024 * 1024
            encoded_size = struct.pack("<Q", gap_size - 8)
            gap = encoded_size + bytes(gap_size - 32) + encoded_size + module.SIGNING_MAGIC
            signed = bytearray(unsigned[:central_offset] + gap + unsigned[central_offset:])
            signed_end_record = end_record + gap_size
            struct.pack_into("<I", signed, signed_end_record + 16, central_offset + gap_size)
            synthetic = self.root / "synthetic-large-signature.zip"
            synthetic.write_bytes(signed)
            original_open = Path.open

            def recording_open(path, *args, **kwargs):
                return RecordingFile(original_open(path, *args, **kwargs))

            with patch.object(Path, "open", recording_open):
                module.compare_payload(self.unsigned, synthetic)
            self.assertLessEqual(max(requested), 64 * 1024)
        finally:
            sys.modules.pop(spec.name, None)

    def test_rejects_wrong_certificate_identity_and_pending_activation(self):
        self.write_identity("0" * 64)
        self.assert_rejected("fingerprint")
        self.write_identity(None, status="pending_activation")
        self.assert_rejected("pending activation")

    def test_rejects_unknown_identity_fields_and_timestamp_claims(self):
        self.write_identity(self.fingerprint, extra={"timestamp": "required"})
        self.assert_rejected("unknown field")

    def test_rejects_invalid_chain(self):
        other_key = self.root / "other-key.pem"
        other_certificate = self.root / "other-certificate.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "2", "-subj", "/CN=Other/O=Perf Sentinel Test",
                "-keyout", str(other_key), "-out", str(other_certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        der = ssl.PEM_cert_to_DER_cert(other_certificate.read_text(encoding="ascii"))
        self.write_identity(hashlib.sha256(der).hexdigest(), common_name="Other")
        self.assert_rejected("signature", certificate=other_certificate)

    def test_rejects_unlocked_signer_bytes(self):
        changed_signer = self.root / "changed-signer.jar"
        changed_signer.write_bytes(signer_jar().read_bytes() + b"changed")
        result = subprocess.run(
            [
                sys.executable, str(CHECKER),
                "--unsigned", str(self.unsigned), "--signed", str(self.signed),
                "--certificate", str(self.certificate), "--identity", str(self.identity),
                "--signer-jar", str(changed_signer),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("signer checksum", result.stderr)
        self.write_identity(self.fingerprint)

    def test_rejects_expired_certificate(self):
        spec, loader = load_spec("plugin_signature", CHECKER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            loader.exec_module(module)
            details = module.certificate_details(
                self.certificate,
                now=datetime.now(timezone.utc) + timedelta(days=3),
            )
            self.assertIn("expired", details.error)
        finally:
            sys.modules.pop(spec.name, None)

    def test_rejects_an_expired_intermediate_certificate(self):
        root_key = self.root / "root-key.pem"
        root_certificate = self.root / "root-certificate.pem"
        intermediate_key = self.root / "intermediate-key.pem"
        intermediate_request = self.root / "intermediate.csr"
        intermediate_certificate = self.root / "intermediate-certificate.pem"
        leaf_key = self.root / "leaf-key.pem"
        leaf_request = self.root / "leaf.csr"
        leaf_certificate = self.root / "leaf-certificate.pem"
        chain = self.root / "certificate-chain.pem"
        commands = [
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "10", "-subj", "/CN=Root", "-keyout", str(root_key),
                "-out", str(root_certificate),
            ],
            [
                "openssl", "req", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-subj", "/CN=Intermediate", "-keyout", str(intermediate_key),
                "-out", str(intermediate_request),
            ],
            [
                "openssl", "x509", "-req", "-sha256", "-days", "2",
                "-in", str(intermediate_request), "-CA", str(root_certificate),
                "-CAkey", str(root_key), "-CAcreateserial", "-out", str(intermediate_certificate),
            ],
            [
                "openssl", "req", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-subj", "/CN=Leaf", "-keyout", str(leaf_key), "-out", str(leaf_request),
            ],
            [
                "openssl", "x509", "-req", "-sha256", "-days", "5",
                "-in", str(leaf_request), "-CA", str(intermediate_certificate),
                "-CAkey", str(intermediate_key), "-CAcreateserial", "-out", str(leaf_certificate),
            ],
        ]
        for command in commands:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chain.write_text(
            leaf_certificate.read_text(encoding="ascii")
            + intermediate_certificate.read_text(encoding="ascii")
            + root_certificate.read_text(encoding="ascii"),
            encoding="ascii",
        )

        spec, loader = load_spec("plugin_signature", CHECKER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            loader.exec_module(module)
            details = module.certificate_details(
                chain,
                now=datetime.now(timezone.utc) + timedelta(days=3),
            )
            self.assertEqual("certificate chain member 2 is expired", details.error)
        finally:
            sys.modules.pop(spec.name, None)

    def test_release_graph_uses_native_tasks_without_rebuild(self):
        text = (REPOSITORY / "build.gradle.kts").read_text(encoding="utf-8")
        for task_type in ("SignPluginTask", "VerifyPluginSignatureTask", "PublishPluginTask"):
            self.assertIn(task_type, text)
        self.assertIn('gradleProperty("releaseUnsignedZip")', text)
        self.assertIn("setDependsOn(emptyList<Any>())", text)
        self.assertIn('environmentVariable("CERTIFICATE_CHAIN")', text)
        self.assertIn('environmentVariable("PRIVATE_KEY")', text)
        self.assertIn('environmentVariable("PRIVATE_KEY_PASSWORD")', text)
        self.assertIn('environmentVariable("PUBLISH_TOKEN")', text)
        self.assertIn("setDependsOn(listOf(verifyPluginSignatureTask))", text)
        release_block = text[text.index("if (releaseUnsignedZip.isPresent)"):]
        for forbidden in ("buildPlugin", "compileKotlin", "rdgen", "compileRiderBackend"):
            self.assertNotIn(forbidden, release_block)

        environment = os.environ.copy()
        environment.update({
            "CERTIFICATE_CHAIN": "fixture-certificate",
            "PRIVATE_KEY": "fixture-private-key",
            "PRIVATE_KEY_PASSWORD": "fixture-password",
            "PUBLISH_TOKEN": "fixture-token",
        })
        result = subprocess.run(
            [
                str(REPOSITORY / "gradlew"), "--offline", "--no-daemon", "--console=plain",
                "signPlugin", "verifyPluginSignature", "publishPlugin", "--dry-run",
                f"-PreleaseUnsignedZip={self.unsigned}",
            ],
            cwd=REPOSITORY,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        graph = result.stdout + result.stderr
        for forbidden in ("buildPlugin", "compileKotlin", "rdgen", "compileRiderBackend"):
            self.assertNotIn(forbidden, graph)

    def test_production_identity_is_honestly_pending(self):
        value = json.loads((REPOSITORY / "config/signing-identities.json").read_text(encoding="utf-8"))
        self.assertEqual("pending_activation", value["certificate"]["status"])
        self.assertIsNone(value["certificate"]["sha256Fingerprint"])


if __name__ == "__main__":
    unittest.main()
