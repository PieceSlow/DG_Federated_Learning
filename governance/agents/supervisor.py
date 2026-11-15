"""Supervisor：分派任务、校验结果、按反馈重试（M1 只做骨架）。

链条上的协作点都走这里：Agent 产出 → validator 判定 → 不合格就带反馈重跑，
重试次数有上限，避免"模型自我辩论"式死循环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from governance.agents.base import Agent, AgentResult
from governance.llm import LLMUnavailable

Validator = Callable[[AgentResult], bool]


@dataclass
class RetryOutcome:
    ok: bool
    attempts: int
    result: AgentResult | None
    history: list[dict[str, Any]] = field(default_factory=list)


class Supervisor:
    def __init__(self, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        self.max_attempts = max_attempts

    def run(
        self,
        agent: Agent,
        payload: dict[str, Any],
        validator: Validator | None = None,
    ) -> RetryOutcome:
        history: list[dict[str, Any]] = []
        current = dict(payload)

        for attempt in range(1, self.max_attempts + 1):
            try:
                result = agent.run(current)
            except LLMUnavailable as exc:
                history.append({"attempt": attempt, "error": str(exc)})
                return RetryOutcome(False, attempt, None, history)

            accepted = result.ok and (validator is None or validator(result))
            if accepted:
                history.append({"attempt": attempt, "accepted": True, "summary": result.summary})
                return RetryOutcome(True, attempt, result, history)

            feedback = result.issues or [{"detail": result.summary}]
            history.append({"attempt": attempt, "accepted": False, "feedback": feedback})
            current = {**current, "feedback": feedback}

        return RetryOutcome(False, self.max_attempts, None, history)
