import hashlib
import multiprocessing
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from mimicgate.browser.distribution import (
    Artifact,
    SUPPORTED_PLATFORM,
    activate_atomically,
    cache_artifact,
    distribution_key,
    find_cached_artifact,
    parse_manifest,
    preflight_bundle,
    source_order,
    stage_artifact,
    verify_artifact,
    verify_manifest,
)
from mimicgate.browser.distribution import _exclusive_lock
from mimicgate.installers import UnsupportedPlatformError, installer_adapter


def _lock_competitor(lock_path, released, result):
    with _exclusive_lock(Path(lock_path)):
        result.write_text("released" if released.is_set() else "overlap")


class DistributionTests(unittest.TestCase):
    def _manifest(self, payload=b"mimicgate"):
        return {
            "schema_version": 1,
            "product": "mimicgate-browser",
            "playwright_version": "1.63.0",
            "browser": "chromium",
            "platform": dict(SUPPORTED_PLATFORM),
            "artifacts": {
                "chromium": {
                    "source": "offline",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            },
        }

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
        with patch("mimicgate.installers.platform.machine", return_value="x86_64"), patch(
            "mimicgate.installers.platform.freedesktop_os_release",
            return_value={"ID": "ubuntu", "VERSION_ID": "24.04"},
        ):
            self.assertEqual(installer_adapter("linux").platform_name, "linux")

    def test_manifest_schema_and_platform_are_strict(self):
        parsed = parse_manifest(self._manifest())
        self.assertEqual(parsed.artifact.size, 9)
        with self.assertRaisesRegex(ValueError, "schema_version"):
            parse_manifest({**self._manifest(), "schema_version": 2})
        unsupported = self._manifest()
        unsupported["platform"] = {**SUPPORTED_PLATFORM, "release": "22.04"}
        with self.assertRaisesRegex(ValueError, "unsupported"):
            parse_manifest(unsupported)

    def test_cache_is_keyed_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "bundle"
            source.write_bytes(b"mimicgate")
            manifest = parse_manifest(self._manifest())
            key = distribution_key(manifest.playwright_version, manifest.artifact.sha256)
            cached = cache_artifact(source, root / "cache", key, manifest.artifact)
            self.assertEqual(find_cached_artifact(root / "cache", key, manifest.artifact), cached)
            cached.write_bytes(b"tampered")
            self.assertIsNone(find_cached_artifact(root / "cache", key, manifest.artifact))

    def test_preflight_and_staging_reverify_local_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "bundle"
            source.write_bytes(b"mimicgate")
            result = preflight_bundle(self._manifest(), source, root / "cache", root / "active")
            self.assertEqual(result.source, source)
            staged = stage_artifact(source, result.manifest.artifact, root / "staging")
            self.assertEqual(staged.read_bytes(), b"mimicgate")

    def test_atomic_activation_replaces_previous_payload(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            destination = root / "active"
            destination.mkdir()
            (destination / "browser").write_text("old")
            staging = root / "staging"
            staging.mkdir()
            (staging / "browser").write_text("new")
            activate_atomically(staging, destination)
            self.assertEqual((destination / "browser").read_text(), "new")

    def test_non_linux_platforms_are_outside_verified_slice(self):
        for name in ("darwin", "windows", "android"):
            with self.subTest(name=name), self.assertRaises(UnsupportedPlatformError):
                installer_adapter(name)

    def test_linux_platform_gate_rejects_wrong_release_or_arch(self):
        cases = (("22.04", "x86_64"), ("24.04", "aarch64"))
        for release, machine in cases:
            with self.subTest(release=release, machine=machine), patch(
                "mimicgate.installers.platform.machine", return_value=machine
            ), patch(
                "mimicgate.installers.platform.freedesktop_os_release",
                return_value={"ID": "ubuntu", "VERSION_ID": release},
            ), self.assertRaises(UnsupportedPlatformError):
                installer_adapter("linux")

    def test_linux_platform_gate_rejects_non_ubuntu(self):
        with patch("mimicgate.installers.platform.machine", return_value="x86_64"), patch(
            "mimicgate.installers.platform.freedesktop_os_release",
            return_value={"ID": "debian", "VERSION_ID": "24.04"},
        ), self.assertRaises(UnsupportedPlatformError):
            installer_adapter("linux")

    def test_cross_process_lock_serializes_competing_transactions(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            lock_path = root / "cache.lock"
            held = multiprocessing.Event()
            released = multiprocessing.Event()
            result = root / "result"

            def holder():
                with _exclusive_lock(lock_path):
                    held.set()
                    time.sleep(0.2)
                    released.set()

            first = multiprocessing.Process(target=holder)
            first.start()
            self.assertTrue(held.wait(2))
            second = multiprocessing.Process(target=_lock_competitor, args=(lock_path, released, result))
            second.start()
            first.join(2)
            second.join(2)
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(second.exitcode, 0)
            self.assertEqual(result.read_text(), "released")
