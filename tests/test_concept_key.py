"""概念主键与图谱/标签键空间一致性测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.concept_key import concept_object_id, has_concept_cui
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


if __name__ == "__main__":
    unittest.main()
