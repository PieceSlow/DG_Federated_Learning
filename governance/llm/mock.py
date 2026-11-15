"""Mock provider：零网络、零成本，用于测试与课堂演示。

响应按 ``prompt_id`` 索引；值可以是单个响应，也可以是**响应列表**（按调用次序取用，
用于演示"第一次提案不合法 → 被驳回 → 第二次修正"的协作循环）。
"""

from __future__ import annotations

import json
from typing import Any

from governance.llm.client import LLMConfig, LLMRequest, LLMResponse

DEFAULT_RESPONSES: dict[str, Any] = {
    "profiler": {
        "objects": [
            {"name": "Patient", "keys": ["subject_id"], "reason": "患者级主键，连接住院与事件"},
            {"name": "Encounter", "keys": ["hadm_id"], "reason": "住院主键，聚合诊断/操作/处方"},
            {"name": "ICUStay", "keys": ["stay_id"], "reason": "ICU 停留，挂在住院下"},
        ],
        "relations": [
            {"from": "Patient", "to": "Encounter", "on": "subject_id", "cardinality": "1:N"},
            {"from": "Encounter", "to": "ICUStay", "on": "hadm_id", "cardinality": "1:N"},
        ],
        "risks": [
            {"code": "MISSING_TIME", "detail": "诊断表没有时间字段，事件时间需留空"},
            {"code": "ORPHAN_FK", "detail": "处方/检验的 hadm_id 可能不在住院表中"},
        ],
    },
    "object_finder": {
        "objects": [
            {
                "name": "Patient",
                "source_table": "patients",
                "keys": ["subject_id"],
                "attributes": ["gender", "anchor_age"],
                "sensitivity": "L3",
                "reason": "患者主体，主键 subject_id",
            },
            {
                "name": "Encounter",
                "source_table": "admissions",
                "keys": ["hadm_id"],
                "attributes": ["admittime", "dischtime"],
                "sensitivity": "L3",
                "reason": "一次住院，事件容器",
            },
            {
                "name": "Diagnosis",
                "source_table": "diagnoses_icd",
                "keys": ["subject_id", "hadm_id", "seq_num"],
                "attributes": ["icd_code", "icd_version"],
                "sensitivity": "L2",
                "reason": "住院诊断编码",
            },
        ],
        "risks": [{"code": "MISSING_TIME", "detail": "诊断表无时间字段"}],
    },
    "relation_extractor": [
        # 第一次：on 用了不存在的 patient_id，应被校验器驳回
        {
            "relations": [
                {"name": "has_encounter", "from": "Patient", "to": "Encounter",
                 "on": "patient_id", "cardinality": "1:N"}
            ]
        },
        # 第二次：修正为真实主键
        {
            "relations": [
                {"name": "has_encounter", "from": "Patient", "to": "Encounter",
                 "on": "subject_id", "cardinality": "1:N"},
                {"name": "has_diagnosis", "from": "Encounter", "to": "Diagnosis",
                 "on": "hadm_id", "cardinality": "1:N"},
            ]
        },
    ],
    "graph_critic": {
        "issues": [
            {"code": "GRAPH_MISSING_RELATION", "severity": "warning",
             "detail": "临床文书应挂到住院上，建议核对 hadm_id 缺失"},
            {"code": "GRAPH_ORPHAN_NODE", "severity": "warning",
             "detail": "患者对象存在零关系节点时，通常是住院表未覆盖该患者"},
        ],
        "suggestions": [
            "在 has_note 边上增加 hadm_id 非空校验",
            "对 Patient 增加 has_encounter 覆盖检查",
        ],
    },
}


class MockProvider:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = {**DEFAULT_RESPONSES, **(responses or {})}
        self.calls = 0
        self._index: dict[str, int] = {}

    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse:
        self.calls += 1
        if request.prompt_id not in self.responses:
            raise KeyError(f"MockProvider 没有 prompt_id={request.prompt_id} 的响应")
        payload = self.responses[request.prompt_id]
        if isinstance(payload, list):
            index = self._index.get(request.prompt_id, 0)
            payload = payload[min(index, len(payload) - 1)]
            self._index[request.prompt_id] = index + 1
        text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            text=text,
            parsed=payload,
            prompt_tokens=len(request.user) // 4,
            completion_tokens=len(text) // 4,
            model="mock",
        )
