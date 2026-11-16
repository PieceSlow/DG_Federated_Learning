"""AlignmentVerifierAgent：校验映射提案（本地证据，不调用模型）。

证据由本地计算：候选是否存在、类型是否兼容、源字段空值率。
规则化校验的好处是"驳回理由"确定、可复现，不依赖模型自证。
"""

from __future__ import annotations

import time
from typing import Any

from governance.agents.base import Agent, AgentResult


class AlignmentVerifierAgent(Agent):
    name = "AlignmentVerifierAgent"
    role = "对齐校验"
    goal = "用本地证据驳回不合法或高风险的字段映射"
    inputs = ("mappings", "evidence")
    outputs = ("accepted", "issues")
    tools = ()
    prompt_id = None

    def run(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        mappings = payload.get("mappings") or []
        evidence = payload.get("evidence") or []
        if len(mappings) != len(evidence):
            raise ValueError("mappings 与 evidence 数量不一致")

        issues: list[dict[str, Any]] = []
        for mapping, item in zip(mappings, evidence):
            target = f"{mapping['target_table']}.{mapping['target_field']}"
            source = f"{mapping['source_table']}.{mapping['source_field']}"
            if not item.get("in_candidates"):
                issues.append({"rule": "NOT_IN_CANDIDATES", "target": target,
                               "detail": f"候选集中不存在 {source}"})
            elif not item.get("dtype_ok"):
                issues.append({"rule": "TYPE_MISMATCH", "target": target,
                               "detail": f"{source} 与目标类型不兼容"})
            elif item.get("source_null_rate", 0) > 0.95:
                issues.append({"rule": "TOO_NULL", "target": target,
                               "detail": f"{source} 空值率过高"})
            elif item.get("target_is_id") and item.get("source_null_rate", 0) > 0.2:
                issues.append({"rule": "ID_NULLABLE", "target": target,
                               "detail": f"{source} 是主键/外键但空值率过高"})

        trace = self.emit(
            started=started,
            payload={"mappings": mappings, "evidence": evidence},
            decision="通过" if not issues else f"驳回 {len(issues)} 条映射",
            output={"issues": issues},
        )
        return AgentResult(
            ok=not issues,
            summary=f"校验 {len(mappings)} 条映射，驳回 {len(issues)} 条",
            artifacts={"accepted": not issues, "issues": issues},
            issues=issues,
            trace=trace,
        )
