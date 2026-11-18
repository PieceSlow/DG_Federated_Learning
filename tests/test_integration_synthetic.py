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
        self.assertEqual(len(meds_map), 45)
        self.assertEqual(len(record_map), 11 + 3)
        self.assertIn("derived:patient_360:", record_map["source_record_id"].iloc[-1])

    def test_object_model_and_catalog(self) -> None:
        metadata = self.settings.governance / "metadata"
        graph = json.loads((metadata / "object_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(len(graph["objects"]), 7)
        self.assertEqual(len(graph["relations"]), 6)
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
        self.assertEqual(len(edge_files), 6)
        # 3 患者 + 3 住院 + 3 ICU + 4 诊断 + 3 处方 + 2 操作 + 2 文书
        self.assertEqual(len(nodes), 20)
        self.assertEqual(sum(manifest["relations"].values()), 17)
        self.assertEqual(manifest["objects"]["ClinicalNote"], 2)
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
        self.assertEqual(len(rules), 7)
        self.assertIn("L3", set(result["level"]))

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
