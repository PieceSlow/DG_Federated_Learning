"""文本分支测试：出院小结章节切分与事件抽取（确定性规则）。"""

from __future__ import annotations

import unittest

from governance.text_extraction import (
    _drug_slug,
    _section_key,
    detect_assertion,
    extract_note_events,
    split_sections,
)

NOTE = """Chief Complaint: Chest pain.
History of Present Illness:
 65-year-old man with chest pain radiating to the left arm.
Discharge Diagnosis:
1. Acute myocardial infarction
2. Hypertension
Discharge Medications:
1. Aspirin 81 mg PO daily
2. Metoprolol 25 mg PO BID
"""


class SplitSectionsTest(unittest.TestCase):
    def test_known_headers_only(self) -> None:
        sections = split_sections(NOTE)
        self.assertEqual(
            list(sections),
            [
                "CHIEF COMPLAINT",
                "HISTORY OF PRESENT ILLNESS",
                "DISCHARGE DIAGNOSIS",
                "DISCHARGE MEDICATIONS",
            ],
        )
        self.assertEqual(sections["CHIEF COMPLAINT"], "Chest pain.")
        self.assertEqual(
            sections["DISCHARGE DIAGNOSIS"],
            "1. Acute myocardial infarction\n2. Hypertension",
        )

    def test_unknown_header_is_ignored(self) -> None:
        self.assertEqual(split_sections("Plan: admit to telemetry\n"), {})

    def test_empty_note(self) -> None:
        self.assertEqual(split_sections(""), {})

    def test_section_key(self) -> None:
        self.assertEqual(_section_key("History of Present Illness"), "HISTORY_OF_PRESENT_ILLNESS")


class ExtractNoteEventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events = extract_note_events(NOTE)
        self.codes = [event["code"] for event in self.events]

    def test_one_event_per_section(self) -> None:
        self.assertEqual(
            [code for code in self.codes if code.startswith("NOTE_SECTION//")],
            [
                "NOTE_SECTION//CHIEF_COMPLAINT",
                "NOTE_SECTION//HISTORY_OF_PRESENT_ILLNESS",
                "NOTE_SECTION//DISCHARGE_DIAGNOSIS",
                "NOTE_SECTION//DISCHARGE_MEDICATIONS",
            ],
        )

    def test_diagnosis_lines(self) -> None:
        diagnoses = [e for e in self.events if e["code"] == "NOTE_DIAGNOSIS//DISCHARGE"]
        self.assertEqual(
            [e["text_value"] for e in diagnoses],
            ["1. Acute myocardial infarction", "2. Hypertension"],
        )

    def test_medication_lines(self) -> None:
        meds = {e["code"]: e["text_value"] for e in self.events if e["code"].startswith("NOTE_MEDICATION//")}
        self.assertEqual(
            meds,
            {
                "NOTE_MEDICATION//ASPIRIN": "Aspirin 81 mg PO daily",
                "NOTE_MEDICATION//METOPROLOL": "Metoprolol 25 mg PO BID",
            },
        )

    def test_medication_limit(self) -> None:
        limited = extract_note_events(NOTE, max_medications=1)
        self.assertEqual(
            len([e for e in limited if e["code"].startswith("NOTE_MEDICATION//")]), 1
        )

    def test_empty_note(self) -> None:
        self.assertEqual(extract_note_events(""), [])

    def test_medication_text_length_capped(self) -> None:
        note = "Discharge Medications:\n1. " + "Aspirin " * 200 + "81 mg PO daily\n"
        meds = [
            e for e in extract_note_events(note) if e["code"].startswith("NOTE_MEDICATION//")
        ]
        self.assertTrue(meds)
        for event in meds:
            self.assertLessEqual(len(event["text_value"]), 500)


class AssertionTest(unittest.TestCase):
    def test_negated(self) -> None:
        for text in ("No chest pain", "Patient denies fever", "Without dyspnea", "Negative for influenza"):
            self.assertEqual(detect_assertion(text), "negated", text)

    def test_family(self) -> None:
        for text in ("Family history of diabetes", "Mother with hypertension"):
            self.assertEqual(detect_assertion(text), "family", text)

    def test_historical(self) -> None:
        for text in ("History of MI", "Prior stroke"):
            self.assertEqual(detect_assertion(text), "historical", text)

    def test_hypothetical(self) -> None:
        for text in ("If fever develops", "Rule out pneumonia"):
            self.assertEqual(detect_assertion(text), "hypothetical", text)

    def test_present(self) -> None:
        self.assertEqual(detect_assertion("Chest pain"), "present")

    def test_events_carry_assertion(self) -> None:
        note = (
            "Discharge Diagnosis:\n"
            "1. Acute myocardial infarction\n"
            "2. No pneumonia\n"
            "Discharge Medications:\n"
            "1. Aspirin 81 mg PO daily\n"
        )
        events = extract_note_events(note)
        self.assertTrue(all("assertion" in event for event in events))
        diagnoses = [event for event in events if event["code"] == "NOTE_DIAGNOSIS//DISCHARGE"]
        self.assertEqual(diagnoses[1]["assertion"], "negated")


class DrugSlugTest(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(_drug_slug("metoprolol succinate"), "METOPROLOL_SUCCINATE")
        self.assertEqual(_drug_slug("  "), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
