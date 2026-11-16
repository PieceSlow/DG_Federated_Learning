"""本地 .env 与人民币预算测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from governance.llm.budget import Budget, BudgetExceeded
from governance.llm.env import load_env_file, parse_env_file


class EnvFileTest(unittest.TestCase):
    def test_parse_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# comment\nDEEPSEEK_API_KEY=sk-test\nLLM_MODEL=deepseek-chat\n", encoding="utf-8")
            values = parse_env_file(path)
            self.assertEqual(values["DEEPSEEK_API_KEY"], "sk-test")
            self.assertEqual(load_env_file(path)["LLM_MODEL"], "deepseek-chat")


class BudgetTest(unittest.TestCase):
    def test_cny_warnings_and_stop(self) -> None:
        budget = Budget(
            max_cny=10,
            usd_to_cny=1.0,
            input_usd_per_m=1_000_000,
            output_usd_per_m=0,
            warn_at=(0.5, 0.8),
        )
        budget.record(3, 0)  # 30%
        self.assertEqual(budget.pop_warnings(), [])
        budget.record(2, 0)  # 50%
        self.assertEqual(len(budget.pop_warnings()), 1)
        budget.record(3, 0)  # 80%
        self.assertEqual(len(budget.pop_warnings()), 1)
        budget.record(2, 0)  # 100%
        with self.assertRaises(BudgetExceeded):
            budget.check()
        self.assertAlmostEqual(budget.spent_cny, 10.0)

    def test_limit_uses_minimum(self) -> None:
        budget = Budget(max_usd=1.0, max_cny=7.2, usd_to_cny=7.2)
        self.assertEqual(budget.limit_usd, 1.0)
