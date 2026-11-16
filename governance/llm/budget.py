"""预算护栏：调用次数与费用上限，超限直接拒绝，避免跑飞。

默认价格按 DeepSeek 文档的 ``deepseek-flash`` 高峰价（USD / 1M tokens），
可用环境变量覆盖；价格只用于估算与护栏，不是账单依据。

预算可以按美元或人民币设置（默认人民币 20 元），达到 50%/80% 打警告，
100% 拒绝后续调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """调用次数或费用超出预算。"""


@dataclass
class Budget:
    max_calls: int = 200
    max_usd: float | None = None
    max_cny: float | None = 20.0
    usd_to_cny: float = 7.2
    input_usd_per_m: float = 0.30
    output_usd_per_m: float = 1.20
    warn_at: tuple[float, ...] = (0.5, 0.8, 1.0)

    calls: int = 0
    usd: float = 0.0
    _warned: set[float] = field(default_factory=set)
    _warnings: list[str] = field(default_factory=list)

    @property
    def limit_usd(self) -> float:
        limits = [value for value in (self.max_usd, self.max_cny / self.usd_to_cny if self.max_cny else None) if value is not None]
        return min(limits) if limits else float("inf")

    @property
    def spent_cny(self) -> float:
        return self.usd * self.usd_to_cny

    def _limits(self) -> str:
        if self.max_usd is not None and self.max_cny is not None:
            return f"${self.max_usd:.2f} / ¥{self.max_cny:.2f}"
        if self.max_cny is not None:
            return f"¥{self.max_cny:.2f}"
        return f"${self.max_usd:.2f}" if self.max_usd is not None else "无"

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"已达到调用上限 {self.max_calls} 次")
        if self.usd >= self.limit_usd:
            raise BudgetExceeded(
                f"已达到费用上限（{self._limits()}，已花 ¥{self.spent_cny:.2f}）"
            )

    def record(self, prompt_tokens: int, completion_tokens: int) -> float:
        cost = (
            prompt_tokens / 1_000_000 * self.input_usd_per_m
            + completion_tokens / 1_000_000 * self.output_usd_per_m
        )
        self.calls += 1
        self.usd += cost
        limit = self.limit_usd
        if limit > 0:
            ratio = self.usd / limit
            for threshold in self.warn_at:
                if ratio >= threshold and threshold not in self._warned:
                    self._warned.add(threshold)
                    self._warnings.append(
                        f"[预算预警] 已用 {ratio:.0%}（¥{self.spent_cny:.2f} / {self._limits()}）"
                    )
        return cost

    def pop_warnings(self) -> list[str]:
        warnings, self._warnings = self._warnings, []
        return warnings

    def status(self) -> dict:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "spent_cny": round(self.spent_cny, 4),
            "limit": self._limits(),
        }
