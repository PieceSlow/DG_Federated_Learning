"""SQL 生成与 SQLite 对账测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.alignment.sql import EVENT_FIELDS, build_sql, validate_sql
from governance.alignment.transform import TRANSFORMS
from tests.synthetic import write_synthetic_mimic


def _tables(tmp: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    mimic = write_synthetic_mimic(tmp)
    hosp, icu, note = mimic / "hosp", mimic / "icu", tmp / "note"
    names = ["patients", "admissions", "diagnoses_icd", "procedures_icd",
             "prescriptions", "d_labitems", "d_icd_diagnoses", "d_icd_procedures"]
    tables = {name: pd.read_csv(hosp / f"{name}.csv.gz") for name in names}
    tables["icustays"] = pd.read_csv(icu / "icustays.csv.gz")
    notes = pd.read_csv(note / "discharge.csv.gz")
    return tables, notes


class SqlBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = build_sql()

    def test_four_event_types(self) -> None:
        for marker in ("-- Diagnosis", "-- Procedure", "-- Prescription", "-- ClinicalNote"):
            self.assertIn(marker, self.sql)
        self.assertEqual(self.sql.count("\nUNION ALL\n"), 3)

    def test_covers_all_target_fields(self) -> None:
        for field in EVENT_FIELDS:
            self.assertIn(f"AS {field}", self.sql)

    def test_dose_transform(self) -> None:
        expression = TRANSFORMS["dose_text"]["build"]("t")
        self.assertIn("dose_val_rx", expression)
        self.assertIn("||", expression)


class SqlExecutionTest(unittest.TestCase):
    def test_runs_and_matches_row_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tables, notes = _tables(Path(tmp))
            sql = build_sql()
            expected = {"Diagnosis": 4, "Procedure": 2, "Prescription": 3, "ClinicalNote": 2}
            report = validate_sql(sql, tables, notes, expected)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["rows"], 11)
            self.assertEqual(report["by_type"], expected)

    def test_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tables, notes = _tables(Path(tmp))
            report = validate_sql(build_sql(), tables, notes, {"Diagnosis": 999})
            self.assertEqual(report["status"], "mismatch")


if __name__ == "__main__":
    unittest.main()
