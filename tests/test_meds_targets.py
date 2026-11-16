"""MEDS 目标接口配置测试。"""

from __future__ import annotations

import unittest

from governance.alignment.targets import assert_no_derived_targets, load_targets


class MedsTargetTest(unittest.TestCase):
    def test_loads_all_event_types(self) -> None:
        targets = load_targets()
        self.assertTrue({"DIAGNOSIS", "LAB", "MEDICATION", "NOTE", "ADMISSION"} <= set(targets))
        for target in targets.values():
            self.assertTrue(target.fields["subject_id"].required)
            self.assertTrue(target.fields["code"].required)

    def test_diagnosis_time_is_admittime(self) -> None:
        targets = load_targets()
        self.assertEqual(targets["DIAGNOSIS"].fields["time"].source_hint, "admissions.admittime")

    def test_no_derived_fields(self) -> None:
        targets = load_targets()
        assert_no_derived_targets(targets)
        all_names = {name for target in targets.values() for name in target.fields}
        self.assertFalse({"n_diagnosis", "total_los_days", "text_chars"} & all_names)
