from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from lora.config import load_mapping_file, load_run_config


class ConfigTests(unittest.TestCase):
    def test_default_max_steps_is_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)

        self.assertEqual(config.max_steps, -1)
        self.assertEqual(config.allow_read_outside_workspace, True)

    def test_load_run_config_merges_file_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "lora_root: data/lora\nruntime:\n  model: demo-model\n  max_steps: 3\n",
                encoding="utf-8",
            )
            config = load_run_config(workspace_root=root, max_steps=9)

        self.assertTrue(config.lora_root.endswith(str(Path("data") / "lora")))
        self.assertEqual(config.model, "demo-model")
        self.assertEqual(config.max_steps, 9)

    def test_env_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("LORA_MODEL")
            os.environ["LORA_MODEL"] = "env-model"
            try:
                config = load_run_config(workspace_root=tmp)
            finally:
                if old is None:
                    os.environ.pop("LORA_MODEL", None)
                else:
                    os.environ["LORA_MODEL"] = old

        self.assertEqual(config.model, "env-model")

    def test_yaml_subset_nested_maps_and_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.yaml"
            path.write_text(
                "id: demo\ninput:\n  messages:\n    - role: user\n      content: hello\n    - role: assistant\n      content: world\nmetrics:\n  max_turns: 8\n",
                encoding="utf-8",
            )
            data = load_mapping_file(path)

        self.assertEqual(data["input"]["messages"][0], {"role": "user", "content": "hello"})
        self.assertEqual(data["input"]["messages"][1], {"role": "assistant", "content": "world"})
        self.assertEqual(data["metrics"]["max_turns"], 8)

    def test_agent_profile_resolves_alias_model_and_secret_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _clean_model_env():
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agent:",
                        "  default_alias: dev",
                        "agents:",
                        "  - alias: dev",
                        "    model_request:",
                        "      model_name: profile-model",
                        "      api_key_env: DEV_API_KEY",
                        "      base_url: https://profile.example/v1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["DEV_API_KEY"] = "secret-from-env"

            config = load_run_config(workspace_root=root)

        self.assertEqual(config.agent_alias, "dev")
        self.assertEqual(config.model_name, "profile-model")
        self.assertEqual(config.api_key_source, "env:DEV_API_KEY")
        self.assertEqual(config.base_url, "https://profile.example/v1")
        self.assertEqual(config.resolved_agent.api_key, "secret-from-env")  # type: ignore[union-attr]
        self.assertNotIn("secret-from-env", str(config.to_dict()))

    def test_cli_model_overrides_agent_profile_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _clean_model_env():
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "agents:\n  - alias: fast-check\n    model_request:\n      model_name: profile-model\n",
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root, agent_alias="fast-check", model="cli-model")

        self.assertEqual(config.agent_alias, "fast-check")
        self.assertEqual(config.model_name, "cli-model")

    def test_missing_agent_alias_fails_before_session_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "agents:\n  - alias: dev\n    model_request:\n      model_name: profile-model\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing"):
                load_run_config(workspace_root=root, agent_alias="missing")

    def test_api_key_source_falls_back_to_config_without_leaking_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _clean_model_env():
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agents:",
                        "  - alias: dev",
                        "    model_request:",
                        "      api_key: config-secret",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root, agent_alias="dev")

        self.assertEqual(config.api_key_source, "config:model_request.api_key")
        self.assertEqual(config.resolved_agent.api_key, "config-secret")  # type: ignore[union-attr]
        self.assertNotIn("config-secret", str(config.to_dict()))

    def test_user_identity_and_cli_presets_resolve_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "user:",
                        "  identity: alice",
                        "cli:",
                        "  bash:",
                        "    presets:",
                        "      - name: rg",
                        "        command: rg --help",
                        "        description: Search files quickly.",
                        "      - name: pyright",
                        "        command: pyright --help",
                        "        description: Run Python type checks.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root)

        self.assertEqual(config.user_identity, "alice")
        self.assertEqual([preset.name for preset in config.cli_bash_presets], ["rg", "pyright"])
        self.assertEqual(config.cli_bash_presets[0].command, "rg --help")
        self.assertEqual(config.cli_bash_presets[1].description, "Run Python type checks.")

    def test_bash_full_output_allowlist_resolves_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "cli:",
                        "  bash:",
                        "    full_output_allowlist:",
                        "      - lora",
                        "      - uv run pytest",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root)

        self.assertEqual(config.bash_full_output_allowlist, ["lora", "uv run pytest"])

    def test_user_identity_and_cli_presets_have_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)

        self.assertEqual(config.user_identity, "default")
        self.assertEqual([preset.name for preset in config.cli_bash_presets], ["rg", "pyright"])
        self.assertEqual(config.bash_full_output_allowlist, [])

    def test_bash_full_output_allowlist_must_be_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "cli:\n  bash:\n    full_output_allowlist: lora\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "cli.bash.full_output_allowlist"):
                load_run_config(workspace_root=root)

    def test_allow_read_outside_workspace_can_be_disabled_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "runtime:\n  allow_read_outside_workspace: false\n",
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root)

        self.assertEqual(config.allow_read_outside_workspace, False)

@contextmanager
def _clean_model_env() -> Iterator[None]:
    keys = ["LORA_MODEL", "DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEV_API_KEY"]
    old = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
