"""Mock provider：零网络、零成本，用于测试与课堂演示。

响应按 ``prompt_id`` 索引；演示用的默认响应内置在这里，可被 ``mock_responses`` 覆盖。
"""

from __future__ import annotations

import json
from typing import Any

from governance.llm.client import LLMConfig, LLMRequest, LLMResponse

DEFAULT_RESPONSES: dict[str, dict[str, Any]] = {
    "profiler": {
        "objects": [
            {
                "name": "Patient",
                "keys": ["subject_id"],
                "reason": "患者级主键，连接住院与事件",
            },
            {
                "name": "Encounter",
                "keys": ["hadm_id"],
                "reason": "住院主键，聚合诊断/操作/处方",
            },
            {
                "name": "ICUStay",
                "keys": ["stay_id"],
                "reason": "ICU 停留，挂在住院下",
            },
        ],
        "relations": [
            {"from": "Patient", "to": "Encounter", "on": "subject_id", "cardinality": "1:N"},
            {"from": "Encounter", "to": "ICUStay", "on": "hadm_id", "cardinality": "1:N"},
        ],
        "risks": [
            {"code": "MISSING_TIME", "detail": "诊断表没有时间字段，事件时间需留空"},
            {"code": "ORPHAN_FK", "detail": "处方/检验的 hadm_id 可能不在住院表中"},
        ],
    }
}


class MockProvider:
    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.responses = {**DEFAULT_RESPONSES, **(responses or {})}
        self.calls = 0

    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse:
        self.calls += 1
        if request.prompt_id not in self.responses:
            raise KeyError(f"MockProvider 没有 prompt_id={request.prompt_id} 的响应")
        payload = self.responses[request.prompt_id]
        text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            text=text,
            parsed=payload,
            prompt_tokens=len(request.user) // 4,
            completion_tokens=len(text) // 4,
            model="mock",
        )
