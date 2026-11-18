"""Agent 基座：角色、输入输出契约、调用与执行痕迹（trace）。

约定：

* 每个 Agent 声明 ``role`` / ``goal`` / ``inputs`` / ``outputs`` / ``tools``；
* 只通过 :meth:`Agent.ask` 调用模型，且入参必须是 :mod:`governance.agents.views` 生成的视图；
* 每次执行产出一条 :class:`AgentTrace`，落 ``output/audit/agent_trace.jsonl``；
* Agent 不直接读写数据表，执行交给 ``executors``（后续阶段接入）。

模型输出的**键名**会在 :meth:`Agent.ask` 里做一次受限修复（见 :func:`repair_contract_keys`）：
``source,table``、``source table``、``sourceTable`` 都还原成 ``source_table``。
小模型常常只错在标点上，而一个逗号就会让整条 agent 链回退，这种修复值得做；
但只修复"能唯一归一成契约字段"的键，数据键（如 ``clinical_events.code``）不碰。
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.agents.prompts import Prompt, load_prompt
from governance.agents.views import digest, require_no_patient_data
from governance.llm import LLMRequest, LLMResponse, LLMService, LLMUnavailable

#: 智能体契约里允许出现的字段名（含各 prompt 的 required 字段）
CONTRACT_KEYS = frozenset(
    {
        "name", "keys", "attributes", "sensitivity", "interface", "grain",
        "source_table", "target_table", "target_field", "source_field",
        "confidence", "reason", "cardinality", "from", "to", "on",
        "objects", "relations", "risks", "mappings", "issues", "suggestions",
        "accepted", "code", "detail", "severity", "repaired",
    }
)

#: 观测到的键名残缺写法 → 契约字段。只在目标键缺失时生效，避免误改正常输出。
#: 例如 qwen3 会输出 ``"source,"``（截断）或 ``"source"``，归一后都成了 ``source``。
KEY_ALIASES = {
    "source": "source_table",
    "source_name": "source_table",
    "source_table_name": "source_table",
    "target": "target_table",
    "target_table_name": "target_table",
}

_KEY_SEPARATOR = re.compile(r"[\s,.;:，。、\-]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")


def normalize_key(key: Any) -> str:
    """``source,table`` / ``sourceTable`` / ``source table`` → ``source_table``。"""
    text = _CAMEL.sub(r"_\1", str(key).strip())
    text = _KEY_SEPARATOR.sub("_", text).lower()
    return re.sub(r"_+", "_", text).strip("_")


def repair_contract_keys(value: Any, counter: list[int] | None = None) -> Any:
    """递归修复契约字段的键名，返回修复后的结构（不修改入参）。

    只重命名**归一化后落在契约词表里**的键，或命中有界别名表的残缺写法；
    其它键原样保留，避免把 ``{"clinical_events.code": ...}`` 这类数据键改坏。
    """
    counter = counter if counter is not None else [0]
    if isinstance(value, dict):
        repaired: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = normalize_key(key)
            name = str(key)
            if name not in CONTRACT_KEYS:
                if normalized in CONTRACT_KEYS:
                    counter[0] += 1
                    name = normalized
                elif KEY_ALIASES.get(normalized) and KEY_ALIASES[normalized] not in value:
                    counter[0] += 1
                    name = KEY_ALIASES[normalized]
            repaired[name] = repair_contract_keys(item, counter)
        return repaired
    if isinstance(value, list):
        return [repair_contract_keys(item, counter) for item in value]
    return value


@dataclass
class AgentResult:
    ok: bool
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    next_hint: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    trace: "AgentTrace | None" = None


@dataclass
class AgentTrace:
    agent: str
    step: int
    prompt_id: str | None
    decision: str
    input_digest: str
    output_digest: str
    duration_ms: int
    tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "step": self.step,
            "prompt_id": self.prompt_id,
            "decision": self.decision,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


class TraceWriter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, trace: AgentTrace) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


class Agent:
    name: str = "Agent"
    role: str = ""
    goal: str = ""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    prompt_id: str | None = None

    def __init__(
        self,
        llm: LLMService | None = None,
        trace_writer: TraceWriter | None = None,
    ) -> None:
        self.llm = llm
        self.trace_writer = trace_writer
        self.traces: list[AgentTrace] = []
        self.step = 0

    # ---- 子类实现 ---------------------------------------------------------
    def run(self, payload: dict[str, Any]) -> AgentResult:
        raise NotImplementedError

    # ---- 公共能力 ---------------------------------------------------------
    def ask(self, payload: dict[str, Any]) -> tuple[dict[str, Any], LLMResponse, Prompt]:
        """校验视图 → 调模型 → 解析 JSON。

        payload 必须是 narrow view；这里再校验一次，防止调用方绕过视图层。
        """
        if self.prompt_id is None:
            raise ValueError(f"{self.name} 未声明 prompt_id")
        if self.llm is None:
            raise LLMUnavailable(f"{self.name} 未注入 LLMService")
        require_no_patient_data(payload)
        prompt = load_prompt(self.prompt_id)
        request = LLMRequest(
            prompt_id=prompt.id,
            system=prompt.system,
            user=json.dumps(payload, ensure_ascii=False, indent=2),
            schema=prompt.output_schema,
        )
        response = self.llm.complete(request)
        parsed = response.parsed
        if parsed is None:
            parsed = json.loads(response.text)
        if not isinstance(parsed, dict):
            raise ValueError(f"{self.name} 期望 JSON 对象，得到 {type(parsed).__name__}")
        counter = [0]
        parsed = repair_contract_keys(parsed, counter)
        if counter[0]:
            print(
                f"[{self.name}] 修复模型输出的键名 {counter[0]} 处（如 source,table → source_table）",
                file=sys.stderr,
            )
        return parsed, response, prompt

    def emit(
        self,
        *,
        started: float,
        payload: dict[str, Any],
        decision: str,
        output: Any,
        response: LLMResponse | None = None,
    ) -> AgentTrace:
        self.step += 1
        usage = response or LLMResponse(text="")
        trace = AgentTrace(
            agent=self.name,
            step=self.step,
            prompt_id=self.prompt_id,
            decision=decision,
            input_digest=digest(payload),
            output_digest=digest(output),
            duration_ms=int((time.perf_counter() - started) * 1000),
            tokens=usage.prompt_tokens + usage.completion_tokens,
            cost_usd=0.0,
        )
        self.traces.append(trace)
        if self.trace_writer is not None:
            self.trace_writer.write(trace)
        return trace
