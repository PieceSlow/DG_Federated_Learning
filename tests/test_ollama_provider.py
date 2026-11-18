"""Ollama provider 测试：原生 /api/chat、关闭思考、上下文放大。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from governance.llm import LLMConfig, LLMError, LLMRequest, build_service, ollama_url
from governance.llm.ollama import DEFAULT_NUM_CTX


def _request(schema: dict | None = None) -> LLMRequest:
    return LLMRequest(
        prompt_id="aligner",
        system="你是数据对齐智能体",
        user='{"targets": []}',
        schema=schema,
    )


def _reply(content: str, done_reason: str = "stop") -> mock.Mock:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "model": "qwen3:8b",
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 1200,
        "eval_count": 40,
        "done_reason": done_reason,
    }
    return response


class OllamaProviderTest(unittest.TestCase):
    def _service(self, root: Path, **config):
        settings = {
            "provider": "ollama",
            "model": "qwen3:8b",
            "base_url": "http://127.0.0.1:11434",
            "timeout_s": 600,
        }
        settings.update(config)
        return build_service(LLMConfig(**settings), root)

    def test_hits_native_chat_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ) as post:
            self._service(Path(tmp)).complete(_request())
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11434/api/chat")

    def test_thinking_is_disabled(self) -> None:
        """关键：请求体必须带 think=false，否则 qwen3 会先写几千 token 思维链。"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ) as post:
            self._service(Path(tmp)).complete(_request())
        body = post.call_args.kwargs["json"]
        self.assertIs(body["think"], False)
        self.assertFalse(body["stream"])

    def test_num_ctx_and_num_predict_are_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ) as post:
            self._service(Path(tmp), max_tokens=3000, num_ctx=8192).complete(_request())
        options = post.call_args.kwargs["json"]["options"]
        self.assertEqual(options["num_predict"], 3000)
        self.assertEqual(options["num_ctx"], 8192)

    def test_default_num_ctx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ) as post:
            self._service(Path(tmp)).complete(_request())
        self.assertEqual(post.call_args.kwargs["json"]["options"]["num_ctx"], DEFAULT_NUM_CTX)

    def test_usage_and_model_are_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ):
            response = self._service(Path(tmp)).complete(_request())
        self.assertEqual(response.prompt_tokens, 1200)
        self.assertEqual(response.completion_tokens, 40)
        self.assertEqual(response.model, "qwen3:8b")
        self.assertEqual(response.parsed, {"mappings": []})

    def test_schema_is_sent(self) -> None:
        schema = {"type": "object", "properties": {"mappings": {"type": "array"}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply('{"mappings": []}')
        ) as post:
            self._service(Path(tmp)).complete(_request(schema))
        system = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("JSON Schema", system)
        self.assertIn('"mappings"', system)

    def test_empty_content_raises_llm_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", return_value=_reply("", done_reason="length")
        ):
            with self.assertRaises(LLMError) as raised:
                self._service(Path(tmp)).complete(_request())
        self.assertIn("空内容", str(raised.exception))

    def test_unreachable_reports_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "requests.post", side_effect=ConnectionError("refused")
        ):
            with self.assertRaises(LLMError) as raised:
                self._service(Path(tmp)).complete(_request())
        self.assertIn("ollama serve", str(raised.exception))

    def test_ollama_does_not_require_api_key(self) -> None:
        LLMConfig(provider="ollama").validate()


class OllamaUrlTest(unittest.TestCase):
    def test_url_has_no_v1_suffix(self) -> None:
        self.assertEqual(ollama_url("http://127.0.0.1:11434"), "http://127.0.0.1:11434/api/chat")

    def test_v1_suffix_is_stripped(self) -> None:
        """用户很容易把 /v1 一起写进来，这里做一次兜底。"""
        self.assertEqual(
            ollama_url("http://127.0.0.1:11434/v1"), "http://127.0.0.1:11434/api/chat"
        )
        self.assertEqual(
            ollama_url("http://127.0.0.1:11434/v1/"), "http://127.0.0.1:11434/api/chat"
        )


if __name__ == "__main__":
    unittest.main()
