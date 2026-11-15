"""模式匹配测试：匹配器、选择与 gold 评测。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.alignment.combine import Weights, combine
from governance.alignment.evaluate import evaluate
from governance.alignment.matchers import (
    name_tokens,
    string_score,
    structure_score,
    type_score,
)
from governance.alignment.schema import FieldView, extract_source_fields
from governance.alignment.select import build_candidates, select_mappings
from governance.align import _field_map
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


class SelectTest(unittest.TestCase):
    def test_selects_best_per_target(self) -> None:
        sources = [
            _field("subject_id", "int64", "patients"),
            _field("hadm_id", "int64", "admissions"),
            _field("unrelated", "object", "other"),
        ]
        targets = [_field("subject_id", "int64", "clinical_events"),
                   _field("hadm_id", "int64", "clinical_events")]
        candidates = build_candidates(sources, targets)
        selected = select_mappings(candidates, threshold=0.5)
        pairs = {(row.target_field, row.source_field) for row in selected.itertuples()}
        self.assertIn(("subject_id", "subject_id"), pairs)
        self.assertIn(("hadm_id", "hadm_id"), pairs)

    def test_candidates_have_ranks(self) -> None:
        candidates = build_candidates([_field("a", "int64")], [_field("a", "int64", "tgt")])
        self.assertEqual(candidates["rank"].tolist(), [1])


class EvaluateTest(unittest.TestCase):
    def test_perfect_match(self) -> None:
        field_map = pd.DataFrame(
            [{"dataset_table": "clinical_events", "dataset_field": "subject_id",
              "source_table": "patients", "source_field": "subject_id", "dtype": "int64"}]
        )
        candidates = pd.DataFrame(
            [{"target_table": "clinical_events", "target_field": "subject_id",
              "source_table": "patients", "source_field": "subject_id", "score": 1.0, "rank": 1}]
        )
        selected = pd.DataFrame(
            [{"target_table": "clinical_events", "target_field": "subject_id",
              "source_table": "patients", "source_field": "subject_id",
              "score": 1.0, "method": "matcher", "shared_source": False}]
        )
        report = evaluate(selected, candidates, field_map)
        self.assertEqual(report["precision"], 1.0)
        self.assertEqual(report["recall"], 1.0)

    def test_difference_is_recorded(self) -> None:
        field_map = pd.DataFrame(
            [{"dataset_table": "clinical_events", "dataset_field": "subject_id",
              "source_table": "patients", "source_field": "subject_id", "dtype": "int64"}]
        )
        candidates = pd.DataFrame(
            [
                {"target_table": "clinical_events", "target_field": "subject_id",
                 "source_table": "admissions", "source_field": "subject_id", "score": 0.9, "rank": 1},
                {"target_table": "clinical_events", "target_field": "subject_id",
                 "source_table": "patients", "source_field": "subject_id", "score": 0.8, "rank": 2},
            ]
        )
        selected = pd.DataFrame(
            [{"target_table": "clinical_events", "target_field": "subject_id",
              "source_table": "admissions", "source_field": "subject_id",
              "score": 0.9, "method": "matcher", "shared_source": False}]
        )
        report = evaluate(selected, candidates, field_map)
        self.assertEqual(report["precision"], 0.0)
        self.assertEqual(len(report["differences"]), 1)


class AlignmentStageTest(unittest.TestCase):
    def test_report_on_synthetic_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mimic = write_synthetic_mimic(root)
            hosp = mimic / "hosp"
            tables = {
                "patients": pd.read_csv(hosp / "patients.csv.gz"),
                "admissions": pd.read_csv(hosp / "admissions.csv.gz"),
                "diagnoses_icd": pd.read_csv(hosp / "diagnoses_icd.csv.gz"),
                "prescriptions": pd.read_csv(hosp / "prescriptions.csv.gz"),
            }
            sources = extract_source_fields(tables)
            field_map = _field_map()
            from governance.alignment.schema import targets_from_gold

            candidates = build_candidates(sources, targets_from_gold(field_map))
            selected = select_mappings(candidates)
            report = evaluate(selected, candidates, field_map)
            self.assertGreater(report["predicted_pairs"], 0)
            self.assertGreaterEqual(report["precision"], 0.0)
            self.assertLessEqual(report["precision"], 1.0)
            self.assertGreaterEqual(report["top3_recall"], report["recall"])


if __name__ == "__main__":
    unittest.main()
