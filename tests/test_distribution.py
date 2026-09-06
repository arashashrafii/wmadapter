import hashlib
import tempfile
import unittest
from pathlib import Path

from mimicgate.browser.distribution import Artifact, activate_atomically, source_order, verify_artifact, verify_manifest
from mimicgate.installers import installer_adapter


class DistributionTests(unittest.TestCase):
    def test_artifact_sha256_and_size(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "browser.bin"
            path.write_bytes(b"mimicgate")
            artifact = Artifact("offline", hashlib.sha256(b"mimicgate").hexdigest(), 9)
            verify_artifact(path, artifact)
            with self.assertRaises(ValueError):
                verify_artifact(path, Artifact("offline", "0" * 64, 9))

    def test_source_order_prefers_offline_and_cache(self):
        self.assertEqual(source_order([Path("bundle")], [Path("cache")], ["cdn"], ["mirror"]), [Path("bundle"), Path("cache"), "cdn", "mirror"])

    def test_manifest_signature_requires_injected_verifier(self):
        with self.assertRaises(ValueError):
            verify_manifest({"artifacts": {}, "signature": "sig"})
        verify_manifest({"artifacts": {}, "signature": "sig"}, lambda payload, signature: signature == "sig")

    def test_atomic_activation_and_platform_contract(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            staging = root / "staging"
            staging.mkdir()
            (staging / "browser").write_text("ok")
            activate_atomically(staging, root / "active")
            self.assertEqual((root / "active" / "browser").read_text(), "ok")
        self.assertEqual(installer_adapter("linux").platform_name, "linux")
