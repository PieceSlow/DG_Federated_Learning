"""端到端集成测试：用合成 MIMIC 小样本跑完整条治理链路。

真实 MIMIC 数据受授权约束、不能进仓库，所以用 ``tests/synthetic.py`` 造一份同构的小数据，
验证：11 个阶段能串起来、产物齐全、MEDS schema 通过、质量规则确实被触发。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from governance import pipeline
from governance.config import Settings
from tests.synthetic import write_synthetic_mimic


class PipelineIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        mimic = write_synthetic_mimic(root)
        cls.settings = Settings(
            mimic_root=mimic,
            note_root=root,
            output=root / "out",
            cohort_size=3,
        )
        cls.ctx = pipeline.run(cls.settings)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # ---- 阶段与产物 ---------------------------------------------------------
    def test_all_stages_ran(self) -> None:
        keys = [stage.key for stage in pipeline.STAGES]
        self.assertEqual([key for key, _ in self.ctx.stage_timings], keys)

    def test_event_store_written(self) -> None:
        store = self.settings.eventstore
        events = pq.read_table(store / "data" / "events.parquet")
        self.assertGreater(events.num_rows, 0)
        self.assertEqual(events.column_names[:5], ["subject_id", "time", "code", "numeric_value", "text_value"])
        self.assertTrue((store / "metadata" / "codes.parquet").exists())
        splits = pq.read_table(store / "metadata" / "subject_splits.parquet").to_pandas()
        self.assertEqual(len(splits), 3)
        self.assertTrue(set(splits["split"]) <= {"train", "tuning", "held_out"})
        metadata = json.loads((store / "metadata" / "dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["meds_version"], "0.4")

    def test_governance_datasets(self) -> None:
        base = self.settings.governance / "dataset"
        events = pq.read_table(base / "clinical_events.parquet").to_pandas()
        p360 = pq.read_table(base / "patient_360.parquet").to_pandas()
        # 4 诊断 + 2 操作 + 3 处方 + 2 文书
        self.assertEqual(len(events), 11)
        self.assertEqual(set(events["event_type"]), {"Diagnosis", "Procedure", "Prescription", "ClinicalNote"})
        self.assertEqual(len(p360), 3)
        self.assertEqual(int(p360["n_diagnosis"].sum()), 4)
        self.assertEqual(int(p360["n_notes"].sum()), 2)

    def test_mappings_written(self) -> None:
        mapping = self.settings.governance / "mapping"
        field_map = pd.read_csv(mapping / "field_level.csv")
        meds_map = pd.read_csv(mapping / "field_level_meds.csv")
        record_map = pq.read_table(mapping / "record_level.parquet").to_pandas()
        self.assertGreater(len(field_map), 0)
        self.assertIn("status", field_map.columns)
        self.assertTrue((mapping / "derived_fields.csv").exists())
        # 11 类事件 × 5 个 MEDS 核心字段（含 DEMOGRAPHIC、PROCEDURE；NOTE 的 3 个子命名空间不单列）
        self.assertEqual(len(meds_map), 55)
        self.assertIn("resolved_by", meds_map.columns)
        self.assertIn("consumed", field_map.columns)
        self.assertEqual(len(record_map), 11 + 3)
        self.assertIn("derived:patient_360:", record_map["source_record_id"].iloc[-1])

    def test_object_model_and_catalog(self) -> None:
        metadata = self.settings.governance / "metadata"
        graph = json.loads((metadata / "object_graph.json").read_text(encoding="utf-8"))
        # 7 类 MIMIC 对象 + 1 类派生概念对象（无 UMLS 时为编码级降级节点）
        self.assertEqual(len(graph["objects"]), 8)
        self.assertIn("Concept", graph["objects"])
        self.assertEqual(len(graph["relations"]), 9)
        self.assertTrue((metadata / "object_inventory.csv").exists())
        self.assertTrue((metadata / "labels.json").exists())
        self.assertTrue((metadata / "data_source_inventory.csv").exists())

        cats = json.loads((self.settings.governance / "catalog" / "data_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(set(cats), {"resource", "asset", "service", "share", "open"})
        self.assertTrue((self.settings.governance / "catalog" / "data_catalog.md").exists())

    def test_graph_instances(self) -> None:
        graph_dir = self.settings.governance / "graph"
        nodes = pd.read_parquet(graph_dir / "nodes.parquet")
        manifest = json.loads((graph_dir / "graph_manifest.json").read_text(encoding="utf-8"))
        edge_files = sorted((graph_dir / "edges").glob("*.parquet"))
        # 6 条结构化关系 + 3 类概念边
        self.assertEqual(len(edge_files), 9)
        # 3 患者 + 3 住院 + 3 ICU + 4 诊断 + 3 处方 + 2 操作 + 2 文书 + 8 概念
        self.assertEqual(len(nodes), 28)
        self.assertEqual(sum(manifest["relations"].values()), 25)
        self.assertEqual(manifest["objects"]["ClinicalNote"], 2)
        self.assertGreater(manifest["objects"]["Concept"], 0)
        self.assertEqual(self.ctx.stats["graph_issues"], 0)

    def test_text_assertion_and_consistency(self) -> None:
        events = pq.read_table(self.settings.eventstore / "data" / "events.parquet").to_pandas()
        self.assertIn("assertion", events.columns)
        self.assertTrue(set(events["assertion"].dropna()) <= {
            "present", "negated", "family", "historical", "hypothetical"})

        self.assertGreater(
            self.ctx.issue_counts.get("TEXT_DRUG_MISMATCH|warning", 0), 0)
        self.assertGreater(
            self.ctx.issue_counts.get("TEXT_SECTION_MISSING|warning", 0), 0)
        self.assertGreater(
            self.ctx.issue_counts.get("TEXT_CODE_MISMATCH|warning", 0), 0)

    def test_object_labels(self) -> None:
        frame = pd.read_parquet(self.settings.governance / "labels" / "object_labels.parquet")
        self.assertTrue(
            {"L2.elderly", "L2.long_stay", "L4.high_followup_priority", "L2.icu_long_stay"}
            <= set(frame["tag_key"])
        )
        self.assertTrue({"L1.sensitivity", "L2.entity_class"} <= set(frame["tag_key"]))
        labels = json.loads(
            (self.settings.governance / "metadata" / "labels.json").read_text(encoding="utf-8")
        )
        self.assertEqual(labels["object_labels"]["counts"]["L2.elderly"], 2)
        self.assertEqual(labels["object_labels"]["counts"]["L4.high_followup_priority"], 1)
        self.assertIn("L1", labels["label_system"]["layers"])
        self.assertTrue((self.settings.governance / "labels" / "tag_dictionary.csv").exists())
        # 标签挂的是对象 ID，与图谱节点一致
        self.assertIn("Encounter:12", set(frame["object_id"]))

    def test_sql_execution(self) -> None:
        sql_path = self.settings.governance / "mapping" / "build_clinical_events.sql"
        report_path = self.settings.governance / "quality" / "sql_execution_report.json"
        self.assertTrue(sql_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["rows"], 11)

    def test_classification(self) -> None:
        base = self.settings.governance / "classification"
        rules = pd.read_csv(base / "rules.csv")
        result = pd.read_csv(base / "result.csv")
        # C01–C07 原有 7 条 + C08（患者级视图）
        self.assertEqual(len(rules), 8)
        self.assertIn("L3", set(result["level"]))

    def test_patient_360_is_l3(self) -> None:
        """患者 360 是一人一行的患者级视图，不是聚合：只能有一个级别，且必须是 L3。"""
        result = pd.read_csv(self.settings.governance / "classification" / "result.csv")
        subset = result[result["dataset_table"] == "patient_360"]
        self.assertEqual(len(subset), 1)
        self.assertEqual(set(subset["level"]), {"L3"})
        labels = pd.read_parquet(self.settings.governance / "labels" / "object_labels.parquet")
        row = labels[
            (labels["object_type"] == "Dataset") & (labels["object_id"] == "Dataset:patient_360")
            & (labels["tag_key"] == "L1.sensitivity")
        ]
        self.assertEqual(set(row["tag_value"]), {"L3"})

    def test_catalog_levels_agree_with_classification(self) -> None:
        """编目不再自己定级：同一个表的级别必须和分级结果一致。"""
        result = pd.read_csv(self.settings.governance / "classification" / "result.csv")
        catalog = json.loads(
            (self.settings.governance / "catalog" / "data_catalog.json").read_text(encoding="utf-8")
        )
        assets = {item["name"]: item for item in catalog["asset"]}
        p360_level = "/".join(sorted(set(result.loc[result.dataset_table == "patient_360", "level"])))
        self.assertEqual(assets["患者 360 宽表 patient_360"]["level"], p360_level)
        ce_level = "/".join(sorted(set(result.loc[result.dataset_table == "clinical_events", "level"])))
        self.assertEqual(assets["临床事件整合表 clinical_events"]["level"], ce_level)

    def test_catalog_separates_field_and_record_level(self) -> None:
        catalog = json.loads(
            (self.settings.governance / "catalog" / "data_catalog.json").read_text(encoding="utf-8")
        )
        assets = {item["name"]: item for item in catalog["asset"]}
        self.assertEqual(assets["字段级映射 field_level"]["level"], "L1")
        self.assertEqual(assets["记录级血缘 record_level"]["level"], "L2")
        self.assertNotIn("字段级/记录级映射", assets)
        for item in catalog["asset"]:
            self.assertIn("unit", item, msg=item["name"])

    def test_share_dir_marks_text_stripping(self) -> None:
        """事件仓储里有出院小结原文，共享条目必须写明剥离文本。"""
        catalog = json.loads(
            (self.settings.governance / "catalog" / "data_catalog.json").read_text(encoding="utf-8")
        )
        names = [item["name"] for item in catalog["share"]]
        self.assertTrue(any("去文本" in name for name in names), msg=names)

    def test_tag_dictionary_counts_by_object(self) -> None:
        """同一个 tag_key 打在 Event/Patient/Dataset 上时，计数必须分开，不能都写总数。"""
        dictionary = pd.read_csv(self.settings.governance / "labels" / "tag_dictionary.csv")
        rows = dictionary[dictionary["tag_key"] == "L1.sensitivity"]
        counts = dict(zip(rows["object"], rows["count"]))
        self.assertEqual(counts, {"Event": 11, "Patient": 3, "Dataset": 3})

    def test_concept_coverage_reports_graph_ratio(self) -> None:
        coverage = json.loads(
            (self.settings.governance / "quality" / "label_coverage.json").read_text(encoding="utf-8")
        )
        concept = coverage["Concept"]
        self.assertIn("graph_ratio", concept)
        self.assertIn("graph_missing", concept)
        self.assertLessEqual(concept["graph_ratio"], 1.0)

    # ---- 质量规则确实被触发 -------------------------------------------------
    def test_issue_rules_triggered(self) -> None:
        self.assertEqual(self.ctx.issue_counts.get("CODE_MAPPING|warning"), 1)
        self.assertEqual(self.ctx.issue_counts.get("REQUIRED_FIELD|warning"), 1)
        self.assertEqual(self.ctx.issue_counts.get("CODE_NOT_IN_DICTIONARY|warning"), 1)
        self.assertEqual(
            sum(v for k, v in self.ctx.issue_counts.items() if k.endswith("|error")), 0
        )
        self.assertTrue((self.settings.governance / "quality" / "issues.csv").exists())

    def test_reports_and_audit(self) -> None:
        quality = self.settings.governance / "quality"
        stream = json.loads((quality / "event_stream.json").read_text(encoding="utf-8"))
        governed = json.loads((quality / "governed_dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(stream["cohort_size"], 3)
        self.assertEqual(governed["clinical_events"]["duplicate_record_id"], 0)
        self.assertEqual(governed["clinical_events"]["orphan_hadm"], 0)
        self.assertTrue(any("MEDS" in item for item in stream["validators"]))
        self.assertTrue((self.settings.audit_dir / "audit_log.csv").exists())
        summary = json.loads((self.settings.output / "run_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(summary["stages"]), len(pipeline.STAGES))


if __name__ == "__main__":
    unittest.main()
