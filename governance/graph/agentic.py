"""智能体链条建图：ObjectFinder → RelationExtractor → 本地执行 → GraphCritic。

这是"图谱由智能体构建"的落点：

1. ObjectFinder 看 schema/统计视图，提出对象候选；
2. 代码校验候选（表存在、主键在列里），不合格则 Supervisor 带反馈重试；
3. RelationExtractor 提出关系，校验 join 列；
4. 本地 `build_graph` 执行定义，生成节点/边；
5. GraphCritic 看图谱统计，提出缺陷 → 若有必要，回退到 3 修正一次。

LLM 关闭时返回 ``None``，由调用方回退到确定性的 ``objects.yaml``，保证可复现。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from governance.agents.base import TraceWriter
from governance.agents.graph_critic import GraphCriticAgent
from governance.agents.object_finder import ObjectFinderAgent
from governance.agents.relation_extractor import RelationExtractorAgent
from governance.agents.supervisor import Supervisor
from governance.agents.views import manifest_view, schema_view, stats_view
from governance.context import GovernanceContext
from governance.graph.build import GraphData, build_graph
from governance.graph.proposal import model_from_proposals, validate_proposals


def build_with_agents(
    ctx: GovernanceContext, notes: pd.DataFrame | None
) -> tuple[GraphData | None, list[str]]:
    """返回 (图谱, 错误)；错误非空表示应回退到确定性模型。"""
    if ctx.llm is None or ctx.llm.config.provider == "off":
        return None, ["LLM 未启用（provider=off）"]

    writer = TraceWriter(ctx.settings.audit_dir / "agent_trace.jsonl")
    schema = schema_view(ctx.tables)
    stats = stats_view(ctx.tables)

    try:
        finder = ObjectFinderAgent(llm=ctx.llm, trace_writer=writer)

        def object_validator(result) -> bool:
            # 表存在、主键在列里、主键唯一——不通过则带 feedback 让模型重试
            errors = validate_proposals(
                result.artifacts["objects"], [], ctx.tables, notes
            )
            if errors:
                # 把具体驳回理由塞进 issues，Supervisor 才会把它作为 feedback 回传；
                # 只返回 False 时模型只收到"未通过校验"，无法自我修正。
                result.issues = [
                    {"code": "INVALID_OBJECT", "detail": error} for error in errors
                ]
                return False
            return True

        found = Supervisor(max_attempts=2).run(
            finder, {"schema_view": schema, "stats_view": stats}, validator=object_validator
        )
        if not found.ok or found.result is None:
            return None, ["对象识别未通过校验"]
        objects = found.result.artifacts["objects"]

        extractor = RelationExtractorAgent(llm=ctx.llm, trace_writer=writer)

        def relation_validator(result) -> bool:
            errors = validate_proposals(
                objects, result.artifacts["relations"], ctx.tables, notes
            )
            if errors:
                result.issues = [
                    {"code": "INVALID_RELATION", "detail": error} for error in errors
                ]
                return False
            return True

        payload: dict[str, Any] = {"objects": objects, "schema_view": schema}
        extracted = Supervisor(max_attempts=2).run(
            extractor, payload, validator=relation_validator
        )
        if not extracted.ok or extracted.result is None:
            return None, ["关系抽取未通过校验"]

        model = model_from_proposals(objects, extracted.result.artifacts["relations"])
        graph = build_graph(ctx.tables, notes, model=model)

        critic = GraphCriticAgent(llm=ctx.llm, trace_writer=writer)
        critique = Supervisor(max_attempts=1).run(
            critic, {"graph_stats_view": manifest_view(graph.manifest)}
        )
        retries = 0
        if critique.ok and critique.result is not None and critique.result.artifacts.get("issues"):
            feedback = {
                "issues": critique.result.artifacts.get("issues", []),
                "suggestions": critique.result.artifacts.get("suggestions", []),
            }
            revised = Supervisor(max_attempts=2).run(
                extractor,
                {**payload, "feedback": feedback},
                validator=relation_validator,
            )
            if revised.ok and revised.result is not None:
                model = model_from_proposals(
                    objects, revised.result.artifacts["relations"]
                )
                graph = build_graph(ctx.tables, notes, model=model)
                retries = 1

        ctx.stats["graph_agents"] = {
            "used": True,
            "objects": len(objects),
            "relations": len(model.relations),
            "retries": retries,
            "provider": ctx.llm.config.provider,
        }
        ctx.log(
            "智能体建图",
            f"ObjectFinder 提出 {len(objects)} 类对象，RelationExtractor 提出 "
            f"{len(model.relations)} 条关系，GraphCritic 审核后重试 {retries} 次；"
            "执行仍由本地代码完成。",
        )
        return graph, []
    except (ValueError, KeyError) as exc:
        return None, [f"智能体建图失败：{exc}"]
