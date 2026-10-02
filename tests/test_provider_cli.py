import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wmadapter.__main__ import main
from wmadapter.config import load_config, mark_provider_authenticated, save_config, update_provider_config


class ProviderCliTests(unittest.TestCase):
    def test_enable_provider_updates_model_allowlist(self):
        config = {"providers": {
            "default": "deepseek",
            "enabled": ["deepseek"],
            "enabled_models": ["deepseek-chat"],
        }}

        updated = update_provider_config(config, "enable", "qwen")

        self.assertEqual(updated["providers"]["enabled"], ["deepseek", "qwen"])
        self.assertEqual(updated["providers"]["enabled_models"], [
            "deepseek-chat",
            "qwen-chat",
            "qwen3.7-plus",
            "qwen3.8-max",
            "qwen3.8-omni-flash",
            "qwen-image-3.0",
        ])
        self.assertEqual(config["providers"]["enabled"], ["deepseek"])

    def test_default_provider_is_enabled_and_cannot_be_disabled(self):
        config = {"providers": {"default": "deepseek", "enabled": ["deepseek"]}}

        updated = update_provider_config(config, "default", "qwen")

        self.assertEqual(updated["providers"]["default"], "qwen")
        self.assertEqual(updated["providers"]["enabled"], ["deepseek", "qwen"])
        with self.assertRaisesRegex(ValueError, "default provider cannot be disabled"):
            update_provider_config(updated, "disable", "qwen")

    def test_successful_login_enables_provider_unless_explicitly_disabled(self):
        config = {"providers": {"enabled": ["deepseek"]}}
        updated = mark_provider_authenticated(config, "qwen")
        self.assertEqual(updated["providers"]["enabled"], ["deepseek", "qwen"])

        disabled = {"providers": {"enabled": ["deepseek"], "disabled": ["qwen"]}}
        unchanged = mark_provider_authenticated(disabled, "qwen")
        self.assertEqual(unchanged["providers"]["enabled"], ["deepseek"])

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
            self.assertFalse(document["agents"]["defaults"]["experimental"]["localModelLean"])
            self.assertEqual(document["tools"]["toolSearch"], {"mode": "directory"})
            provider = document["models"]["providers"]["wmadapter"]
            self.assertEqual(provider["baseUrl"], "http://127.0.0.1:11555/v1")
            self.assertEqual(provider["timeoutSeconds"], 900)
            self.assertEqual(provider["models"], [{
                "id": "qwen-chat", "name": "qwen-chat",
                "compat": {"supportsTools": True},
            }])

    def test_run_openclaw_targets_selected_environment_and_qwen_models(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.test.yaml"
            target = Path(directory) / "openclaw.json"
            save_config({
                "server": {"host": "127.0.0.1", "port": 11556},
                "qwen": {"models": [
                    "qwen-chat", "qwen3.7-plus", "qwen3.8-max", "qwen3.8-omni-flash"
                ]},
            }, source)

            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "run", "openclaw", "qwen", "--config", str(target)]):
                main()

            provider = json.loads(target.read_text())["models"]["providers"]["wmadapter"]
            self.assertEqual(provider["baseUrl"], "http://127.0.0.1:11556/v1")
            self.assertEqual([item["id"] for item in provider["models"]], [
                "qwen-chat", "qwen3.7-plus", "qwen3.8-max", "qwen3.8-omni-flash"
            ])

    def test_run_pi_writes_openai_compatible_provider_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "models.json"
            save_config({"server": {"port": 11556}, "qwen": {"models": ["qwen-chat"]}}, source)
            target.write_text(json.dumps({"defaultProvider": "other", "providers": {"other": {}}}))
            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "run", "pi", "qwen", "--config", str(target)]):
                main()
            document = json.loads(target.read_text())
            self.assertEqual(document["defaultProvider"], "other")
            provider = document["providers"]["wmadapter-qwen"]
            self.assertEqual(provider["baseUrl"], "http://127.0.0.1:11556/v1")
            self.assertEqual(provider["models"], [{"id": "qwen-chat", "name": "qwen-chat"}])

    def test_run_dsh_merges_pi_ai_provider_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "cordis.patch.yml"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            target.write_text("- id: existing\n  config: {enabled: true}\n", encoding="utf-8")
            with patch.object(sys, "argv", ["wmadapter", "--config", str(source), "run", "dsh", "qwen", "--config", str(target)]):
                main()
            import yaml
            document = yaml.safe_load(target.read_text())
            self.assertEqual(document[0]["id"], "existing")
            provider = document[1]["config"]["providers"]["wmadapter-qwen"]
            self.assertEqual(provider["baseURL"], "http://127.0.0.1:11555/v1")
            self.assertEqual(provider["models"], [{"id": "qwen-chat"}])

    def test_run_without_a_provider_configures_every_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            cases = {
                "opencode": ("opencode.json", lambda document: sorted(
                    name for name in document["provider"] if name.startswith("wmadapter-"))),
                "pi": ("models.json", lambda document: sorted(
                    name for name in document["providers"] if name.startswith("wmadapter-"))),
                "omp": ("models.json", lambda document: sorted(
                    name for name in document["providers"] if name.startswith("wmadapter-"))),
            }
            for client, (filename, names) in cases.items():
                with self.subTest(client=client):
                    target = Path(directory) / filename
                    with patch.object(sys, "argv", [
                        "wmadapter", "--config", str(source), "run", client, "--config", str(target)
                    ]):
                        main()
                    document = json.loads(target.read_text())
                    self.assertEqual(names(document), ["wmadapter-deepseek", "wmadapter-qwen"])

    def test_run_without_a_provider_uses_deepseek_catalog_and_configured_qwen_models(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "models.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "pi", "--config", str(target)
            ]):
                main()
            providers = json.loads(target.read_text())["providers"]
            self.assertEqual([item["id"] for item in providers["wmadapter-deepseek"]["models"]],
                             ["deepseek-chat", "deepseek-reasoner"])
            self.assertEqual([item["id"] for item in providers["wmadapter-qwen"]["models"]], ["qwen-chat"])

    def test_run_openclaw_without_a_provider_aggregates_one_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "openclaw.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "openclaw", "--config", str(target)
            ]):
                main()
            provider = json.loads(target.read_text())["models"]["providers"]["wmadapter"]
            self.assertEqual([item["id"] for item in provider["models"]], [
                "deepseek-chat", "deepseek-reasoner", "qwen-chat",
            ])

    def test_run_dsh_without_a_provider_patches_both_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "cordis.patch.yml"
            save_config({"server": {"port": 11556}, "qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "dsh", "--config", str(target)
            ]):
                main()
            import yaml
            providers = yaml.safe_load(target.read_text())[0]["config"]["providers"]
            self.assertEqual(sorted(providers), ["wmadapter-deepseek", "wmadapter-qwen"])
            self.assertEqual(providers["wmadapter-qwen"]["baseURL"], "http://127.0.0.1:11556/v1")
            self.assertEqual(providers["wmadapter-deepseek"]["models"], [
                {"id": "deepseek-chat"}, {"id": "deepseek-reasoner"},
            ])

    def test_run_omp_writes_providers_and_default_model_role(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.test.yaml"
            target = Path(directory) / "agent" / "models.json"
            save_config({"server": {"port": 11556}, "qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "omp", "qwen", "--config", str(target)
            ]):
                main()
            import yaml
            provider = json.loads(target.read_text())["providers"]["wmadapter-qwen"]
            self.assertEqual(provider["baseUrl"], "http://127.0.0.1:11556/v1")
            self.assertEqual(provider["api"], "openai-completions")
            self.assertEqual(provider["models"], [{"id": "qwen-chat", "name": "qwen-chat"}])
            roles = yaml.safe_load((target.parent / "config.yml").read_text())["modelRoles"]
            self.assertEqual(roles["default"], "wmadapter-qwen/qwen-chat")
            self.assertEqual(roles["slow"], "wmadapter-qwen/qwen-chat")

    def test_run_omp_without_a_provider_selects_deepseek_and_qwen_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "agent" / "models.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "omp", "--config", str(target)
            ]):
                main()
            import yaml
            document = json.loads(target.read_text())
            self.assertEqual(sorted(document["providers"]), ["wmadapter-deepseek", "wmadapter-qwen"])
            roles = yaml.safe_load((target.parent / "config.yml").read_text())["modelRoles"]
            self.assertEqual(roles["default"], "wmadapter-deepseek/deepseek-chat")
            self.assertEqual(roles["smol"], "wmadapter-deepseek/deepseek-chat")
            self.assertEqual(roles["slow"], "wmadapter-qwen/qwen-chat")

    def test_run_omp_keeps_existing_providers_and_model_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "agent" / "models.json"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"providers": {"other": {"models": []}}}))
            (target.parent / "config.yml").write_text(
                "modelRoles:\n  default: other/custom\nsetupVersion: 2\n", encoding="utf-8")
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "omp", "qwen", "--config", str(target)
            ]):
                main()
            import yaml
            document = json.loads(target.read_text())
            self.assertIn("other", document["providers"])
            self.assertIn("wmadapter-qwen", document["providers"])
            settings = yaml.safe_load((target.parent / "config.yml").read_text())
            self.assertEqual(settings["setupVersion"], 2)
            self.assertEqual(settings["modelRoles"]["default"], "other/custom")
            self.assertEqual(settings["modelRoles"]["slow"], "wmadapter-qwen/qwen-chat")

    def test_run_hermes_writes_named_provider_and_custom_model_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.test.yaml"
            target = Path(directory) / "hermes" / "config.yaml"
            save_config({"server": {"port": 11556}, "qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "hermes", "qwen", "--config", str(target)
            ]):
                main()
            import yaml
            settings = yaml.safe_load(target.read_text())
            provider = settings["providers"]["wmadapter-qwen"]
            self.assertEqual(provider["api"], "http://127.0.0.1:11556/v1")
            self.assertEqual(provider["models"], ["qwen-chat"])
            self.assertEqual(settings["model"], {
                "provider": "custom",
                "base_url": "http://127.0.0.1:11556/v1",
                "default": "qwen-chat",
                "api_mode": "chat_completions",
                "api_key": "any",
            })

    def test_run_hermes_without_a_provider_configures_every_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "config.yaml"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "hermes", "--config", str(target)
            ]):
                main()
            import yaml
            settings = yaml.safe_load(target.read_text())
            self.assertEqual(sorted(settings["providers"]), ["wmadapter-deepseek", "wmadapter-qwen"])
            self.assertEqual(settings["providers"]["wmadapter-deepseek"]["models"],
                             ["deepseek-chat", "deepseek-reasoner"])
            self.assertEqual(settings["providers"]["wmadapter-qwen"]["models"], ["qwen-chat"])
            self.assertEqual(settings["model"]["default"], "deepseek-chat")

    def test_run_hermes_preserves_unrelated_settings_and_other_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "config.yaml"
            save_config({"qwen": {"models": ["qwen-chat"]}}, source)
            target.write_text(
                "model:\n  default: existing-model\n"
                "providers:\n  other-gateway:\n    api: http://localhost:9999/v1\n"
                "agent:\n  max_turns: 42\nterminal:\n  backend: local\n",
                encoding="utf-8",
            )
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "hermes", "qwen", "--config", str(target)
            ]):
                main()
            import yaml
            settings = yaml.safe_load(target.read_text())
            self.assertEqual(settings["agent"], {"max_turns": 42})
            self.assertEqual(settings["terminal"], {"backend": "local"})
            self.assertEqual(settings["providers"]["other-gateway"], {"api": "http://localhost:9999/v1"})
            self.assertIn("wmadapter-qwen", settings["providers"])
            self.assertEqual(settings["model"]["default"], "qwen-chat")

    def test_run_hermes_reuses_a_configured_gateway_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.yaml"
            target = Path(directory) / "config.yaml"
            save_config({"server": {"api_key": "secret-key"}, "qwen": {"models": ["qwen-chat"]}}, source)
            with patch.object(sys, "argv", [
                "wmadapter", "--config", str(source), "run", "hermes", "qwen", "--config", str(target)
            ]):
                main()
            import yaml
            settings = yaml.safe_load(target.read_text())
            self.assertEqual(settings["model"]["api_key"], "secret-key")
            self.assertEqual(settings["providers"]["wmadapter-qwen"]["api_key"], "secret-key")

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
