"""MEDS 字段交叉表测试：覆盖 9 类事件 × 5 个核心字段，且不引入自造聚合。"""

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
        self.assertEqual(len(self.targets), 9)
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


if __name__ == "__main__":
    unittest.main()
