from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lora.config import load_run_config
from lora.credentials import (
    credential_is_configured,
    delete_user_credential,
    list_user_credential_names,
    load_credentials,
    lookup_credential,
    read_env_entries,
    set_user_credential,
    user_credentials_path,
    write_env_entries,
)


class SecretsTests(unittest.TestCase):
    def test_load_credentials_prefers_existing_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_root = root / "user"
            user_root.mkdir()
            (user_root / "credentials.env").write_text(
                "DEEPSEEK_API_KEY=file-key\n", encoding="utf-8"
            )
            os.environ["DEEPSEEK_API_KEY"] = "process-key"
            try:
                load_credentials(user_lora_root=user_root)
                value, source = lookup_credential("DEEPSEEK_API_KEY")
            finally:
                os.environ.pop("DEEPSEEK_API_KEY", None)

        self.assertEqual(value, "process-key")
        self.assertEqual(source, "env:DEEPSEEK_API_KEY")

    def test_load_credentials_uses_only_the_user_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_root = root / "user"
            user_root.mkdir()
            (user_root / "credentials.env").write_text(
                "DEEPSEEK_API_KEY=user-key\n", encoding="utf-8"
            )
            (root / ".env.local").write_text(
                "OPENAI_API_KEY=project-key\n", encoding="utf-8"
            )
            os.environ.pop("OPENAI_API_KEY", None)
            sources = load_credentials(user_lora_root=user_root)
            self.assertIn("file:" + str(user_root / "credentials.env"), sources)
            self.assertNotIn("file:" + str(root / ".env.local"), sources)

            value, source = lookup_credential("DEEPSEEK_API_KEY")
            self.assertEqual(value, "user-key")
            self.assertEqual(source, "env:DEEPSEEK_API_KEY")
            self.assertIsNone(os.environ.get("OPENAI_API_KEY"))

    def test_set_and_delete_user_credential_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_root = Path(tmp)
            path = set_user_credential(user_root, "DEV_API_KEY", "secret-value")

            self.assertEqual(path, user_credentials_path(user_root))
            self.assertEqual(read_env_entries(path)["DEV_API_KEY"], "secret-value")
            self.assertEqual(list_user_credential_names(user_root), ["DEV_API_KEY"])
            self.assertTrue(
                credential_is_configured("DEV_API_KEY", user_lora_root=user_root)
            )
            self.assertTrue(delete_user_credential(user_root, "DEV_API_KEY"))
            self.assertEqual(list_user_credential_names(user_root), [])
            self.assertFalse(
                credential_is_configured("DEV_API_KEY", user_lora_root=user_root)
            )

    def test_write_env_entries_preserves_other_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.env"
            write_env_entries(path, {"A": "1", "B": "2"})
            entries = read_env_entries(path)
            entries["C"] = "3"
            write_env_entries(path, entries)
            self.assertEqual(read_env_entries(path), {"A": "1", "B": "2", "C": "3"})

    def test_load_run_config_reads_user_credentials_file(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("lora.config.loader.Path.home", return_value=Path(tmp)),
        ):
            root = Path(tmp) / "workspace"
            user_root = Path(tmp) / ".lora"
            root.mkdir()
            user_root.mkdir()
            from lora.config import replace_user_model_config
            from tests.unit.test_model_configuration import native_mapping

            mapping = native_mapping()
            mapping["connections"]["shared"]["credential"] = {
                "env": "DEV_API_KEY"
            }
            replace_user_model_config(
                user_root,
                model_config=mapping,
                agents=[
                    {
                        "alias": "dev",
                        "model_request": {"default_model_group": "coding"},
                    }
                ],
            )
            (user_root / "credentials.env").write_text(
                "DEV_API_KEY=from-user-file\n", encoding="utf-8"
            )
            os.environ.pop("DEV_API_KEY", None)
            config = load_run_config(workspace_root=root, agent_alias="dev")

        self.assertEqual(config.model_configuration_status, "configured")
        serialized = str(config.to_dict())
        self.assertIn("DEV_API_KEY", serialized)
        self.assertNotIn("from-user-file", serialized)

    def test_legacy_routes_do_not_create_a_default_credential(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("lora.config.loader.Path.home", return_value=Path(tmp)),
        ):
            root = Path(tmp)
            user_root = root / ".lora"
            user_root.mkdir()
            (user_root / "config.yaml").write_text(
                "agents:\n  - alias: dev\n    model_request:\n      routes:\n        - id: primary\n          provider: openai\n          model_name: m\n          base_url: https://example.test/v1\n          api_key_env: DEEPSEEK_API_KEY\n",
                encoding="utf-8",
            )
            config = load_run_config(workspace_root=root, agent_alias="dev")

        self.assertEqual(config.model_configuration_status, "legacy")
        self.assertIsNone(config.model_config)
        self.assertIsNone(config.resolved_agent)

    def test_user_credentials_file_wins_over_process_environment(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("lora.config.loader.Path.home", return_value=Path(tmp)),
        ):
            root = Path(tmp) / "workspace"
            user_root = Path(tmp) / ".lora"
            root.mkdir()
            user_root.mkdir()
            (user_root / "credentials.env").write_text(
                "DEV_API_KEY=file-key\n", encoding="utf-8"
            )
            os.environ["DEV_API_KEY"] = "stale-process-key"
            try:
                value, source = lookup_credential(
                    "DEV_API_KEY", user_lora_root=user_root
                )
            finally:
                os.environ.pop("DEV_API_KEY", None)

        self.assertEqual(value, "file-key")
        self.assertEqual(source, "user-file:DEV_API_KEY")


if __name__ == "__main__":
    unittest.main()
