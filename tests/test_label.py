"""标签体系测试：ICD 章节映射与敏感级别。"""

from __future__ import annotations

import unittest

from governance.icd import icd_chapter
from governance.label import sensitivity_for


class Icd10ChapterTest(unittest.TestCase):
    def check(self, code: str, expected: str) -> None:
        self.assertEqual(icd_chapter(10, code), expected)

    def test_infectious_and_neoplasms(self) -> None:
        self.check("A09", "Certain infectious and parasitic diseases")
        self.check("B20", "Certain infectious and parasitic diseases")
        self.check("C50", "Neoplasms")
        self.check("D64", "Neoplasms")

    def test_h_block_splits_eye_and_ear(self) -> None:
        self.check("H25", "Diseases of the eye and adnexa")
        self.check("H60", "Diseases of the ear and mastoid process")

    def test_circulatory_digestive_injury_factors(self) -> None:
        self.check("I10", "Diseases of the circulatory system")
        self.check("K35", "Diseases of the digestive system")
        self.check("S72", "Injury, poisoning and external causes")
        self.check("Z00", "Factors influencing health status")

    def test_normalisation_and_fallbacks(self) -> None:
        self.check(" i10 ", "Diseases of the circulatory system")
        self.assertEqual(icd_chapter(10, ""), "Unknown")
        self.assertEqual(icd_chapter(10, "!!"), "Other / unclassified")


class Icd9ChapterTest(unittest.TestCase):
    def test_numeric_ranges(self) -> None:
        self.assertEqual(icd_chapter(9, "4019"), "Diseases of the circulatory system")
        self.assertEqual(icd_chapter(9, "25000"), "Endocrine and metabolic diseases")
        self.assertEqual(icd_chapter(9, "7802"), "Symptoms and signs")
        self.assertEqual(icd_chapter(9, "8800"), "Injury and poisoning")

    def test_unknown(self) -> None:
        self.assertEqual(icd_chapter(9, "V3000"), "Unknown")
        self.assertEqual(icd_chapter(9, ""), "Unknown")


class SensitivityTest(unittest.TestCase):
    def test_object_levels(self) -> None:
        self.assertEqual(sensitivity_for("ClinicalNote"), "L3")
        self.assertEqual(sensitivity_for("Diagnosis"), "L2")
        self.assertEqual(sensitivity_for("Procedure"), "L2")
        self.assertEqual(sensitivity_for("Prescription"), "L2")

    def test_unknown_defaults_to_l2(self) -> None:
        self.assertEqual(sensitivity_for("UnknownType"), "L2")


if __name__ == "__main__":
    unittest.main()
