"""模型输出键名修复测试：小模型常把契约字段写成逗号/驼峰/截断形式。"""

from __future__ import annotations

import unittest

from governance.agents.base import normalize_key, repair_contract_keys


class ContractKeyRepairTest(unittest.TestCase):
    def test_comma_variant_is_repaired(self) -> None:
        self.assertEqual(
            repair_contract_keys({"source,table": "patients"}),
            {"source_table": "patients"},
        )

    def test_space_and_camel_variants(self) -> None:
        for key in ("source table", "sourceTable", "Source_Table", "source-table"):
            self.assertEqual(normalize_key(key), "source_table")

    def test_nested_lists_are_repaired(self) -> None:
        raw = {"objects": [{"name": "Patient", "source,table": "patients"}]}
        self.assertEqual(
            repair_contract_keys(raw),
            {"objects": [{"name": "Patient", "source_table": "patients"}]},
        )

    def test_data_keys_are_left_alone(self) -> None:
        raw = {"mappings": {"clinical_events.code": "diagnoses_icd.icd_code"}}
        self.assertEqual(repair_contract_keys(raw), raw)

    def test_input_is_not_mutated(self) -> None:
        raw = {"source,table": "x"}
        repair_contract_keys(raw)
        self.assertEqual(raw, {"source,table": "x"})

    def test_canonical_keys_untouched(self) -> None:
        raw = {"source_table": "x", "target_field": "code", "from": "A", "to": "B"}
        counter: list[int] = [0]
        self.assertEqual(repair_contract_keys(raw, counter), raw)
        self.assertEqual(counter[0], 0)

    def test_truncated_key_is_repaired_by_alias(self) -> None:
        """实测小模型会输出 "source,"（截断成 source），归一后要能还原。"""
        for key in ("source,", "source", "Source"):
            self.assertEqual(
                repair_contract_keys({key: "patients"}), {"source_table": "patients"}
            )

    def test_alias_does_not_override_existing_key(self) -> None:
        raw = {"source": "wrong", "source_table": "patients"}
        self.assertEqual(repair_contract_keys(raw), raw)


if __name__ == "__main__":
    unittest.main()
