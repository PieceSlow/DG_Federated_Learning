"""质量规则的纯逻辑测试：编码格式、问题记录上限与可追溯字段。"""

from __future__ import annotations

import unittest
from pathlib import Path

from governance.config import Settings
from governance.context import ISSUE_CAP_PER_RULE, GovernanceContext, require_state
from governance.meds import CODE_RE


def _ctx() -> GovernanceContext:
    settings = Settings(mimic_root=Path("m"), note_root=Path("n"), output=Path("out"))
    return GovernanceContext(settings=settings)


class CodeFormatTest(unittest.TestCase):
    def test_valid_codes(self) -> None:
        for code in (
            "GENDER//F",
            "DIAGNOSIS//ICD10//I10",
            "LAB//MIMIC_ITEM//50912//MG_DL",
            "MEDICATION//MIMIC_FORMULARY//ASPIRIN",
            "NOTE_SECTION//DISCHARGE_DIAGNOSIS",
        ):
            self.assertIsNotNone(CODE_RE.match(code), code)

    def test_invalid_codes(self) -> None:
        for code in ("", "gender//F", "GENDER/F", "GENDER//", "//F"):
            self.assertIsNone(CODE_RE.match(code), code)


class IssueCapTest(unittest.TestCase):
    def test_detail_is_capped_but_count_is_not(self) -> None:
        ctx = _ctx()
        for index in range(ISSUE_CAP_PER_RULE + 5):
            ctx.add_issue("DUPLICATE_EVENT", "warning", "labevents", f"第{index}条")
        self.assertEqual(len(ctx.issues), ISSUE_CAP_PER_RULE)
        self.assertEqual(ctx.issue_counts["DUPLICATE_EVENT|warning"], ISSUE_CAP_PER_RULE + 5)

    def test_issue_keeps_traceability(self) -> None:
        ctx = _ctx()
        ctx.add_issue(
            "OBJECT_ASSOCIATION",
            "error",
            "diagnoses_icd",
            "hadm_id 不在住院对象中",
            subject=42,
            record="diagnoses_icd:12",
        )
        self.assertEqual(
            ctx.issues[0],
            {
                "rule": "OBJECT_ASSOCIATION",
                "severity": "error",
                "source_table": "diagnoses_icd",
                "source_record_id": "diagnoses_icd:12",
                "subject_id": 42,
                "detail": "hadm_id 不在住院对象中",
            },
        )


class AuditTest(unittest.TestCase):
    def test_audit_records_stage_and_timestamp(self) -> None:
        ctx = _ctx()
        ctx.log("数据接入", "读取完成")
        self.assertEqual(ctx.audit[0]["stage"], "数据接入")
        self.assertIn("at", ctx.audit[0])


class RequireStateTest(unittest.TestCase):
    def test_missing_state_raises_with_hint(self) -> None:
        ctx = _ctx()
        with self.assertRaises(RuntimeError) as raised:
            require_state(ctx, "clinical_events")
        self.assertIn("clinical_events", str(raised.exception))

    def test_dataframe_emptiness_is_handled(self) -> None:
        import pandas as pd

        ctx = _ctx()
        ctx.clinical_events = pd.DataFrame({"a": [1]})
        require_state(ctx, "clinical_events")  # 不应抛错


if __name__ == "__main__":
    unittest.main()
