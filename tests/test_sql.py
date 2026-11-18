"""SQL 生成与 SQLite 对账测试。

重点覆盖"SQL 由字段映射驱动"：目标字段的表达式必须来自 ``field_map``，
而不是源码里写死的四段规范。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.alignment.sql import (
    EVENT_FIELDS,
    build_sql,
    build_sql_with_usage,
    validate_sql,
)
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


def _field_map() -> pd.DataFrame:
    """模拟 schema matching 的产出（列名与 field_level.csv 一致）。"""
    rows = [
        ("subject_id", "patients", "subject_id", 0.98, "auto"),
        ("subject_id", "admissions", "subject_id", 0.95, "auto"),
        ("subject_id", "diagnoses_icd", "subject_id", 0.95, "auto"),
        ("subject_id", "procedures_icd", "subject_id", 0.95, "auto"),
        ("subject_id", "prescriptions", "subject_id", 0.95, "auto"),
        ("subject_id", "discharge", "subject_id", 0.95, "auto"),
        ("hadm_id", "admissions", "hadm_id", 0.98, "auto"),
        ("hadm_id", "diagnoses_icd", "hadm_id", 0.95, "auto"),
        ("hadm_id", "procedures_icd", "hadm_id", 0.95, "auto"),
        ("hadm_id", "prescriptions", "hadm_id", 0.95, "auto"),
        ("hadm_id", "discharge", "hadm_id", 0.95, "auto"),
        ("event_time", "procedures_icd", "chartdate", 0.72, "review"),
        ("event_time", "prescriptions", "starttime", 0.81, "auto"),
        ("event_time", "discharge", "charttime", 0.81, "auto"),
        ("code", "diagnoses_icd", "icd_code", 0.9, "auto"),
        ("code", "procedures_icd", "icd_code", 0.9, "auto"),
        ("code", "prescriptions", "formulary_drug_cd", 0.86, "auto"),
        ("display", "d_icd_diagnoses", "long_title", 0.8, "auto"),
        ("display", "d_icd_procedures", "long_title", 0.8, "auto"),
        ("numeric_value", "prescriptions", "dose_val_rx", 0.78, "auto"),
        ("text_value", "discharge", "text", 0.9, "auto"),
    ]
    return pd.DataFrame(
        rows,
        columns=["target_field", "source_table", "source_field", "score", "status"],
    ).assign(target_table="clinical_events")


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


class SqlMappingDrivenTest(unittest.TestCase):
    """表达式必须来自字段映射，而不是源码里写死的规范。"""

    def setUp(self) -> None:
        self.sql, self.usage = build_sql_with_usage(_field_map())

    def _section(self, event_type: str) -> str:
        start = self.sql.index(f"-- {event_type}")
        ends = [self.sql.index(f"-- {other}", start + 1) for other in
                ("Diagnosis", "Procedure", "Prescription", "ClinicalNote")
                if self.sql.find(f"-- {other}", start + 1) > 0]
        return self.sql[start:min(ends)] if ends else self.sql[start:]

    def test_code_expression_comes_from_mapping(self) -> None:
        self.assertIn("t.icd_code AS code", self._section("Diagnosis"))
        self.assertIn("t.formulary_drug_cd AS code", self._section("Prescription"))

    def test_event_time_differs_per_event_type(self) -> None:
        self.assertIn("t.chartdate AS event_time", self._section("Procedure"))
        self.assertIn("t.starttime AS event_time", self._section("Prescription"))
        self.assertIn("t.charttime AS event_time", self._section("ClinicalNote"))

    def test_join_table_source_uses_its_alias(self) -> None:
        self.assertIn("d.long_title AS display", self._section("Diagnosis"))
        self.assertIn("p.long_title AS display", self._section("Procedure"))

    def test_numeric_value_is_cast_from_mapping(self) -> None:
        self.assertIn("CAST(t.dose_val_rx AS REAL) AS numeric_value",
                      self._section("Prescription"))

    def test_literal_wins_over_mapping(self) -> None:
        note = self._section("ClinicalNote")
        self.assertIn("'DISCHARGE' AS code", note)
        self.assertIn("'Discharge summary' AS display", note)
        self.assertEqual(self.usage["by_event"]["ClinicalNote"]["code"], "spec")

    def test_text_value_uses_one_to_many_transform(self) -> None:
        self.assertIn("AS text_value", self._section("Prescription"))
        self.assertEqual(
            self.usage["by_event"]["Prescription"]["text_value"], "transform:dose_text"
        )

    def test_usage_reports_mapping_and_gaps(self) -> None:
        summary = self.usage["summary"]
        self.assertGreater(summary.get("mapping", 0), 0)
        self.assertGreater(summary.get("derived", 0), 0)
        self.assertIn("ClinicalNote.stay_id", self.usage["missing"])
        self.assertIn("diagnoses_icd.icd_code", self.usage["mapped_targets"])

    def test_without_field_map_everything_mapped_is_missing(self) -> None:
        _, usage = build_sql_with_usage(None)
        self.assertEqual(usage["summary"].get("mapping", 0), 0)
        self.assertGreater(len(usage["missing"]), 0)
        self.assertEqual(usage["mapping_rows"], 0)

    def test_sql_with_mapping_still_passes_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tables, notes = _tables(Path(tmp))
            expected = {"Diagnosis": 4, "Procedure": 2, "Prescription": 3, "ClinicalNote": 2}
            report = validate_sql(self.sql, tables, notes, expected)
            self.assertEqual(report["status"], "ok", msg=report.get("error"))


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
