"""分级复核检测：新表/新列/自由文本/级别变化。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from governance.review import detect, write_review

SNAPSHOT = {
    "patients": {"rows": 10, "columns": {"subject_id": "int64", "gender": "object"}},
    "labevents": {"rows": 100, "columns": {"labevent_id": "int64", "note_text": "object"}},
}


class DetectTest(unittest.TestCase):
    def test_first_run_only_builds_baseline(self) -> None:
        report = detect(SNAPSHOT, baseline=None, level_diff=None)
        self.assertEqual(report["count"], 0)

    def test_new_table_and_free_text_column(self) -> None:
        baseline = {"patients": {"rows": 10, "columns": ["subject_id", "gender"]}}
        report = detect(SNAPSHOT, baseline, level_diff=None)
        types = {signal["type"] for signal in report["signals"]}
        self.assertIn("new_table", types)          # labevents
        self.assertIn("new_column", types)         # labevents 的列都算新列
        self.assertIn("possible_free_text", types)  # note_text
        free = [s for s in report["signals"] if s["type"] == "possible_free_text"]
        self.assertEqual(free[0]["suggested_level"], "L3")

    def test_level_change(self) -> None:
        report = detect(SNAPSHOT, baseline=json.loads(json.dumps(
            {"patients": {"rows": 10, "columns": ["subject_id", "gender"]},
             "labevents": {"rows": 100, "columns": ["labevent_id", "note_text"]}})),
            level_diff={"changed": {"patient_360.PatientSummary": ["L3", "L2"]},
                        "added": [], "removed": []})
        self.assertIn("level_changed", {s["type"] for s in report["signals"]})


class WriteReviewTest(unittest.TestCase):
    def test_writes_and_updates_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            first = write_review(dest, SNAPSHOT, level_diff=None)
            self.assertEqual(first["count"], 0)
            second = write_review(dest, SNAPSHOT, level_diff=None)
            self.assertEqual(second["count"], 0)
            self.assertTrue((dest / "review_required.json").exists())
            self.assertTrue((dest / "review_baseline.json").exists())


if __name__ == "__main__":
    unittest.main()
