"""Agent 基座测试：视图脱敏、契约、trace、Supervisor 重试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance.agents.base import Agent, AgentResult, TraceWriter
from governance.agents.graph_critic import GraphCriticAgent
from governance.agents.prompts import load_prompt
from governance.agents.profiler import SourceProfilerAgent
from governance.agents.supervisor import Supervisor
from governance.agents.views import (
    digest,
    graph_stats_view,
    manifest_view,
    require_no_patient_data,
    schema_view,
    stats_view,
)
from governance.llm import LLMConfig, LLMUnavailable, build_service


class PromptSchemaTest(unittest.TestCase):
    """YAML 1.1 会把 on 解析成布尔，schema 里的字段名必须原样保留。"""

    def test_on_key_survives_yaml_loading(self) -> None:
        prompt = load_prompt("relation_extractor")
        props = prompt.output_schema["properties"]["relations"]["items"]["properties"]
        self.assertIn("on", props)
        self.assertNotIn("true", props)
        self.assertNotIn('"true"', json.dumps(prompt.output_schema))

    def test_other_schema_fields_intact(self) -> None:
        prompt = load_prompt("aligner")
        item = prompt.output_schema["properties"]["mappings"]["items"]
        for field in ("target_table", "target_field", "source_table", "source_field"):
            self.assertIn(field, item["properties"])


class ViewGuardTest(unittest.TestCase):
    def test_forbidden_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            require_no_patient_data({"subject_id": 1})

    def test_patient_id_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            require_no_patient_data({"value": str(10_000_032)})  # 模拟患者号

    def test_column_names_are_metadata_and_allowed(self) -> None:
        # 字段名作为取值出现是允许的：它是元数据，不是患者数据
        require_no_patient_data(
            {"tables": [{"table": "patients", "columns": ["subject_id", "gender"]}]}
        )

    def test_nested_violation_raises(self) -> None:
        with self.assertRaises(ValueError):
            require_no_patient_data({"tables": [{"note_id": 1}]})


class ViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tables = {
            "patients": pd.DataFrame({"subject_id": [1, 2, 3], "gender": ["F", "M", "F"]}),
            "admissions": pd.DataFrame({"hadm_id": [11, 12], "admittime": ["2100-01-01", None]}),
        }

    def test_schema_view_has_no_values(self) -> None:
        view = schema_view(self.tables)
        require_no_patient_data(view)
        self.assertEqual(len(view["tables"]), 2)
        self.assertEqual(view["tables"][0]["rows"], 3)
        self.assertEqual(view["tables"][0]["columns"][0]["name"], "subject_id")

    def test_stats_view_only_rates(self) -> None:
        view = stats_view(self.tables)
        require_no_patient_data(view)
        rates = {item["column"]: item["rate"] for item in view["tables"][1]["null_rates"]}
        self.assertEqual(rates["admittime"], 0.5)

    def test_graph_stats_view(self) -> None:
        graph = {
            "objects": {"Patient": {"keys": ["subject_id"], "rows": 100}},
            "relations": [{"from": "Patient", "to": "Encounter", "on": "subject_id"}],
        }
        view = graph_stats_view(graph)
        require_no_patient_data(view)
        self.assertEqual(view["objects"][0]["rows"], 100)

    def test_digest_is_stable(self) -> None:
        self.assertEqual(digest({"a": 1}), digest({"a": 1}))
        self.assertNotEqual(digest({"a": 1}), digest({"a": 2}))


class PromptTest(unittest.TestCase):
    def test_profiler_prompt_contract(self) -> None:
        prompt = load_prompt("profiler")
        self.assertEqual(prompt.id, "profiler")
        self.assertIn("objects", prompt.output_schema["required"])


class FlakyAgent(Agent):
    name = "FlakyAgent"
    role = "测试用"

    def __init__(self) -> None:
        super().__init__(llm=None)
        self.calls = 0

    def run(self, payload: dict) -> AgentResult:
        self.calls += 1
        if self.calls == 1:
            return AgentResult(
                ok=False, summary="第一次不合格", issues=[{"detail": "需要修正"}]
            )
        return AgentResult(
            ok=True, summary="第二次通过", artifacts={"feedback": payload.get("feedback")}
        )


class SupervisorTest(unittest.TestCase):
    def test_retry_with_feedback(self) -> None:
        agent = FlakyAgent()
        outcome = Supervisor(max_attempts=2).run(agent, {"x": 1})
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(agent.calls, 2)
        self.assertEqual(outcome.result.artifacts["feedback"], [{"detail": "需要修正"}])

    def test_stops_after_max_attempts(self) -> None:
        agent = FlakyAgent()
        outcome = Supervisor(max_attempts=1).run(agent, {"x": 1})
        self.assertFalse(outcome.ok)
        self.assertEqual(agent.calls, 1)


class ProfilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _payload(self) -> dict:
        tables = {
            "patients": pd.DataFrame({"subject_id": [1, 2], "gender": ["F", "M"]}),
            "admissions": pd.DataFrame({"subject_id": [1], "hadm_id": [11]}),
        }
        return {"schema_view": schema_view(tables), "stats_view": stats_view(tables)}

    def test_mock_run_produces_artifacts_and_trace(self) -> None:
        service = build_service(LLMConfig(provider="mock"), self.root)
        agent = SourceProfilerAgent(llm=service)
        result = agent.run(self._payload())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.artifacts["objects"]), 3)
        self.assertIsNotNone(result.trace)
        self.assertEqual(result.trace.agent, "SourceProfilerAgent")
        self.assertGreater(result.trace.tokens, 0)

    def test_trace_writer_appends_jsonl(self) -> None:
        service = build_service(LLMConfig(provider="mock"), self.root)
        writer = TraceWriter(self.root / "audit" / "agent_trace.jsonl")
        agent = SourceProfilerAgent(llm=service, trace_writer=writer)
        agent.run(self._payload())
        lines = (self.root / "audit" / "agent_trace.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["agent"], "SourceProfilerAgent")

    def test_off_mode_degrades_cleanly(self) -> None:
        service = build_service(LLMConfig(provider="off"), self.root)
        agent = SourceProfilerAgent(llm=service)
        outcome = Supervisor().run(agent, self._payload())
        self.assertFalse(outcome.ok)
        self.assertIn("provider=off", outcome.history[0]["error"])


class GraphCriticTest(unittest.TestCase):
    def test_mock_critic_gives_issues_and_suggestions(self) -> None:
        manifest = {
            "objects": {"Patient": 100, "Encounter": 350, "ClinicalNote": 155},
            "relations": {"has_encounter": 350, "has_note": 0},
        }
        payload = {"graph_stats_view": manifest_view(manifest)}
        require_no_patient_data(payload)

        with tempfile.TemporaryDirectory() as tmp:
            service = build_service(LLMConfig(provider="mock"), Path(tmp))
            agent = GraphCriticAgent(llm=service)
            result = agent.run(payload)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.artifacts["issues"]), 2)
        self.assertEqual(len(result.artifacts["suggestions"]), 2)
        self.assertEqual(result.trace.agent, "GraphCriticAgent")


if __name__ == "__main__":
    unittest.main()
