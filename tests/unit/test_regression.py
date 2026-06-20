from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lora.evaluation.regression import RegressionManifest, _combined_status


class RegressionManifestTests(unittest.TestCase):
    def test_loads_json_manifest_with_cases_and_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "regression.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "fail_fast": True,
                        "cases": [{"path": "cases/basic.yaml", "session_mode": "new"}],
                    }
                ),
                encoding="utf-8",
            )

            manifest = RegressionManifest.from_file(manifest_path)

        self.assertTrue(manifest.fail_fast)
        self.assertEqual(manifest.cases[0].path, "cases/basic.yaml")
        self.assertEqual(manifest.cases[0].session_mode, "new")

    def test_rejects_invalid_case_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "regression.json"
            manifest_path.write_text(
                json.dumps({"version": "1.0", "cases": [{"session_mode": "new"}]}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                RegressionManifest.from_file(manifest_path)


class RegressionStatusTests(unittest.TestCase):
    def test_error_takes_precedence_over_failed(self) -> None:
        self.assertEqual(_combined_status([{"status": "passed"}, {"status": "failed"}, {"status": "error"}]), "error")

    def test_failed_takes_precedence_over_passed(self) -> None:
        self.assertEqual(_combined_status([{"status": "passed"}, {"status": "failed"}]), "failed")

    def test_empty_suite_is_skipped(self) -> None:
        self.assertEqual(_combined_status([]), "skipped")


if __name__ == "__main__":
    unittest.main()
