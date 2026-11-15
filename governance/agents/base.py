"""Agent 基座：角色、输入输出契约、调用与执行痕迹（trace）。

约定：

* 每个 Agent 声明 ``role`` / ``goal`` / ``inputs`` / ``outputs`` / ``tools``；
* 只通过 :meth:`Agent.ask` 调用模型，且入参必须是 :mod:`governance.agents.views` 生成的视图；
* 每次执行产出一条 :class:`AgentTrace`，落 ``output/audit/agent_trace.jsonl``；
* Agent 不直接读写数据表，执行交给 ``executors``（后续阶段接入）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.agents.prompts import Prompt, load_prompt
from governance.agents.views import digest, require_no_patient_data
from governance.llm import LLMRequest, LLMResponse, LLMService, LLMUnavailable


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
