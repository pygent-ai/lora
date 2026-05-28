from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lora.config import load_mapping_file, load_run_config


class ConfigTests(unittest.TestCase):
    def test_default_max_steps_is_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)

        self.assertEqual(config.max_steps, -1)

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


if __name__ == "__main__":
    unittest.main()
