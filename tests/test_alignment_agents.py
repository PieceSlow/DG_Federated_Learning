"""对齐智能体（AlignerAgent + AlignmentVerifierAgent）测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from governance.agents.alignment_verifier import AlignmentVerifierAgent
from governance.agents.views import require_no_patient_data
from governance.alignment.agentic import (
    align_with_agents,
    build_evidence,
    candidate_view,
)
from governance.alignment.schema import extract_source_fields
from governance.alignment.select import build_candidates
from governance.alignment.target_schema import (
    allowed_table_map,
    load_object_targets,
    target_views,
)
from governance.config import Settings
from governance.context import GovernanceContext
from governance.llm import LLMConfig, build_service
from tests.test_graph import _tables


def _setup():
    targets = load_object_targets()
    candidates = build_candidates(
        extract_source_fields(_tables()),
        target_views(targets),
        allowed_tables=allowed_table_map(targets),
    )
    return candidates


class CandidateViewTest(unittest.TestCase):
    def test_view_contains_only_metadata(self) -> None:
        candidates = _setup()
        view = candidate_view(candidates, top_k=3)
        require_no_patient_data(view)
        self.assertTrue(view["targets"])
        self.assertLessEqual(len(view["targets"][0]["candidates"]), 3)

    def test_evidence_flags_missing_candidate(self) -> None:
        candidates = _setup()
        evidence = build_evidence(
            [{"target_table": "clinical_events", "target_field": "event_time",
              "source_table": "admissions", "source_field": "patient_id"}],
            candidates,
        )
        self.assertFalse(evidence[0]["in_candidates"])


class VerifierTest(unittest.TestCase):
    def test_rejects_missing_candidate(self) -> None:
        candidates = _setup()
        mapping = {"target_table": "clinical_events", "target_field": "event_time",
                   "source_table": "admissions", "source_field": "patient_id"}
        evidence = build_evidence([mapping], candidates)
        verdict = AlignmentVerifierAgent().run({"mappings": [mapping], "evidence": evidence})
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.issues[0]["rule"], "NOT_IN_CANDIDATES")

    def test_accepts_valid_mapping(self) -> None:
        candidates = _setup()
        mapping = {"target_table": "clinical_events", "target_field": "subject_id",
                   "source_table": "patients", "source_field": "subject_id"}
        evidence = build_evidence([mapping], candidates)
        verdict = AlignmentVerifierAgent().run({"mappings": [mapping], "evidence": evidence})
        self.assertTrue(verdict.ok)


class AlignWithAgentsTest(unittest.TestCase):
    def _context(self, tmp: Path) -> GovernanceContext:
        settings = Settings(mimic_root=tmp / "m", note_root=tmp / "n", output=tmp / "out")
        settings.ensure_dirs()
        ctx = GovernanceContext(settings=settings)
        ctx.tables = _tables()
        return ctx

    def test_mock_aligner_rejects_then_retries(self) -> None:
        candidates = _setup()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._context(root)
            ctx.llm = build_service(LLMConfig(provider="mock"), ctx.settings.output)

            selected, info = align_with_agents(ctx, candidates)

            self.assertIsNotNone(selected)
            self.assertEqual(info["status"], "ok")
            self.assertEqual(info["attempts"], 2)
            self.assertEqual(len(selected), 3)

            traces = [
                json.loads(line)
                for line in (ctx.settings.audit_dir / "agent_trace.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            agents = [item["agent"] for item in traces]
            self.assertEqual(agents.count("AlignerAgent"), 2)
            self.assertEqual(agents.count("AlignmentVerifierAgent"), 2)

    def test_off_mode_skips(self) -> None:
        candidates = _setup()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._context(Path(tmp))
            selected, info = align_with_agents(ctx, candidates)
            self.assertIsNone(selected)
            self.assertEqual(info["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
