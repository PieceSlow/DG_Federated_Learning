"""MEDS 接口台账测试：10 类事件 × 5 个核心字段，且回填匹配结果、不引入自造聚合。"""

from __future__ import annotations

import unittest

from governance.alignment.crosswalk import build_meds_crosswalk, validate_crosswalk
from governance.alignment.targets import CORE_FIELDS, load_targets


class CrosswalkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = build_meds_crosswalk()
        cls.targets = load_targets()

    def test_one_row_per_event_and_core_field(self) -> None:
        # 9 类结构化事件 + DEMOGRAPHIC（人门学锚定年龄，标准层确实会发）
        self.assertEqual(len(self.targets), 10)
        self.assertEqual(len(self.frame), len(self.targets) * len(CORE_FIELDS))
        for event_type in self.targets:
            subset = self.frame[self.frame["event_type"] == event_type]
            self.assertEqual(set(subset["target_field"]), set(CORE_FIELDS))

    def test_required_fields(self) -> None:
        for event_type in self.targets:
            subset = self.frame[self.frame["event_type"] == event_type]
            required = set(subset.loc[subset["required"], "target_field"])
            self.assertIn("subject_id", required)
            self.assertIn("code", required)

    def test_no_derived_aggregates(self) -> None:
        self.assertFalse(self.frame["target_field"].str.startswith("n_").any())
        self.assertNotIn("total_los_days", set(self.frame["target_field"]))

    def test_validate_crosswalk(self) -> None:
        validate_crosswalk(self.frame)

    def test_declaration_covers_every_emitted_namespace(self) -> None:
        """standardize 实际发出的命名空间必须都在声明里。"""
        from governance.alignment.targets import declared_namespaces

        namespaces = declared_namespaces(self.targets)
        for expected in ("GENDER", "DEMOGRAPHIC", "ADMISSION", "DISCHARGE", "ICU_STAY",
                         "ICU_DISCHARGE", "DIAGNOSIS", "LAB", "MEDICATION"):
            self.assertIn(expected, namespaces)
        for note_namespace in ("NOTE", "NOTE_SECTION", "NOTE_MEDICATION", "NOTE_DIAGNOSIS"):
            self.assertIn(note_namespace, namespaces)

    def test_mapping_rows_are_backfilled(self) -> None:
        """没有匹配结果时不能凭空出现 mapping；给了匹配结果要能回填到源字段。"""
        self.assertFalse((self.frame["resolved_by"] == "mapping").any())

        import pandas as pd

        field_map = pd.DataFrame(
            [
                {"target_table": "clinical_events", "target_field": "code",
                 "source_table": "diagnoses_icd", "source_field": "icd_code",
                 "score": 0.9, "status": "auto", "method": "matcher"},
            ]
        )
        frame = build_meds_crosswalk(field_map=field_map)
        row = frame[(frame["event_type"] == "DIAGNOSIS") & (frame["target_field"] == "code")].iloc[0]
        self.assertEqual(row["resolved_by"], "mapping")
        self.assertEqual(row["matched_source_field"], "icd_code")
        self.assertEqual(row["mapping_status"], "auto")


if __name__ == "__main__":
    unittest.main()
