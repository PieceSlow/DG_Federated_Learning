"""MEDS 规范适配层测试：schema 校验、编码描述、患者划分。"""

from __future__ import annotations

import hashlib
import unittest

import pyarrow as pa

from governance import meds


class SubjectSplitTest(unittest.TestCase):
    def test_matches_documented_hash_rule(self) -> None:
        for subject in (1, 42, 4242):
            bucket = int(hashlib.sha256(f"split-v1:{subject}".encode()).hexdigest(), 16) % 100
            expected = "train" if bucket < 70 else ("tuning" if bucket < 85 else "held_out")
            self.assertEqual(meds.subject_split(subject), expected)

    def test_stable_and_in_vocabulary(self) -> None:
        self.assertEqual(meds.subject_split(42), meds.subject_split(42))
        self.assertIn(meds.subject_split(42), {"train", "tuning", "held_out"})


class CodeDescriptionTest(unittest.TestCase):
    def test_gender(self) -> None:
        self.assertEqual(meds.code_description("GENDER//M", {}), ("Male", []))

    def test_note_section(self) -> None:
        self.assertEqual(
            meds.code_description("NOTE_SECTION//DISCHARGE_DIAGNOSIS", {}),
            ("Discharge Diagnosis", ["MIMIC_IV_NOTE"]),
        )

    def test_discharge_note(self) -> None:
        self.assertEqual(
            meds.code_description("NOTE//DISCHARGE", {}), ("Discharge summary note", [])
        )


class SchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = meds.load_schema()

    def test_event_table_matches_data_schema(self) -> None:
        table = pa.table(
            {
                "subject_id": pa.array([42], pa.int64()),
                "time": pa.array([None], pa.timestamp("us")),
                "code": pa.array(["GENDER//F"], pa.string()),
                "numeric_value": pa.array([None], pa.float32()),
                "text_value": pa.array([None], pa.large_string()),
            }
        )
        self.schema.DataSchema.validate(table)

    def test_codes_metadata_matches_schema(self) -> None:
        table = pa.table(
            {
                "code": pa.array(["GENDER//F"], pa.string()),
                "description": pa.array(["Female"], pa.string()),
                "parent_codes": pa.array([[]], pa.list_(pa.string())),
            }
        )
        self.schema.CodeMetadataSchema.validate(table)

    def test_wrong_dtype_is_rejected(self) -> None:
        table = pa.table(
            {
                "subject_id": pa.array([1], pa.int64()),
                "time": pa.array([None], pa.timestamp("us")),
                "code": pa.array(["A//B"], pa.string()),
                "numeric_value": pa.array([None], pa.float64()),
                "text_value": pa.array([None], pa.large_string()),
            }
        )
        with self.assertRaises(Exception):
            self.schema.DataSchema.validate(table)


class DatasetMetadataTest(unittest.TestCase):
    def test_metadata_declares_extension_columns(self) -> None:
        metadata = meds.dataset_metadata()
        self.assertEqual(metadata["meds_version"], "0.4")
        self.assertIn("source_record_id", metadata["raw_source_id_columns"])
        self.assertTrue(meds.MAPPING_VERSION.startswith("mimic-iv-3.1-to-meds"))


if __name__ == "__main__":
    unittest.main()
