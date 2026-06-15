from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lora.cli import main
from lora.secrets import read_env_entries


class CredentialsCliTests(unittest.TestCase):
    def test_credentials_set_list_validate_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("lora.config.Path.home", return_value=Path(tmp)):
            root = Path(tmp) / "workspace"
            user_root = Path(tmp) / ".lora"
            root.mkdir()
            user_root.mkdir()
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agent:",
                        "  default_alias: dev",
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

            exit_code = main(
                [
                    "--workspace-root",
                    str(root),
                    "credentials",
                    "set",
                    "DEV_API_KEY",
                    "--value",
                    "cli-secret",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(read_env_entries(user_root / "credentials.env")["DEV_API_KEY"], "cli-secret")

            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                exit_code = main(["--workspace-root", str(root), "credentials", "list"])
            self.assertEqual(exit_code, 0)
            self.assertIn("DEV_API_KEY", buffer.getvalue())

            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                exit_code = main(["--workspace-root", str(root), "--agent", "dev", "credentials", "validate"])
            self.assertEqual(exit_code, 0)
            self.assertIn('"status": "ok"', buffer.getvalue())

            exit_code = main(
                [
                    "--workspace-root",
                    str(root),
                    "credentials",
                    "delete",
                    "DEV_API_KEY",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(read_env_entries(user_root / "credentials.env"), {})

    def test_credentials_validate_reports_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "agents:\n  - alias: dev\n    model_request:\n      api_key_env: DEV_API_KEY\n",
                encoding="utf-8",
            )
            os.environ.pop("DEV_API_KEY", None)
            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                exit_code = main(["--workspace-root", str(root), "--agent", "dev", "credentials", "validate"])
            self.assertEqual(exit_code, 0)
            self.assertIn('"status": "missing"', buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
