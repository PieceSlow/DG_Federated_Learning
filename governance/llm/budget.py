"""预算护栏：调用次数与费用上限，超限直接拒绝，避免跑飞。

默认价格按 DeepSeek 文档的 ``deepseek-flash`` 高峰价（USD / 1M tokens），
可用环境变量覆盖；价格只用于估算与护栏，不是账单依据。
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """调用次数或费用超出预算。"""


@dataclass
class Budget:
    max_calls: int = 200
    max_usd: float = 1.0
    input_usd_per_m: float = 0.30
    output_usd_per_m: float = 1.20

    calls: int = 0
    usd: float = 0.0

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"已达到调用上限 {self.max_calls} 次")
        if self.usd >= self.max_usd:
            raise BudgetExceeded(f"已达到费用上限 ${self.max_usd:.2f}")

    def record(self, prompt_tokens: int, completion_tokens: int) -> float:
        cost = (
            prompt_tokens / 1_000_000 * self.input_usd_per_m
            + completion_tokens / 1_000_000 * self.output_usd_per_m
        )
        self.calls += 1
        self.usd += cost
        return cost
