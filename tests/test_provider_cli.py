import contextlib
import io
import json
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

    def test_run_opencode_merges_provider_models_without_clobbering_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "opencode.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            target.write_text(json.dumps({"theme": "dark", "provider": {"other": {"models": {}}}}))

            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "run", "opencode", "qwen", "--config", str(target)]):
                main()

            document = json.loads(target.read_text())
            self.assertEqual(document["theme"], "dark")
            self.assertEqual(document["provider"]["wmadapter-qwen"]["models"], {"qwen-chat": {"name": "qwen-chat"}})

    def test_run_openclaw_merges_provider_models_without_clobbering_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "openclaw.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            target.write_text(json.dumps({"agents": {"defaults": {"model": "wmadapter/qwen-chat"}}}))

            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "run", "openclaw", "qwen", "--config", str(target)]):
                main()

            document = json.loads(target.read_text())
            self.assertEqual(document["agents"]["defaults"]["model"], "wmadapter/qwen-chat")
            provider = document["models"]["providers"]["wmadapter"]
            self.assertEqual(provider["baseUrl"], "http://127.0.0.1:11555/v1")
            self.assertEqual(provider["timeoutSeconds"], 300)
            self.assertEqual(provider["models"], [{"id": "qwen-chat", "name": "qwen-chat"}])

    def test_check_ready_returns_nonzero_for_unready_service(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            save_config({"server": {"host": "127.0.0.1", "port": 1}}, source)
            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "check", "ready"]):
                with self.assertRaises(SystemExit) as raised:
                    with patch("wmadapter.__main__.urllib.request.urlopen", side_effect=OSError("offline")):
                        main()
            self.assertEqual(raised.exception.code, 1)

    def test_check_ready_prints_only_okay_for_ready_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            save_config({"server": {"host": "127.0.0.1", "port": 11555}}, source)
            response = type("Response", (), {
                "status": 200,
                "__enter__": lambda self: self,
                "__exit__": lambda self, *args: None,
                "read": lambda self: b'{"status":"ready","providers":{"deepseek":{"ready":true}}}',
            })()
            output = io.StringIO()
            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "check", "ready", "deepseek"]), patch(
                "wmadapter.__main__.urllib.request.urlopen", return_value=response
            ), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    main()
            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), "okay")


    def test_doctor_fix_restarts_service_before_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            save_config({"server": {"host": "127.0.0.1", "port": 11555}}, source)
            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "doctor", "--fix"]), patch(
                "wmadapter.__main__._fix_service"
            ) as fix, patch("wmadapter.__main__._doctor", return_value=0) as doctor:
                with self.assertRaises(SystemExit) as raised:
                    main()
            self.assertEqual(raised.exception.code, 0)
            fix.assert_called_once_with(load_config(source))
            doctor.assert_called_once_with(load_config(source))


if __name__ == "__main__":
    unittest.main()
