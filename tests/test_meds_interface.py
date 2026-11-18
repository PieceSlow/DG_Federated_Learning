"""MEDS 接口声明 × 实现 × 匹配结果 的一致性测试。

这一组测试守的是"接口不能再说一套做一套"：

* 声明里的模板占位符必须能在 ``uses`` 里找到源列；
* ``standardize`` 渲染编码时不得偷偷引用未声明的列；
* 事件流的命名空间、必填列要被 ``check_events`` 检查；
* 接口台账必须能从 ``field_level.csv`` 回填，声明要映射却没落地要报 ``unmapped``。
"""

from __future__ import annotations

import re
import unittest

import pandas as pd

from governance.alignment.crosswalk import build_meds_crosswalk, validate_crosswalk
from governance.alignment.interface import check_events, resolve_field
from governance.alignment.targets import CORE_FIELDS, declared_namespaces, load_targets
from governance.standardize import _render_code

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class DeclarationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = load_targets()

    def test_structured_events_declare_their_table(self) -> None:
        for event_type, target in self.targets.items():
            if target.extractor != "structured":
                continue
            self.assertTrue(target.table, msg=f"{event_type} 没声明源表")
            self.assertIn(target.table, target.source_tables)
            self.assertTrue(target.code_namespaces, msg=f"{event_type} 没声明命名空间")

    def test_template_placeholders_are_declared_in_uses(self) -> None:
        """模板里写 ``{列}``，``uses`` 里就必须有 ``表.列``，否则就是没声明来源。"""
        for event_type, target in self.targets.items():
            code = target.fields["code"]
            placeholders = {match.group(1).partition(":")[0] for match in PLACEHOLDER.finditer(code.template or "")}
            used = {use.partition(".")[2] for use in code.uses}
            self.assertEqual(
                placeholders - used,
                set(),
                msg=f"{event_type} 的编码模板引用了未声明的列: {sorted(placeholders - used)}",
            )

    def test_join_declares_on_column(self) -> None:
        """``join`` 必须用 ``on_column``：YAML 1.1 会把裸写 ``on:`` 解析成布尔 True。"""
        for event_type, target in self.targets.items():
            if not target.join:
                continue
            self.assertIn("on_column", target.join, msg=f"{event_type} 的 join 缺少 on_column")
            self.assertIn("table", target.join)
            self.assertTrue(target.join.get("columns"), msg=f"{event_type} 的 join 没声明列")
            self.assertIn(target.join["table"], target.source_tables)

    def test_time_via_matches_declared_join(self) -> None:
        for event_type, target in self.targets.items():
            field = target.fields["time"]
            if field.via:
                self.assertEqual(field.via, target.join.get("table"), msg=f"{event_type}.time.via")
                self.assertIn(field.column, target.join.get("columns", ()), msg=f"{event_type}.time.column")

    def test_required_fields_have_a_source(self) -> None:
        for event_type, target in self.targets.items():
            for name, field in target.fields.items():
                if field.required:
                    self.assertNotEqual(field.source, "none", msg=f"{event_type}.{name}")

    def test_core_fields_complete(self) -> None:
        for event_type, target in self.targets.items():
            self.assertEqual(set(target.fields), set(CORE_FIELDS), msg=event_type)

    def test_declared_namespaces_cover_note_sub_namespaces(self) -> None:
        namespaces = declared_namespaces(self.targets)
        self.assertTrue({"NOTE", "NOTE_SECTION", "NOTE_MEDICATION", "NOTE_DIAGNOSIS"} <= namespaces)

    def test_source_gaps_are_written_down(self) -> None:
        from governance.alignment.targets import source_gaps

        gaps = source_gaps()
        self.assertIn("procedures_icd", gaps)
        self.assertTrue(gaps["procedures_icd"].strip())


class DeclaredColumnGuardTest(unittest.TestCase):
    def test_missing_declared_column_raises(self) -> None:
        """声明引用了已接入表里不存在的列 → 直接报错，不能静默发空值。"""
        from governance.standardize import _check_declared_columns

        tables = {
            "diagnoses_icd": pd.DataFrame(
                {"subject_id": [1], "hadm_id": [10], "icd_code": ["I10"], "icd_version": [10]}
            ),
            "admissions": pd.DataFrame({"hadm_id": [10]}),  # 缺 admittime
        }
        with self.assertRaises(KeyError) as ctx:
            _check_declared_columns(load_targets(), tables)
        self.assertIn("admittime", str(ctx.exception))

    def test_declared_columns_present_passes(self) -> None:
        from governance.standardize import _check_declared_columns

        tables = {
            "diagnoses_icd": pd.DataFrame(
                {"subject_id": [1], "hadm_id": [10], "icd_code": ["I10"], "icd_version": [10]}
            ),
            "admissions": pd.DataFrame(
                {
                    "subject_id": [1],
                    "hadm_id": [10],
                    "admittime": ["2100-01-01"],
                    "dischtime": ["2100-01-03"],
                }
            ),
        }
        _check_declared_columns(load_targets(), tables)  # 不抛异常即可


class RenderCodeTest(unittest.TestCase):
    def test_transforms(self) -> None:
        row = pd.Series(
            {
                "icd_version": "10.0",
                "itemid": 501,
                "valueuom": "mg/dL",
                "formulary_drug_cd": "NS 0.9%",
                "first_careunit": "MICU 1",
            }
        )
        self.assertEqual(_render_code("LAB//MIMIC_ITEM//{itemid:int}//{valueuom:unit}", row, "LAB"),
                         "LAB//MIMIC_ITEM//501//mg_dL")
        self.assertEqual(_render_code("DIAGNOSIS//ICD{icd_version:int}//{itemid:int}", row, "DIAGNOSIS"),
                         "DIAGNOSIS//ICD10//501")
        self.assertEqual(
            _render_code("MEDICATION//MIMIC_FORMULARY//{formulary_drug_cd:drug}", row, "MEDICATION"),
            "MEDICATION//MIMIC_FORMULARY//NS_0.9_",
        )
        self.assertEqual(_render_code("ICU_STAY//{first_careunit:unit}", row, "ICU_STAY"),
                         "ICU_STAY//MICU_1")

    def test_unknown_column_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            _render_code("GENDER//{not_a_column}", pd.Series({"gender": "M"}), "GENDER")


class CheckEventsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = load_targets()

    def _frame(self, **overrides) -> pd.DataFrame:
        base = {
            "subject_id": 1,
            "time": pd.Timestamp("2100-01-01"),
            "code": "DIAGNOSIS//ICD10//I10",
            "numeric_value": None,
            "text_value": None,
        }
        base.update(overrides)
        return pd.DataFrame([base])

    def test_declared_namespace_passes(self) -> None:
        report = check_events(self._frame(), self.targets)
        self.assertEqual(report["undeclared"], [])
        self.assertEqual(report["required_violations"], [])

    def test_undeclared_namespace_is_reported(self) -> None:
        report = check_events(self._frame(code="FOO//BAR"), self.targets)
        self.assertEqual(report["undeclared"], ["FOO"])

    def test_required_column_null_is_reported(self) -> None:
        report = check_events(self._frame(time=None), self.targets)
        violations = {(item["event_type"], item["column"]) for item in report["required_violations"]}
        self.assertIn(("DIAGNOSIS", "time"), violations)

    def test_empty_events_report_all_declared(self) -> None:
        report = check_events([], self.targets)
        self.assertEqual(report["observed_namespaces"], [])
        self.assertEqual(sorted(report["declared_without_events"]), sorted(declared_namespaces(self.targets)))


class ResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = load_targets()

    def _index(self, field_map: pd.DataFrame):
        from governance.alignment.interface import mapping_index

        return mapping_index(field_map)

    def _field_map(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows).assign(
            target_table="clinical_events",
            method="matcher",
        )

    def test_mapped_field_is_backfilled(self) -> None:
        field_map = self._field_map(
            [
                {"target_field": "code", "source_table": "prescriptions",
                 "source_field": "formulary_drug_cd", "score": 0.43, "status": "review"},
            ]
        )
        index = self._index(field_map)
        row = resolve_field(self.targets["MEDICATION"], "code", index)
        self.assertEqual(row["resolved_by"], "mapping")
        self.assertEqual(row["matched_source_table"], "prescriptions")
        self.assertEqual(row["mapping_status"], "review")

    def test_declared_but_absent_is_unmapped(self) -> None:
        """声明要由匹配提供、但匹配结果里没有 → 必须记 unmapped，而不是当成 none。"""
        field_map = self._field_map(
            [
                {"target_field": "code", "source_table": "diagnoses_icd",
                 "source_field": "icd_code", "score": 0.9, "status": "auto"},
            ]
        )
        index = self._index(field_map)
        row = resolve_field(self.targets["MEDICATION"], "code", index)
        self.assertEqual(row["resolved_by"], "unmapped")

    def test_interface_owned_column_is_spec(self) -> None:
        """patients.gender 拼 GENDER//M 不在 clinical_events 的匹配范围里，算 spec 而不是 unmapped。"""
        index = self._index(pd.DataFrame(columns=["target_field", "source_table", "source_field"]))
        row = resolve_field(self.targets["GENDER"], "code", index)
        self.assertEqual(row["resolved_by"], "spec")

    def test_none_source_stays_none(self) -> None:
        index = self._index(None)
        row = resolve_field(self.targets["GENDER"], "time", index)
        self.assertEqual(row["resolved_by"], "none")


class CrosswalkValidationTest(unittest.TestCase):
    def test_mapping_without_matched_source_is_rejected(self) -> None:
        frame = build_meds_crosswalk(None)
        broken = frame.copy()
        target = broken.index[0]
        broken.loc[target, ["resolved_by", "matched_source_table", "matched_source_field"]] = [
            "mapping",
            "",
            "",
        ]
        with self.assertRaises(ValueError):
            validate_crosswalk(broken)

    def test_unknown_resolved_by_is_rejected(self) -> None:
        frame = build_meds_crosswalk(None)
        broken = frame.copy()
        broken.loc[broken.index[0], "resolved_by"] = "magic"
        with self.assertRaises(ValueError):
            validate_crosswalk(broken)


if __name__ == "__main__":
    unittest.main()
