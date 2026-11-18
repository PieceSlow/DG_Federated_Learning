"""ObjectFinderAgent：从表结构与统计中提出对象候选。

契约不满足时返回 ``ok=False`` 并带上 ``issues``，而不是抛异常：
异常会绕过 ``Supervisor`` 的重试，模型就永远收不到"错在哪"的反馈。
实测 qwen3 第一次常把 ``source_table`` 写成 ``source,`` 或只给 ``name``，
带上具体缺哪个字段重试一次通常就能补齐。
"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED = ("name", "source_table", "keys", "attributes", "sensitivity", "reason")


class ObjectFinderAgent(Agent):
    name = "ObjectFinderAgent"
    role = "对象识别"
    goal = "从表结构与统计中提出可作为对象的主体（人/物/事件）"
    inputs = ("schema_view", "stats_view")
    outputs = ("objects", "risks")
    tools = ()
    prompt_id = "object_finder"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        missing = [key for key in self.inputs if key not in payload]
        if missing:
            raise ValueError(f"{self.name} 缺少输入: {', '.join(missing)}")

        parsed, response, _ = self.ask(payload)
        objects = parsed.get("objects") or []
        issues: list[dict[str, Any]] = []
        if not objects:
            issues.append({"code": "EMPTY_OBJECTS", "detail": "没有提出任何对象"})
        for item in objects:
            if not isinstance(item, dict):
                issues.append({"code": "BAD_OBJECT", "detail": f"对象不是键值结构: {item!r}"})
                continue
            absent = [key for key in REQUIRED if key not in item]
            if absent:
                issues.append(
                    {
                        "code": "MISSING_FIELD",
                        "detail": f"{item.get('name')}: 缺少字段 {', '.join(absent)}",
                    }
                )

        trace = self.emit(
            started=started,
            payload=payload,
            decision=(
                f"提出 {len(objects)} 类对象候选"
                if not issues
                else f"提出 {len(objects)} 类对象，{len(issues)} 处不合约"
            ),
            output=parsed,
            response=response,
        )
        if issues:
            return AgentResult(
                ok=False,
                summary=f"提案不合约：{issues[0]['detail']}",
                artifacts={"objects": objects, "risks": parsed.get("risks", [])},
                issues=issues,
                trace=trace,
            )
        return AgentResult(
            ok=True,
            summary=f"提出 {len(objects)} 类对象、{len(parsed.get('risks', []))} 项风险",
            artifacts={"objects": objects, "risks": parsed.get("risks", [])},
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )
