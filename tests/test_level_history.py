"""分级历史：diff 计算与落盘。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.classify import _write_level_history, diff_levels


class DiffLevelsTest(unittest.TestCase):
    def test_changed_added_removed(self) -> None:
        previous = {"clinical_events.Diagnosis": "L2", "patient_360.PatientSummary": "L3"}
        current = {"clinical_events.Diagnosis": "L3", "clinical_events.LabEvent": "L2"}
        diff = diff_levels(previous, current)
        self.assertEqual(diff["changed"], {"clinical_events.Diagnosis": ["L2", "L3"]})
        self.assertEqual(diff["added"], ["clinical_events.LabEvent"])
        self.assertEqual(diff["removed"], ["patient_360.PatientSummary"])

    def test_first_run_has_no_changes(self) -> None:
        diff = diff_levels({}, {"a": "L1"})
        self.assertEqual(diff["changed"], {})
        self.assertEqual(diff["added"], ["a"])


class HistoryWriteTest(unittest.TestCase):
    def test_appends_one_record_and_diffs_against_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            first = pd.DataFrame(
                [["clinical_events", "Diagnosis", "C03", "L2", 1]],
                columns=["dataset_table", "object", "category_id", "level", "rows"],
            )
            _write_level_history(dest, first)
            second = first.copy()
            second.loc[0, "level"] = "L3"
            diff = _write_level_history(dest, second)
            self.assertEqual(diff["changed"], {"clinical_events.Diagnosis": ["L2", "L3"]})
            lines = (dest / "level_history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            last = json.loads(lines[-1])
            self.assertEqual(last["levels"]["clinical_events.Diagnosis"], "L3")


if __name__ == "__main__":
    unittest.main()
