"""RelationExtractorAgent：为已确认的对象提出关系（边）。

契约不满足时返回 ``ok=False`` 并带上 ``issues``，而不是抛异常——
抛异常会绕过 ``Supervisor`` 的带反馈重试，模型就收不到"缺哪个字段"。
"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult

REQUIRED = ("name", "from", "to", "on", "cardinality")


class RelationExtractorAgent(Agent):
    name = "RelationExtractorAgent"
    role = "关系抽取"
    goal = "为对象定义提出可执行的关系（边），并给出置信度"
    inputs = ("objects", "schema_view")
    outputs = ("relations",)
    tools = ()
    prompt_id = "relation_extractor"

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        missing = [key for key in self.inputs if key not in payload]
        if missing:
            raise ValueError(f"{self.name} 缺少输入: {', '.join(missing)}")

        parsed, response, _ = self.ask(payload)
        relations = parsed.get("relations") or []
        issues: list[dict[str, Any]] = []
        if not relations:
            issues.append({"code": "EMPTY_RELATIONS", "detail": "没有提出任何关系"})
        for item in relations:
            if not isinstance(item, dict):
                issues.append(
                    {"code": "BAD_RELATION", "detail": f"关系不是键值结构: {item!r}"}
                )
                continue
            absent = [key for key in REQUIRED if not item.get(key)]
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
                f"提出 {len(relations)} 条关系候选"
                if not issues
                else f"提出 {len(relations)} 条关系，{len(issues)} 处不合约"
            ),
            output=parsed,
            response=response,
        )
        if issues:
            return AgentResult(
                ok=False,
                summary=f"关系提案不合约：{issues[0]['detail']}",
                artifacts={"relations": relations},
                issues=issues,
                trace=trace,
            )
        return AgentResult(
            ok=True,
            summary=f"提出 {len(relations)} 条关系",
            artifacts={"relations": relations},
            usage={
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "model": response.model,
                "cached": response.cached,
            },
            trace=trace,
        )
