"""SourceProfilerAgent：从表结构与统计中识别业务对象、关系候选与数据风险。

这是链条上的第一个真实 Agent（M1 用 mock 演示）。输入只包含
``schema_view`` 与 ``stats_view``，不含任何行级记录或文本。
"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED_OUTPUT_KEYS = ("objects", "relations", "risks")


class SourceProfilerAgent(Agent):
    name = "SourceProfilerAgent"
    role = "数据源画像"
    goal = "识别数据中的人/物/关系候选，并指出结构层面的数据风险"
    inputs = ("schema_view", "stats_view")
    outputs = ("objects", "relations", "risks")
    tools = ()
    prompt_id = "profiler"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        missing = [key for key in self.inputs if key not in payload]
        if missing:
            raise ValueError(f"{self.name} 缺少输入: {', '.join(missing)}")

        parsed, response, _ = self.ask(payload)
        missing_keys = [key for key in REQUIRED_OUTPUT_KEYS if key not in parsed]
        if missing_keys:
            raise ValueError(f"{self.name} 输出缺少字段: {', '.join(missing_keys)}")

        trace = self.emit(
            started=started,
            payload=payload,
            decision="生成对象/关系候选与风险清单",
            output=parsed,
            response=response,
        )
        return AgentResult(
            ok=True,
            summary=(
                f"识别 {len(parsed['objects'])} 类对象、"
                f"{len(parsed['relations'])} 条关系、{len(parsed['risks'])} 项风险"
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
