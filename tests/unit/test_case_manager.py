from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lora.case import CaseManager
from lora.schema import CaseRunRef


class CaseManagerTests(unittest.TestCase):
    def test_load_validates_case_and_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_path = Path(tmp) / "case.yaml"
            case_path.write_text(
                "id: c1\ntitle: Demo\ntype: e2e\ninput:\n  content: hello\nexpect:\n  answer:\n    contains:\n      - Echo\n",
                encoding="utf-8",
            )
            manager = CaseManager(tmp)
            case = manager.load(case_path)

            self.assertEqual(case.id, "c1")
            self.assertEqual(manager.case_hash(case), manager.case_hash(case))

    def test_invalid_case_reports_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_path = Path(tmp) / "case.yaml"
            case_path.write_text("id: c1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "input"):
                CaseManager(tmp).load(case_path)

    def test_prepare_workspace_copies_fixture_and_writes_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixtures" / "case"
            fixture.mkdir(parents=True)
            (fixture / "a.txt").write_text("hello", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: c1",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: copy_fixture",
                        "      from: fixtures/case",
                        "      to: work/case",
                        "expect:",
                        "  files:",
                        "    unchanged:",
                        "      - work/case/a.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=root / "run")

            workspace = manager.prepare_workspace(case, run)

            self.assertTrue((root / "work" / "case" / "a.txt").exists())
            baseline = json.loads(Path(workspace.baseline_path or "").read_text(encoding="utf-8"))
            self.assertTrue(baseline["work/case/a.txt"]["exists"])


if __name__ == "__main__":
    unittest.main()
