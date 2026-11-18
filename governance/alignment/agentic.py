"""对齐的智能体编排：AlignerAgent 提案 → AlignmentVerifierAgent 校验 → 反馈重试。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from governance.agents.aligner import AlignerAgent
from governance.agents.alignment_verifier import AlignmentVerifierAgent
from governance.agents.base import TraceWriter
from governance.alignment.schema import family_of
from governance.context import GovernanceContext
from governance.llm import LLMError

MAX_ATTEMPTS = 2


def candidate_view(candidates: pd.DataFrame, top_k: int = 5) -> dict[str, Any]:
    """给 LLM 的候选视图：只有字段名、分数、类型、空值率。"""
    targets: list[dict[str, Any]] = []
    for (table, field), group in candidates.groupby(["target_table", "target_field"], sort=False):
        top = group.sort_values("rank").head(top_k)
        targets.append(
            {
                "target_table": table,
                "target_field": field,
                "candidates": [
                    {
                        "source_table": row.source_table,
                        "source_field": row.source_field,
                        "score": row.score,
                        "string_score": row.string_score,
                        "type_score": row.type_score,
                        "structure_score": row.structure_score,
                        "value_score": row.value_score,
                        "source_dtype": row.source_dtype,
                        "target_dtype": row.target_dtype,
                        "source_null_rate": row.source_null_rate,
                    }
                    for row in top.itertuples()
                ],
            }
        )
    return {"targets": targets}


def build_evidence(mappings: list[dict[str, Any]], candidates: pd.DataFrame) -> list[dict[str, Any]]:
    index = {
        (row.target_table, row.target_field, row.source_table, row.source_field): row
        for row in candidates.itertuples()
    }
    evidence: list[dict[str, Any]] = []
    for mapping in mappings:
        key = (
            mapping["target_table"],
            mapping["target_field"],
            mapping["source_table"],
            mapping["source_field"],
        )
        row = index.get(key)
        if row is None:
            evidence.append({"in_candidates": False, "dtype_ok": False, "source_null_rate": 1.0})
            continue
        source_family, target_family = family_of(row.source_dtype), family_of(row.target_dtype)
        dtype_ok = source_family == target_family or {source_family, target_family} == {"int", "float"}
        evidence.append(
            {
                "in_candidates": True,
                "dtype_ok": dtype_ok,
                "source_null_rate": float(row.source_null_rate),
                "target_is_id": str(mapping["target_field"]).lower().endswith("_id"),
                "score": float(row.score),
            }
        )
    return evidence


def canonicalize_targets(
    mappings: list[dict[str, Any]],
    candidates: pd.DataFrame,
) -> tuple[list[dict[str, Any]], int]:
    """把模型写错的目标标识纠正回候选集里的那一套。

    实测 qwen3 会把 ``target_table`` 填成源表名（``diagnoses_icd.code``），
    而候选集的 ``target_table`` 恒为数据集名（``clinical_events``）。
    这里只做**无歧义纠正**：``target_field`` 在候选集中唯一对应一个目标表时，
    才把 ``target_table`` 改写过去；对不上的保持原样，交给校验器驳回。
    """
    targets = set(zip(candidates["target_table"], candidates["target_field"]))
    tables_by_field: dict[str, set[str]] = {}
    for table, field in targets:
        tables_by_field.setdefault(str(field), set()).add(str(table))

    fixed = 0
    adjusted: list[dict[str, Any]] = []
    for mapping in mappings:
        item = dict(mapping)
        key = (str(item.get("target_table")), str(item.get("target_field")))
        if key not in targets:
            tables = tables_by_field.get(str(item.get("target_field")))
            if tables and len(tables) == 1:
                item["target_table"] = next(iter(tables))
                fixed += 1
        adjusted.append(item)
    return adjusted, fixed


def align_with_agents(
    ctx: GovernanceContext,
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    if ctx.llm is None or ctx.llm.config.provider == "off":
        return None, {"status": "skipped", "reason": "provider=off"}

    writer = TraceWriter(ctx.settings.audit_dir / "agent_trace.jsonl")
    aligner = AlignerAgent(llm=ctx.llm, trace_writer=writer)
    verifier = AlignmentVerifierAgent(trace_writer=writer)
    payload: dict[str, Any] = {"candidate_view": candidate_view(candidates)}
    history: list[dict[str, Any]] = []
    canonicalized = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            proposed = aligner.run(payload)
        except (LLMError, ValueError) as exc:
            return None, {"status": "error", "attempts": attempt, "error": str(exc), "history": history}
        mappings, fixed = canonicalize_targets(proposed.artifacts["mappings"], candidates)
        canonicalized += fixed
        evidence = build_evidence(mappings, candidates)
        verdict = verifier.run({"mappings": mappings, "evidence": evidence})
        if verdict.ok:
            frame = pd.DataFrame(mappings)
            frame["score"] = frame.get("confidence", 0.0)
            frame["method"] = "aligner_agent"
            frame["shared_source"] = False
            columns = ["target_table", "target_field", "source_table", "source_field",
                       "score", "method", "shared_source"]
            return frame[columns], {
                "status": "ok",
                "attempts": attempt,
                "mappings": len(frame),
                "canonicalized_targets": canonicalized,
                "history": history,
            }
        history.append({"attempt": attempt, "issues": verdict.issues})
        payload = {**payload, "feedback": verdict.issues}

    return None, {
        "status": "rejected",
        "attempts": MAX_ATTEMPTS,
        "canonicalized_targets": canonicalized,
        "history": history,
    }
