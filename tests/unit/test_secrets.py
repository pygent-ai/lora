from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from lora.config import load_run_config
from lora.secrets import (
    DEFAULT_API_KEY_ENV,
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
            (user_root / "credentials.env").write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
            os.environ["DEEPSEEK_API_KEY"] = "process-key"
            try:
                load_credentials(user_lora_root=user_root, workspace_root=root)
                value, source = lookup_credential("DEEPSEEK_API_KEY")
            finally:
                os.environ.pop("DEEPSEEK_API_KEY", None)

        self.assertEqual(value, "process-key")
        self.assertEqual(source, "env:DEEPSEEK_API_KEY")

    def test_load_credentials_merges_user_project_and_legacy_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_root = root / "user"
            user_root.mkdir()
            (user_root / "credentials.env").write_text("DEEPSEEK_API_KEY=user-key\n", encoding="utf-8")
            (root / ".env.local").write_text("OPENAI_API_KEY=project-key\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                sources = load_credentials(user_lora_root=user_root, workspace_root=root)
            self.assertIn("file:" + str(user_root / "credentials.env"), sources)
            self.assertIn("file:" + str(root / ".env.local"), sources)

            value, source = lookup_credential("DEEPSEEK_API_KEY")
            self.assertEqual(value, "user-key")
            self.assertEqual(source, "env:DEEPSEEK_API_KEY")
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "project-key")
            self.assertFalse(any("deprecated" in str(item.message).lower() for item in caught))

    def test_load_credentials_warns_for_legacy_workspace_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_root = root / "user"
            user_root.mkdir()
            (root / ".env").write_text("DEEPSEEK_API_KEY=legacy-key\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                load_credentials(user_lora_root=user_root, workspace_root=root)

        self.assertTrue(any("deprecated" in str(item.message).lower() for item in caught))
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "legacy-key")

    def test_set_and_delete_user_credential_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_root = Path(tmp)
            path = set_user_credential(user_root, "DEV_API_KEY", "secret-value")

            self.assertEqual(path, user_credentials_path(user_root))
            self.assertEqual(read_env_entries(path)["DEV_API_KEY"], "secret-value")
            self.assertEqual(list_user_credential_names(user_root), ["DEV_API_KEY"])
            self.assertTrue(credential_is_configured("DEV_API_KEY", user_lora_root=user_root))
            self.assertTrue(delete_user_credential(user_root, "DEV_API_KEY"))
            self.assertEqual(list_user_credential_names(user_root), [])
            self.assertFalse(credential_is_configured("DEV_API_KEY", user_lora_root=user_root))

    def test_write_env_entries_preserves_other_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.env"
            write_env_entries(path, {"A": "1", "B": "2"})
            entries = read_env_entries(path)
            entries["C"] = "3"
            write_env_entries(path, entries)
            self.assertEqual(read_env_entries(path), {"A": "1", "B": "2", "C": "3"})

    def test_load_run_config_reads_user_credentials_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("lora.config.Path.home", return_value=Path(tmp)):
            root = Path(tmp) / "workspace"
            user_root = Path(tmp) / ".lora"
            root.mkdir()
            user_root.mkdir()
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agents:",
                        "  - alias: dev",
                        "    model_request:",
                        "      api_key_env: DEV_API_KEY",
                        "      model_name: profile-model",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (user_root / "credentials.env").write_text("DEV_API_KEY=from-user-file\n", encoding="utf-8")
            os.environ.pop("DEV_API_KEY", None)
            config = load_run_config(workspace_root=root, agent_alias="dev")

        self.assertEqual(config.api_key_source, "env:DEV_API_KEY")
        self.assertEqual(config.resolved_agent.api_key, "from-user-file")  # type: ignore[union-attr]
        self.assertEqual(config.resolved_agent.api_key_env, "DEV_API_KEY")  # type: ignore[union-attr]

    def test_default_api_key_env_is_deepseek(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text("agents:\n  - alias: dev\n    model_request:\n      model_name: m\n", encoding="utf-8")
            os.environ["DEEPSEEK_API_KEY"] = "fallback"
            try:
                config = load_run_config(workspace_root=root, agent_alias="dev")
            finally:
                os.environ.pop("DEEPSEEK_API_KEY", None)

        self.assertEqual(config.resolved_agent.api_key_env, DEFAULT_API_KEY_ENV)  # type: ignore[union-attr]
        self.assertEqual(config.api_key_source, "env:DEEPSEEK_API_KEY")


if __name__ == "__main__":
    unittest.main()
