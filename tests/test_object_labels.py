"""对象级标签测试：事实/派生/画像规则、手动标签与存储。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.labels import (
    ObjectLabelStore,
    compute_labels,
    definitions_catalog,
    load_definitions,
)
from tests.synthetic import write_synthetic_mimic


def _fixture(tmp: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    mimic = write_synthetic_mimic(tmp)
    hosp, icu, note = mimic / "hosp", mimic / "icu", tmp / "note"
    tables = {
        "patients": pd.read_csv(hosp / "patients.csv.gz"),
        "admissions": pd.read_csv(hosp / "admissions.csv.gz"),
        "icustays": pd.read_csv(icu / "icustays.csv.gz"),
        "diagnoses_icd": pd.read_csv(hosp / "diagnoses_icd.csv.gz"),
        "procedures_icd": pd.read_csv(hosp / "procedures_icd.csv.gz"),
        "prescriptions": pd.read_csv(hosp / "prescriptions.csv.gz"),
    }
    notes = pd.read_csv(note / "discharge.csv.gz")
    return tables, notes


class DefinitionsTest(unittest.TestCase):
    def test_load_definitions(self) -> None:
        version, definitions = load_definitions()
        self.assertEqual(version, "1.0")
        keys = {item.key for item in definitions}
        self.assertTrue({"elderly", "long_stay", "high_followup_priority"} <= keys)
        catalog = definitions_catalog(definitions)
        self.assertEqual(len(catalog), len(definitions))


class ComputeLabelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tables, cls.notes = _fixture(Path(cls._tmp.name))
        cls.labels = compute_labels(cls.tables, notes=cls.notes)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _ids(self, key: str) -> set[str]:
        subset = self.labels[self.labels["key"] == key]
        return set(subset["object_id"])

    def test_fact_label_elderly(self) -> None:
        self.assertEqual(self._ids("elderly"), {"Patient:1", "Patient:2"})

    def test_derived_label_long_stay(self) -> None:
        self.assertEqual(self._ids("long_stay"), {"Encounter:12"})

    def test_derived_label_polypharmacy_empty(self) -> None:
        self.assertEqual(self._ids("polypharmacy"), set())

    def test_profile_label_followup(self) -> None:
        self.assertEqual(self._ids("high_followup_priority"), {"Encounter:12"})

    def test_icu_label(self) -> None:
        self.assertEqual(self._ids("icu_long_stay"), {"ICUStay:22"})

    def test_output_columns_and_values(self) -> None:
        self.assertEqual(
            list(self.labels.columns),
            ["object_type", "object_id", "key", "value", "label_type", "source", "version", "computed_at"],
        )
        self.assertTrue((self.labels["value"] == "true").all())
        self.assertTrue((self.labels["source"] == "rule").all())


class ManualLabelTest(unittest.TestCase):
    def test_manual_label_adds_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tables, notes = _fixture(root)
            manual = root / "manual.csv"
            manual.write_text(
                "object_type,object_id,key,value\n"
                "Patient,Patient:3,manual_followup,yes\n"
                "Patient,Patient:1,elderly,false\n",
                encoding="utf-8",
            )
            labels = compute_labels(tables, notes=notes, manual_path=manual)

            manual_rows = labels[labels["source"] == "manual"]
            self.assertEqual(len(manual_rows), 2)
            patient1 = labels[(labels["object_id"] == "Patient:1") & (labels["key"] == "elderly")]
            self.assertEqual(patient1["value"].tolist(), ["false"])
            self.assertEqual(patient1["source"].tolist(), ["manual"])

            elderly = labels[labels["key"] == "elderly"]
            self.assertEqual(len(elderly), 2)  # Patient:1（手动 false）+ Patient:2（自动 true）


class StoreTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tables, notes = _fixture(root)
            frame = compute_labels(tables, notes=notes)
            store = ObjectLabelStore(root / "labels")
            path = store.write(frame)
            self.assertTrue(path.exists())
            self.assertEqual(len(store.read()), len(frame))


if __name__ == "__main__":
    unittest.main()
