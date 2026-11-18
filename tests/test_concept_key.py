"""概念主键与图谱/标签键空间一致性测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.concept_key import ConceptResolver, concept_object_id, has_concept_cui
from governance.graph.build import build_concept_layer, build_graph
from governance.labels import compute_labels
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


def _diagnosis_events(notes: pd.DataFrame) -> pd.DataFrame:
    """最小事件表：只为诊断造事件，用来驱动标签阶段的概念对象。"""
    return pd.DataFrame(
        [
            {
                "record_id": "DX:1:11:ICD10:1",
                "subject_id": 1,
                "hadm_id": 11,
                "stay_id": None,
                "event_type": "Diagnosis",
                "event_time": None,
                "code_system": "ICD10",
                "code": "I10",
                "display": "Essential (primary) hypertension",
                "numeric_value": None,
                "text_value": None,
                "icd_chapter": "Diseases of the circulatory system",
                "source_table": "diagnoses_icd",
                "source_record_id": "diagnoses_icd:0",
                "split": "train",
                "sensitivity": "L2",
            }
        ]
    )


class ConceptKeyTest(unittest.TestCase):
    def test_cui_wins_over_code(self) -> None:
        self.assertEqual(concept_object_id("ICD10", "I10", "C001"), "Concept:C001")

    def test_code_key_without_cui(self) -> None:
        self.assertEqual(concept_object_id("ICD10", "I10"), "Concept:ICD10:I10")

    def test_blank_cui_falls_back_to_code(self) -> None:
        for blank in (None, "", "nan", "NaN", " None "):
            self.assertEqual(concept_object_id("ICD9", "4019", blank), "Concept:ICD9:4019")

    def test_has_concept_cui(self) -> None:
        self.assertTrue(has_concept_cui("C001"))
        self.assertFalse(has_concept_cui(" nan "))


class LabelGraphKeyAlignmentTest(unittest.TestCase):
    """标签的 Concept 对象必须是图谱里真实存在的节点（键完全一致）。"""

    def test_concept_labels_are_graph_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tables, notes = _fixture(Path(tmp))
            graph = build_concept_layer(build_graph(tables, notes=notes), None)
            events = _diagnosis_events(notes)
            labels = compute_labels(tables, notes=notes, events=events, graph=graph)

            graph_ids = set(
                graph.nodes.loc[graph.nodes["object_type"] == "Concept", "object_id"]
            )
            label_ids = set(
                labels.loc[labels["object_type"] == "Concept", "object_id"]
            )
            self.assertTrue(graph_ids)
            self.assertTrue(label_ids)
            self.assertTrue(
                label_ids <= graph_ids,
                msg=f"标签造出了图谱没有的概念对象: {sorted(label_ids - graph_ids)}",
            )


class ResolverTest(unittest.TestCase):
    """事件编码 → UMLS 概念：诊断/操作/处方各有各的体系名，必须统一到一处解析。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.map = pd.DataFrame(
            [
                {"source_system": "ICD10", "source_code": "I10", "cui": "C001",
                 "concept_name": "Essential hypertension", "sty": "Disease or Syndrome",
                 "confidence": 1.0, "method": "icd_exact"},
                {"source_system": "ICD9", "source_code": "4019", "cui": "C001",
                 "concept_name": "Essential hypertension", "sty": "Disease or Syndrome",
                 "confidence": 1.0, "method": "icd_exact"},
                {"source_system": "ICD10PCS", "source_code": "0BH17EZ", "cui": "C005",
                 "concept_name": "Insertion of endotracheal airway",
                 "sty": "Therapeutic or Preventive Procedure", "confidence": 1.0,
                 "method": "procedure_exact"},
                {"source_system": "ICD9CM_PROC", "source_code": "3893", "cui": "C006",
                 "concept_name": "Venous catheterization",
                 "sty": "Therapeutic or Preventive Procedure", "confidence": 1.0,
                 "method": "procedure_exact"},
                {"source_system": "RXNORM", "source_code": "Aspirin", "cui": "C003",
                 "concept_name": "Aspirin", "sty": "Pharmacologic Substance",
                 "confidence": 1.0, "method": "term_exact"},
            ]
        )

    def test_diagnosis_uses_icd_version_system(self) -> None:
        resolver = ConceptResolver(self.map)
        self.assertEqual(resolver.resolve("Diagnosis", "ICD10", "I10").cui, "C001")
        self.assertEqual(resolver.resolve("Diagnosis", "ICD9", "4019").cui, "C001")

    def test_procedure_uses_pcs_system(self) -> None:
        """事件里操作也叫 ICD10/ICD9，但概念表里是 ICD10PCS/ICD9CM_PROC。"""
        resolver = ConceptResolver(self.map)
        self.assertEqual(resolver.resolve("Procedure", "ICD10", "0BH17EZ").cui, "C005")
        self.assertEqual(resolver.resolve("Procedure", "ICD9", "3893").cui, "C006")

    def test_prescription_falls_back_to_drug_name(self) -> None:
        resolver = ConceptResolver(self.map)
        self.assertEqual(
            resolver.resolve("Prescription", "MIMIC_FORMULARY", "ASPIRIN", "Aspirin").cui,
            "C003",
        )
        self.assertIsNone(
            resolver.resolve("Prescription", "MIMIC_FORMULARY", "NOT_A_CODE", "Not a drug")
        )

    def test_unmapped_returns_none(self) -> None:
        resolver = ConceptResolver(self.map)
        self.assertIsNone(resolver.resolve("Diagnosis", "ICD10", "ZZZ"))
        self.assertIsNone(resolver.resolve("ClinicalNote", "MIMIC_NOTE", "DISCHARGE"))

    def test_empty_map_disables_resolution(self) -> None:
        resolver = ConceptResolver(None)
        self.assertFalse(resolver.enabled)
        self.assertIsNone(resolver.resolve("Diagnosis", "ICD10", "I10"))


if __name__ == "__main__":
    unittest.main()
