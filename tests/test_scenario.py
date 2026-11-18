"""业务场景脚本的编码口径测试（不依赖真实数据）。"""

from __future__ import annotations

import unittest

from scripts.scenario_hf_readmission import is_heart_failure


class HeartFailureCodeTest(unittest.TestCase):
    def test_icd9_428(self) -> None:
        self.assertTrue(is_heart_failure("ICD9", "4280"))
        self.assertTrue(is_heart_failure("ICD9", "428.0"))
        self.assertFalse(is_heart_failure("ICD9", "42731"))

    def test_icd10_i50(self) -> None:
        self.assertTrue(is_heart_failure("ICD10", "I5020"))
        self.assertTrue(is_heart_failure("ICD10", "i509"))
        self.assertFalse(is_heart_failure("ICD10", "I2510"))

    def test_other_system_or_empty(self) -> None:
        self.assertFalse(is_heart_failure("LNC", "4280"))
        self.assertFalse(is_heart_failure("ICD10", ""))


if __name__ == "__main__":
    unittest.main()
