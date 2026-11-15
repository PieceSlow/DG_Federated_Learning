"""GraphCriticAgent：对图谱统计视图做质量批评与修正建议。

M2 用 mock 演示；M5 接入 Supervisor 后，批评结果会驱动 ObjectFinder/RelationExtractor 重跑。
视图里只有"对象/关系类型与计数"，没有任何患者行或实例 ID。
"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult
from governance.agents.views import manifest_view

REQUIRED_OUTPUT_KEYS = ("issues", "suggestions")


class GraphCriticAgent(Agent):
    name = "GraphCriticAgent"
    role = "图谱质量批评"
    goal = "从图谱统计中识别结构缺陷，并给出可执行的修正建议"
    inputs = ("graph_stats_view",)
    outputs = ("issues", "suggestions")
    tools = ()
    prompt_id = "graph_critic"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        if "graph_stats_view" not in payload:
            raise ValueError("GraphCriticAgent 缺少输入: graph_stats_view")
        from governance.agents.views import require_no_patient_data

        require_no_patient_data(payload)

        parsed, response, _ = self.ask(payload)
        missing = [key for key in REQUIRED_OUTPUT_KEYS if key not in parsed]
        if missing:
            raise ValueError(f"{self.name} 输出缺少字段: {', '.join(missing)}")

        trace = self.emit(
            started=started,
            payload=payload,
            decision="基于图谱统计给出缺陷与修正建议",
            output=parsed,
            response=response,
        )
        return AgentResult(
            ok=True,
            summary=(
                f"提出 {len(parsed['issues'])} 项图谱问题、"
                f"{len(parsed['suggestions'])} 条修正建议"
            ),
            artifacts=parsed,
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )

    @staticmethod
    def view(graph_manifest: dict[str, Any]) -> dict[str, Any]:
        """把 manifest 转成可外发的图谱统计视图。"""
        return manifest_view(graph_manifest)
