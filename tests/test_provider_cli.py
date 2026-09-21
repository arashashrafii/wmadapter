import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wmadapter.__main__ import main
from wmadapter.config import load_config, save_config, update_provider_config


class ProviderCliTests(unittest.TestCase):
    def test_enable_provider_updates_model_allowlist(self):
        config = {"providers": {
            "default": "deepseek",
            "enabled": ["deepseek"],
            "enabled_models": ["deepseek-chat"],
        }}

        updated = update_provider_config(config, "enable", "qwen")

        self.assertEqual(updated["providers"]["enabled"], ["deepseek", "qwen"])
        self.assertEqual(updated["providers"]["enabled_models"], ["deepseek-chat", "qwen-chat"])
        self.assertEqual(config["providers"]["enabled"], ["deepseek"])

    def test_default_provider_is_enabled_and_cannot_be_disabled(self):
        config = {"providers": {"default": "deepseek", "enabled": ["deepseek"]}}

        updated = update_provider_config(config, "default", "qwen")

        self.assertEqual(updated["providers"]["default"], "qwen")
        self.assertEqual(updated["providers"]["enabled"], ["deepseek", "qwen"])
        with self.assertRaisesRegex(ValueError, "default provider cannot be disabled"):
            update_provider_config(updated, "disable", "qwen")

    def test_cli_persists_provider_changes_and_lists_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            save_config({"providers": {
                "default": "deepseek",
                "enabled": ["deepseek"],
                "enabled_models": ["deepseek-chat"],
            }}, path)

            with patch.object(sys, "argv", ["wmadapter", "--config", str(path), "provider", "enable", "qwen"]):
                main()
            self.assertEqual(load_config(path)["providers"]["enabled"], ["deepseek", "qwen"])

            output = io.StringIO()
            with patch.object(sys, "argv", ["wmadapter", "--config", str(path), "provider", "list"]), contextlib.redirect_stdout(output):
                main()
            self.assertIn("default: deepseek", output.getvalue())
            self.assertIn("qwen: enabled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
