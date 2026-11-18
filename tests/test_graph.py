"""图谱测试：对象模型、实例化、边表存储与质量规则。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.graph.build import build_concept_layer, build_graph, make_object_id
from governance.graph.model import load_model
from governance.graph.store import GraphStore
from governance.graph.verify import verify


def _tables() -> dict[str, pd.DataFrame]:
    return {
        "patients": pd.DataFrame(
            {"subject_id": [1], "gender": ["F"], "anchor_age": [65], "anchor_year": [2150]}
        ),
        "admissions": pd.DataFrame(
            {
                "subject_id": [1],
                "hadm_id": [11],
                "admittime": ["2100-01-01 08:00:00"],
                "dischtime": ["2100-01-05 12:00:00"],
                "admission_type": ["EMERGENCY"],
                "hospital_expire_flag": [0],
            }
        ),
        "icustays": pd.DataFrame(
            {
                "subject_id": [1],
                "hadm_id": [11],
                "stay_id": [21],
                "first_careunit": ["MICU"],
                "last_careunit": ["MICU"],
                "intime": ["2100-01-01 09:00:00"],
                "outtime": ["2100-01-03 09:00:00"],
            }
        ),
        "diagnoses_icd": pd.DataFrame(
            {"subject_id": [1], "hadm_id": [11], "seq_num": [1], "icd_code": ["I10"], "icd_version": [10]}
        ),
        "procedures_icd": pd.DataFrame(
            {
                "subject_id": [1],
                "hadm_id": [11],
                "seq_num": [1],
                "icd_code": ["0BH17EZ"],
                "icd_version": [10],
                "chartdate": ["2100-01-02"],
            }
        ),
        "prescriptions": pd.DataFrame(
            {
                "subject_id": [1],
                "hadm_id": [11],
                "pharmacy_id": [501],
                "starttime": ["2100-01-02 10:00:00"],
                "drug": ["Aspirin"],
                "formulary_drug_cd": ["ASPIRIN"],
                "dose_val_rx": ["81"],
            }
        ),
    }


def _tables_two() -> dict[str, pd.DataFrame]:
    tables = _tables()
    tables["patients"] = pd.concat(
        [tables["patients"], pd.DataFrame(
            {"subject_id": [2], "gender": ["M"], "anchor_age": [70], "anchor_year": [2150]}
        )], ignore_index=True)
    tables["admissions"] = pd.concat(
        [tables["admissions"], pd.DataFrame({
            "subject_id": [2], "hadm_id": [12],
            "admittime": ["2100-02-01 08:00:00"], "dischtime": ["2100-02-03 08:00:00"],
            "admission_type": ["URGENT"], "hospital_expire_flag": [0],
        })], ignore_index=True)
    tables["icustays"] = pd.concat(
        [tables["icustays"], pd.DataFrame({
            "subject_id": [2], "hadm_id": [12], "stay_id": [22],
            "first_careunit": ["SICU"], "last_careunit": ["SICU"],
            "intime": ["2100-02-01 09:00:00"], "outtime": ["2100-02-02 09:00:00"],
        })], ignore_index=True)
    tables["diagnoses_icd"] = pd.concat(
        [tables["diagnoses_icd"], pd.DataFrame({
            "subject_id": [2], "hadm_id": [12], "seq_num": [1],
            "icd_code": ["E119"], "icd_version": [10],
        })], ignore_index=True)
    tables["procedures_icd"] = pd.concat(
        [tables["procedures_icd"], pd.DataFrame({
            "subject_id": [2], "hadm_id": [12], "seq_num": [1],
            "icd_code": ["3893"], "icd_version": [9], "chartdate": ["2100-02-02"],
        })], ignore_index=True)
    tables["prescriptions"] = pd.concat(
        [tables["prescriptions"], pd.DataFrame({
            "subject_id": [2], "hadm_id": [12], "pharmacy_id": [502],
            "starttime": ["2100-02-02 10:00:00"], "drug": ["Metoprolol"],
            "formulary_drug_cd": ["METOPROLOL"], "dose_val_rx": ["25"],
        })], ignore_index=True)
    return tables


def _notes_two() -> pd.DataFrame:
    return pd.concat(
        [_notes(), pd.DataFrame({
            "note_id": ["902"], "subject_id": [2], "hadm_id": [12],
            "charttime": ["2100-02-03 10:00:00"], "note_type": ["DS"],
        })], ignore_index=True)


def _notes(hadm_id: int | None = 11) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "note_id": ["901"],
            "subject_id": [1],
            "hadm_id": [hadm_id],
            "charttime": ["2100-01-05 10:00:00"],
            "note_type": ["DS"],
        }
    )


class ModelTest(unittest.TestCase):
    def test_three_layers_and_relations(self) -> None:
        model = load_model()
        self.assertEqual(len(model.objects), 7)
        self.assertEqual(len(model.relations), 6)
        for spec in model.objects.values():
            self.assertTrue(spec.keys, spec.name)
            self.assertTrue(spec.source_table, spec.name)
            self.assertIn(spec.sensitivity, {"L2", "L3"}, spec.name)
        for relation in model.relations:
            self.assertIn(relation.on, model.objects[relation.from_object].keys)

    def test_object_id_is_stable(self) -> None:
        self.assertEqual(make_object_id("Patient", [1]), "Patient:1")
        self.assertEqual(make_object_id("Diagnosis", [1, 11, 2]), "Diagnosis:1:11:2")
        self.assertIsNone(make_object_id("Encounter", [None]))


class BuildTest(unittest.TestCase):
    def test_node_and_edge_counts(self) -> None:
        graph = build_graph(_tables(), _notes())
        self.assertEqual(len(graph.nodes), 7)  # 1 patient + 1 encounter + 1 icu + 1 dx + 1 px + 1 rx + 1 note
        self.assertEqual(graph.manifest["objects"]["Patient"], 1)
        self.assertEqual(graph.manifest["objects"]["ClinicalNote"], 1)
        self.assertEqual(sum(graph.manifest["relations"].values()), 6)
        for frame in graph.edges.values():
            self.assertIn("evidence", frame.columns)
            self.assertTrue((frame["confidence"] == 1.0).all())

    def test_missing_pharmacy_id_is_not_a_node(self) -> None:
        tables = _tables()
        tables["prescriptions"]["pharmacy_id"] = [None]
        graph = build_graph(tables, _notes())
        self.assertEqual(len(graph.nodes[graph.nodes.object_type == "Prescription"]), 0)

    def test_notes_without_hadm_produce_no_edge(self) -> None:
        graph = build_graph(_tables(), _notes(hadm_id=None))
        self.assertEqual(len(graph.edges["has_note"]), 0)


class StoreTest(unittest.TestCase):
    def test_roundtrip_and_stale_edges_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = GraphStore(root)
            graph = build_graph(_tables(), _notes())
            store.write(graph)

            self.assertTrue(store.nodes_path.exists())
            self.assertEqual(len(store.read_nodes()), len(graph.nodes))
            self.assertEqual(len(store.read_edges("has_diagnosis")), 1)
            manifest = store.read_manifest()
            self.assertEqual(manifest["objects"]["Patient"], 1)

            # 重写时清掉不属于当前模型的旧文件；缺失的表对应空边表而非残留数据
            (store.edges_dir / "obsolete.parquet").write_bytes(b"x")
            tables = _tables()
            tables.pop("procedures_icd")
            store.write(build_graph(tables, _notes()))
            self.assertFalse((store.edges_dir / "obsolete.parquet").exists())
            self.assertEqual(len(store.read_edges("has_procedure")), 0)


class IncrementalStoreTest(unittest.TestCase):
    def _store(self, tmp: str) -> GraphStore:
        return GraphStore(Path(tmp))

    def test_full_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            graph = build_graph(_tables(), _notes())
            store.write(graph)
            first = store.read_nodes()
            store.write(graph)
            second = store.read_nodes()
            pd.testing.assert_frame_equal(first, second)
            history = store.read_history()
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["mode"], "full")

    def test_incremental_upsert_keeps_old_and_reports_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.write(build_graph(_tables(), _notes()))
            store.write(build_graph(_tables_two(), _notes_two()), mode="incremental")

            nodes = store.read_nodes()
            self.assertEqual(len(nodes), 14)
            self.assertIn("Patient:1", set(nodes["object_id"]))
            self.assertIn("Patient:2", set(nodes["object_id"]))
            manifest = store.read_manifest()
            self.assertEqual(manifest["mode"], "incremental")
            self.assertEqual(manifest["delta"]["nodes"]["added"], 7)
            self.assertEqual(manifest["delta"]["nodes"]["unchanged"], 7)
            self.assertEqual(manifest["delta"]["edges"]["added"], 6)
            self.assertEqual(len(store.read_history()), 2)

    def test_incremental_delete_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.write(build_graph(_tables_two(), _notes_two()))
            store.write(
                build_graph(_tables(), _notes()),
                mode="incremental",
                delete_missing=True,
            )
            nodes = store.read_nodes()
            self.assertEqual(len(nodes), 7)
            self.assertNotIn("Patient:2", set(nodes["object_id"]))
            manifest = store.read_manifest()
            self.assertGreater(manifest["delta"]["nodes"]["removed"], 0)

    def test_unknown_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._store(tmp).write(build_graph(_tables(), _notes()), mode="append")


class VerifyTest(unittest.TestCase):
    def test_clean_graph_has_no_issues(self) -> None:
        graph = build_graph(_tables(), _notes())
        self.assertEqual(verify(graph), [])

    def test_dangling_edge_detected(self) -> None:
        tables = _tables()
        tables["icustays"]["hadm_id"] = [99]  # 住院 99 不存在
        issues = verify(build_graph(tables, _notes()))
        rules = {issue["rule"] for issue in issues}
        self.assertIn("GRAPH_DANGLING_EDGE", rules)

    def test_missing_relation_for_note_without_hadm(self) -> None:
        issues = verify(build_graph(_tables(), _notes(hadm_id=None)))
        self.assertIn("GRAPH_MISSING_RELATION", {issue["rule"] for issue in issues})

    def test_temporal_conflict_detected(self) -> None:
        tables = _tables()
        tables["admissions"]["dischtime"] = ["2100-01-01 07:00:00"]  # 早于入院
        issues = verify(build_graph(tables, _notes()))
        self.assertIn("GRAPH_TEMPORAL_CONFLICT", {issue["rule"] for issue in issues})

    def test_icu_window_outside_encounter(self) -> None:
        tables = _tables()
        tables["icustays"]["outtime"] = ["2100-02-01 09:00:00"]  # 晚于出院 1 天以上
        issues = verify(build_graph(tables, _notes()))
        details = [issue for issue in issues if issue["rule"] == "GRAPH_TEMPORAL_CONFLICT"]
        self.assertTrue(any("ICU" in issue["detail"] for issue in details))


def _concept_map() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["ICD10", "I10", "", "C001", "Essential hypertension", "Disease or Syndrome", 1.0, "icd_exact"],
            ["RXNORM", "Aspirin", "Aspirin", "C003", "Aspirin", "Pharmacologic Substance", 1.0, "term_exact"],
            ["ICD10PCS", "0BH17EZ", "", "C005", "Insertion of endotracheal airway",
             "Therapeutic or Preventive Procedure", 1.0, "procedure_exact"],
        ],
        columns=["source_system", "source_code", "source_label", "cui",
                 "concept_name", "sty", "confidence", "method"],
    )


class ConceptLayerTest(unittest.TestCase):
    def test_adds_concept_nodes_and_edges(self) -> None:
        graph = build_concept_layer(build_graph(_tables(), _notes()), _concept_map())
        concepts = graph.nodes[graph.nodes.object_type == "Concept"]
        self.assertEqual(len(concepts), 3)
        self.assertEqual(graph.manifest["relations"]["diagnosis_concept"], 1)
        self.assertEqual(graph.manifest["relations"]["prescription_concept"], 1)
        self.assertEqual(graph.manifest["relations"]["procedure_concept"], 1)
        self.assertEqual(graph.manifest["objects"]["Concept"], 3)
        self.assertEqual(verify(graph), [])

    def test_no_concept_map_degrades_to_code_level_nodes(self) -> None:
        """没有 UMLS 时概念层必须降级存在；否则图谱有 0 个概念对象，标签却有概念标签。"""
        graph = build_concept_layer(build_graph(_tables(), _notes()), None)
        concepts = graph.nodes[graph.nodes.object_type == "Concept"]
        self.assertGreater(len(concepts), 0)
        self.assertTrue(all(str(item).startswith("Concept:") for item in concepts.object_id))
        self.assertEqual(graph.manifest["objects"]["Concept"], len(concepts))
        self.assertEqual(verify(graph), [])

    def test_empty_concept_map_degrades_like_missing_one(self) -> None:
        missing = build_concept_layer(build_graph(_tables(), _notes()), None)
        empty = build_concept_layer(build_graph(_tables(), _notes()), pd.DataFrame())
        self.assertEqual(
            set(missing.nodes["object_id"]), set(empty.nodes["object_id"])
        )

    def test_unmapped_codes_are_ignored_in_umls_mode(self) -> None:
        """有 UMLS 时只收已归一的编码，未归一的不造 CUI 节点。"""
        graph = build_concept_layer(build_graph(_tables(), _notes()), _concept_map())
        concepts = graph.nodes[graph.nodes.object_type == "Concept"]
        self.assertEqual(len(concepts), 3)
        self.assertTrue(all(str(item).startswith("Concept:C") for item in concepts.object_id))


if __name__ == "__main__":
    unittest.main()
