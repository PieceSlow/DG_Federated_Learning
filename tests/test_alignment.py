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
from governance.ingest import coerce_times
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

    def test_selection_is_per_target_and_source_table(self) -> None:
        """``subject_id`` 在每张源表都要留一条，否则下游按事件类型取字段会缺。"""
        sources = [
            _field("subject_id", "int64", "patients"),
            _field("subject_id", "int64", "diagnoses_icd"),
            _field("subject_id", "int64", "prescriptions"),
        ]
        targets = [_field("subject_id", "int64", "clinical_events")]
        selected = select_mappings(build_candidates(sources, targets), top_k=3)
        self.assertEqual(
            set(selected["source_table"]),
            {"patients", "diagnoses_icd", "prescriptions"},
        )

    def test_declared_field_survives_low_score(self) -> None:
        """目标 schema 声明过的字段是契约，不能因为字面不相似就被阈值否掉。"""
        sources = [_field("formulary_drug_cd", "object", "prescriptions")]
        targets = [_field("code", "object", "clinical_events")]
        declared = {("clinical_events", "code"): {"formulary_drug_cd"}}
        selected = select_mappings(
            build_candidates(sources, targets, allowed_tables={
                ("clinical_events", "code"): {"prescriptions"}
            }),
            threshold=0.5,
            declared=declared,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected["method"].tolist(), ["declared"])


def _is_string_like(series: pd.Series) -> bool:
    """字符串列判定要跨 pandas 版本：2.x 推断为 object，3.x 推断为 str。"""
    return series.dtype.kind in "OUS"


class SourceDtypeTest(unittest.TestCase):
    def test_time_columns_are_normalised(self) -> None:
        """源表时间列必须是 datetime，否则类型匹配器会把正确字段判为不兼容。"""
        frame = pd.DataFrame({"chartdate": ["2100-01-02"], "icd_code": ["X"]})
        coerce_times("procedures_icd", frame)
        self.assertEqual(str(frame["chartdate"].dtype)[:8], "datetime")
        # 非时间列不能被 coerce_times 改坏（pandas 2.x 与 3.x 的字符串 dtype 名称不同）
        self.assertTrue(_is_string_like(frame["icd_code"]))
        self.assertNotIn("datetime", str(frame["icd_code"].dtype))

    def test_unknown_table_is_untouched(self) -> None:
        frame = pd.DataFrame({"charttime": ["2100-01-02"]})
        coerce_times("not_a_table", frame)
        self.assertTrue(_is_string_like(frame["charttime"]))


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
