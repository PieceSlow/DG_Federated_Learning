"""LLM 接入层测试：mock、缓存、预算、审计、无网络与关闭时的降级。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from governance.llm import (
    LLMConfig,
    LLMRequest,
    LLMUnavailable,
    build_service,
)
from governance.llm.budget import Budget, BudgetExceeded
from governance.llm.cache import ResponseCache
from governance.llm.client import LLMService
from governance.llm.mock import MockProvider
from governance.llm.usage import UsageLog


def _request() -> LLMRequest:
    return LLMRequest(prompt_id="profiler", system="你是画像智能体", user='{"tables": []}')


class ConfigTest(unittest.TestCase):
    def test_unknown_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LLMConfig(provider="gpt").validate()

    def test_api_requires_key(self) -> None:
        with self.assertRaises(LLMUnavailable):
            LLMConfig(provider="api", api_key=None).validate()

    def test_off_is_default(self) -> None:
        self.assertEqual(LLMConfig().provider, "off")


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.provider = MockProvider()
        self.service = LLMService(
            LLMConfig(provider="mock"),
            self.provider,
            cache=ResponseCache(self.root / "cache"),
            budget=Budget(max_calls=5, max_usd=1.0),
            usage=UsageLog(self.root / "usage.jsonl"),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mock_returns_parsed_json(self) -> None:
        response = self.service.complete(_request())
        self.assertIsNotNone(response.parsed)
        self.assertIn("objects", response.parsed)
        self.assertEqual(response.model, "mock")

    def test_cache_hit_avoids_second_call(self) -> None:
        first = self.service.complete(_request())
        second = self.service.complete(_request())
        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)

    def test_usage_log_records_both_calls(self) -> None:
        self.service.complete(_request())
        self.service.complete(_request())
        lines = (self.root / "usage.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertFalse(json.loads(lines[0])["cached"])
        self.assertTrue(json.loads(lines[1])["cached"])

    def test_budget_stops_runaway_calls(self) -> None:
        service = LLMService(
            LLMConfig(provider="mock"),
            MockProvider(),
            budget=Budget(max_calls=1, max_usd=1.0),
        )
        service.complete(_request())
        with self.assertRaises(BudgetExceeded):
            service.complete(_request())

    def test_mock_never_touches_network(self) -> None:
        with mock.patch("socket.socket", side_effect=AssertionError("不应联网")):
            response = self.service.complete(_request())
        self.assertIsNotNone(response.parsed)


class OffModeTest(unittest.TestCase):
    def test_off_provider_raises_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = build_service(LLMConfig(provider="off"), Path(tmp))
        with self.assertRaises(LLMUnavailable) as raised:
            service.complete(_request())
        self.assertIn("provider=off", str(raised.exception))

    def test_api_mode_without_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LLMUnavailable):
                build_service(LLMConfig(provider="api", api_key=None), Path(tmp))


if __name__ == "__main__":
    unittest.main()
