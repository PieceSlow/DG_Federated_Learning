"""M2.6a：智能体链条建图测试（ObjectFinder → RelationExtractor → Critic）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from governance import pipeline
from governance.config import Settings
from governance.context import GovernanceContext
from governance.graph.proposal import model_from_proposals, validate_proposals
from governance.llm import LLMConfig, build_service
from governance.objectify import run as objectify_run
from tests.synthetic import write_synthetic_mimic
from tests.test_graph import _notes, _tables


class ProposalValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.objects = [
            {"name": "Patient", "source_table": "patients", "keys": ["subject_id"],
             "attributes": ["gender"], "sensitivity": "L3", "reason": "主体"},
            {"name": "Encounter", "source_table": "admissions", "keys": ["hadm_id"],
             "attributes": ["admittime"], "sensitivity": "L3", "reason": "住院"},
        ]

    def test_valid_relation_passes(self) -> None:
        relations = [{"name": "has_encounter", "from": "Patient", "to": "Encounter",
                      "on": "subject_id", "cardinality": "1:N"}]
        self.assertEqual(validate_proposals(self.objects, relations, _tables(), _notes()), [])

    def test_unknown_column_is_rejected(self) -> None:
        relations = [{"name": "has_encounter", "from": "Patient", "to": "Encounter",
                      "on": "patient_id", "cardinality": "1:N"}]
        errors = validate_proposals(self.objects, relations, _tables(), _notes())
        self.assertTrue(any("patient_id" in item for item in errors))

    def test_unknown_source_table_is_rejected(self) -> None:
        objects = [dict(self.objects[0], source_table="not_a_table")]
        errors = validate_proposals(objects, [], _tables(), _notes())
        self.assertTrue(any("not_a_table" in item for item in errors))

    def test_model_from_proposals(self) -> None:
        model = model_from_proposals(
            self.objects,
            [{"name": "has_encounter", "from": "Patient", "to": "Encounter",
              "on": "subject_id", "cardinality": "1:N"}],
        )
        self.assertEqual(len(model.objects), 2)
        self.assertEqual(model.relations[0].name, "has_encounter")


class AgenticObjectifyTest(unittest.TestCase):
    def _context(self, tmp: Path, with_llm: bool) -> GovernanceContext:
        settings = Settings(mimic_root=tmp / "m", note_root=tmp / "n", output=tmp / "out")
        settings.ensure_dirs()
        ctx = GovernanceContext(settings=settings)
        ctx.tables = _tables()
        ctx.events = [
            {"code": "NOTE//DISCHARGE", "source_record_id": "discharge:901",
             "subject_id": 1, "hadm_id": 11, "time": "2100-01-05 10:00:00"}
        ]
        ctx.stats["notes_used"] = 1
        if with_llm:
            ctx.llm = build_service(LLMConfig(provider="mock"), settings.output)
        return ctx

    def test_agent_chain_builds_graph_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._context(Path(tmp), with_llm=True)
            objectify_run(ctx)

            manifest = json.loads(
                (ctx.settings.governance / "graph" / "graph_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(manifest["objects"]), {"Patient", "Encounter", "Diagnosis"})
            self.assertEqual(manifest["relations"]["has_encounter"], 1)
            self.assertEqual(manifest["relations"]["has_diagnosis"], 1)
            self.assertTrue(ctx.stats["graph_agents"]["used"])
            self.assertEqual(ctx.stats["graph_agents"]["retries"], 1)

            traces = [
                json.loads(line)
                for line in (ctx.settings.audit_dir / "agent_trace.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            agents = [item["agent"] for item in traces]
            self.assertIn("ObjectFinderAgent", agents)
            self.assertIn("GraphCriticAgent", agents)
            # 关系抽取：首次被校验器驳回后重试一次，GraphCritic 后又修正一次
            self.assertEqual(agents.count("RelationExtractorAgent"), 3)

    def test_llm_off_falls_back_to_deterministic_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._context(Path(tmp), with_llm=False)
            objectify_run(ctx)
            manifest = json.loads(
                (ctx.settings.governance / "graph" / "graph_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest["objects"]), 7)
            self.assertNotIn("graph_agents", ctx.stats)


class PipelineWithAgentsTest(unittest.TestCase):
    def test_pipeline_runs_with_mock_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mimic = write_synthetic_mimic(root)
            settings = Settings(mimic_root=mimic, note_root=root, output=root / "out", cohort_size=3)
            settings.ensure_dirs()
            llm = build_service(LLMConfig(provider="mock"), settings.output)

            ctx = pipeline.run(settings, llm=llm)
            manifest = json.loads(
                (settings.governance / "graph" / "graph_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(manifest["objects"]), {"Patient", "Encounter", "Diagnosis"})
            self.assertTrue(ctx.stats["graph_agents"]["used"])
            self.assertTrue((settings.audit_dir / "agent_trace.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
