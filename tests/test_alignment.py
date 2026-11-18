"""模式匹配测试：匹配器、候选选择、覆盖率与目标 schema。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.alignment.combine import Weights, combine
from governance.alignment.evaluate import coverage
from governance.alignment.matchers import (
    name_tokens,
    string_score,
    structure_score,
    type_score,
)
from governance.alignment.schema import FieldView, extract_source_fields
from governance.alignment.select import build_candidates, select_mappings
from governance.alignment.target_schema import (
    allowed_table_map,
    load_object_targets,
    target_views,
)
from tests.synthetic import write_synthetic_mimic


def _field(name: str, dtype: str = "int64", table: str = "t") -> FieldView:
    return FieldView(table=table, name=name, dtype=dtype)


class MatcherTest(unittest.TestCase):
    def test_name_tokens_split_suffix(self) -> None:
        self.assertIn("chart", name_tokens("charttime"))
        self.assertIn("time", name_tokens("charttime"))
        self.assertEqual(name_tokens("event_time"), {"event", "time"})

    def test_string_score(self) -> None:
        self.assertEqual(string_score(_field("subject_id"), _field("subject_id")), 1.0)
        score = string_score(_field("charttime"), _field("event_time"))
        self.assertGreater(score, 0.3)

    def test_type_score(self) -> None:
        self.assertEqual(type_score(_field("a", "int64"), _field("b", "int64")), 1.0)
        self.assertEqual(type_score(_field("a", "int64"), _field("b", "float64")), 0.8)
        self.assertEqual(
            type_score(_field("a", "datetime64[ns]"), _field("b", "timestamp[us]")), 1.0
        )

    def test_structure_score(self) -> None:
        self.assertEqual(structure_score(_field("charttime"), _field("event_time")), 0.5)
        self.assertEqual(structure_score(_field("a"), _field("b")), 0.0)

    def test_combine_weights(self) -> None:
        score = combine({"string": 1.0, "type": 1.0, "structure": 1.0, "value": 1.0}, Weights())
        self.assertEqual(score, 1.0)


class TargetSchemaTest(unittest.TestCase):
    def test_targets_are_source_derived_only(self) -> None:
        targets = load_object_targets()
        names = {target.field for target in targets}
        self.assertEqual(len(targets), 8)
        self.assertEqual(
            names,
            {
                "subject_id",
                "hadm_id",
                "stay_id",
                "event_time",
                "code",
                "display",
                "numeric_value",
                "text_value",
            },
        )
        # 派生/聚合字段不能出现在匹配目标里
        for derived in ("record_id", "event_type", "n_admissions", "total_los_days"):
            self.assertNotIn(derived, names)

    def test_required_and_allowed_sources(self) -> None:
        targets = load_object_targets()
        required = {target.field for target in targets if target.required}
        self.assertEqual(required, {"subject_id", "code"})
        allowed = allowed_table_map(targets)
        self.assertIn("diagnoses_icd", allowed[("clinical_events", "code")])
        self.assertNotIn("patients", allowed[("clinical_events", "stay_id")])


class SelectTest(unittest.TestCase):
    def test_selects_top_k_per_target(self) -> None:
        sources = [
            _field("subject_id", "int64", "patients"),
            _field("hadm_id", "int64", "admissions"),
            _field("unrelated", "object", "other"),
        ]
        targets = [
            _field("subject_id", "int64", "clinical_events"),
            _field("hadm_id", "int64", "clinical_events"),
        ]
        candidates = build_candidates(sources, targets)
        selected = select_mappings(candidates, threshold=0.5, top_k=2)
        pairs = {(row.target_field, row.source_field) for row in selected.itertuples()}
        self.assertIn(("subject_id", "subject_id"), pairs)
        self.assertIn(("hadm_id", "hadm_id"), pairs)
        self.assertIn("rank", selected.columns)

    def test_allowed_tables_filter(self) -> None:
        sources = [_field("stay_id", "int64", "patients"), _field("stay_id", "int64", "icustays")]
        targets = [_field("stay_id", "int64", "clinical_events")]
        candidates = build_candidates(
            sources,
            targets,
            allowed_tables={("clinical_events", "stay_id"): {"icustays"}},
        )
        self.assertEqual(set(candidates["source_table"]), {"icustays"})

    def test_candidates_have_ranks(self) -> None:
        candidates = build_candidates([_field("a", "int64")], [_field("a", "int64", "tgt")])
        self.assertEqual(candidates["rank"].tolist(), [1])


class CoverageTest(unittest.TestCase):
    def test_reports_required_and_review(self) -> None:
        targets = load_object_targets()
        frame = pd.DataFrame(
            [
                {
                    "target_table": "clinical_events",
                    "target_field": "subject_id",
                    "required": True,
                    "source_table": "patients",
                    "source_field": "subject_id",
                    "score": 1.0,
                    "rank": 1,
                    "status": "auto",
                },
                {
                    "target_table": "clinical_events",
                    "target_field": "code",
                    "required": True,
                    "source_table": "diagnoses_icd",
                    "source_field": "icd_code",
                    "score": 0.6,
                    "rank": 1,
                    "status": "review",
                },
            ]
        )
        report = coverage(frame, targets)
        self.assertEqual(report["required_total"], 2)
        self.assertEqual(report["required_mapped"], 2)
        self.assertEqual(report["required_coverage"], 1.0)
        self.assertEqual(report["auto_mappings"], 1)
        self.assertEqual(report["review_mappings"], 1)

    def test_missing_required_is_listed(self) -> None:
        targets = load_object_targets()
        report = coverage(pd.DataFrame(), targets)
        self.assertIn("clinical_events.subject_id", report["missing_required"])
        self.assertIn("clinical_events.code", report["missing_required"])


class AlignmentStageTest(unittest.TestCase):
    def test_candidates_and_coverage_on_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mimic = write_synthetic_mimic(root)
            hosp = mimic / "hosp"
            tables = {
                "patients": pd.read_csv(hosp / "patients.csv.gz"),
                "admissions": pd.read_csv(hosp / "admissions.csv.gz"),
                "diagnoses_icd": pd.read_csv(hosp / "diagnoses_icd.csv.gz"),
                "prescriptions": pd.read_csv(hosp / "prescriptions.csv.gz"),
                "d_icd_diagnoses": pd.read_csv(hosp / "d_icd_diagnoses.csv.gz"),
            }
            targets = load_object_targets()
            sources = extract_source_fields(tables)
            candidates = build_candidates(
                sources,
                target_views(targets),
                allowed_tables=allowed_table_map(targets),
            )
            selected = select_mappings(candidates, threshold=0.5, top_k=3)
            self.assertGreater(len(selected), 0)
            self.assertFalse(candidates.empty)
            self.assertTrue({"target_table", "target_field", "score"}.issubset(candidates.columns))


if __name__ == "__main__":
    unittest.main()
