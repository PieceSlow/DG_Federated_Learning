"""LLM 接入层：provider 抽象、缓存、预算与审计。

设计原则：

* 默认 ``provider="off"``，任何 Agent 调用都会得到明确错误，而不是静默联网；
* ``mock`` 用于测试与演示，零网络、零成本；
* ``api`` 走 OpenAI 兼容接口（DeepSeek），key 只从环境变量读取，绝不写入仓库；
* 所有出域内容由调用方（Agent 的视图层）负责脱敏，本层不接触患者数据。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from governance.llm.budget import Budget, BudgetExceeded
from governance.llm.cache import ResponseCache
from governance.llm.usage import UsageLog


class LLMError(RuntimeError):
    """LLM 调用相关错误的基类。"""


class LLMUnavailable(LLMError):
    """未配置或明确关闭模型时抛出。"""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "off"  # off | mock | api
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4000  # 8 类对象 + 关系的 JSON 约 1.5k~2.5k tokens，1200 会被截断
    timeout_s: float = 60.0

    def validate(self) -> None:
        if self.provider not in {"off", "mock", "api"}:
            raise ValueError(f"未知 provider: {self.provider}（可选 off/mock/api）")
        if self.provider == "api" and not self.api_key:
            raise LLMUnavailable(
                "provider=api 需要环境变量 DEEPSEEK_API_KEY；"
                "没有 key 时请使用 --llm mock 或 --llm off。"
            )


def config_from_env(provider: str | None = None, model: str | None = None) -> LLMConfig:
    return LLMConfig(
        provider=(provider or os.environ.get("LLM_PROVIDER", "off")).lower(),
        model=model or os.environ.get("LLM_MODEL", "deepseek-flash"),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0")),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "4000")),
        timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "60")),
    )


@dataclass(frozen=True)
class LLMRequest:
    prompt_id: str
    system: str
    user: str
    schema: dict[str, Any] | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass
class LLMResponse:
    text: str
    parsed: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    cached: bool = False
    finish_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "parsed": self.parsed,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "finish_reason": self.finish_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMResponse":
        return cls(
            text=data["text"],
            parsed=data.get("parsed"),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            model=data.get("model", ""),
            finish_reason=data.get("finish_reason", ""),
        )


class Provider(Protocol):
    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse: ...


class OffProvider:
    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse:
        raise LLMUnavailable(
            f"LLM 已关闭（provider=off），无法执行 {request.prompt_id}；"
            "使用 --llm mock 演示或 --llm api 启用真实调用。"
        )


class ApiProvider:
    """OpenAI 兼容接口（DeepSeek 默认）。"""

    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse:
        import requests  # 仅 API 模式需要

        body: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature if request.temperature is not None else config.temperature,
            "max_tokens": request.max_tokens or config.max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        url = config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for _ in range(2):  # 一次重试，覆盖偶发网络/5xx
            try:
                response = requests.post(url, headers=headers, json=body, timeout=config.timeout_s)
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                text = choice["message"].get("content") or ""
                finish_reason = str(choice.get("finish_reason", ""))
                usage = data.get("usage", {})
                if not text:
                    raise LLMError(
                        "LLM 返回空内容"
                        f"（finish_reason={finish_reason}，"
                        f"completion_tokens={usage.get('completion_tokens')}）；"
                        "finish_reason=length 说明 max_tokens 太小，可用 LLM_MAX_TOKENS 调大"
                    )
                parsed = _parse_json(text)
                return LLMResponse(
                    text=text,
                    parsed=parsed,
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                    model=config.model,
                    finish_reason=finish_reason,
                )
            except Exception as exc:  # noqa: BLE001 - 统一转换为 LLMError
                last_error = exc
        raise LLMError(f"LLM 调用失败：{last_error}")


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 返回不是合法 JSON：{text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("LLM 返回的 JSON 顶层必须是对象")
    return parsed


class LLMService:
    """把 provider、缓存、预算、审计串起来，Agent 只依赖这一层。"""

    def __init__(
        self,
        config: LLMConfig,
        provider: Provider,
        *,
        cache: ResponseCache | None = None,
        budget: Budget | None = None,
        usage: UsageLog | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache
        self.budget = budget or Budget()
        self.usage = usage

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.budget.check()
        key = cache_key(request, self.config.model, request.max_tokens or self.config.max_tokens)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                response = LLMResponse.from_dict(cached)
                response.cached = True
                if self.usage is not None:
                    self.usage.log(
                        prompt_id=request.prompt_id,
                        model=response.model or self.config.model,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        cost_usd=0.0,
                        cached=True,
                    )
                return response

        response = self.provider.complete(request, self.config)
        cost = self.budget.record(response.prompt_tokens, response.completion_tokens)
        for message in self.budget.pop_warnings():
            print(message, file=sys.stderr)
        if self.cache is not None:
            self.cache.put(key, response.to_dict())
        if self.usage is not None:
            self.usage.log(
                prompt_id=request.prompt_id,
                model=response.model or self.config.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=cost,
                cached=False,
            )
        return response


def cache_key(request: LLMRequest, model: str, max_tokens: int = 0) -> str:
    import hashlib

    payload = json.dumps(
        _stringify_keys(
            {
                "model": model,
                "max_tokens": max_tokens,
                "prompt_id": request.prompt_id,
                "system": request.system,
                "user": request.user,
                "schema": request.schema,
            }
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_service(
    config: LLMConfig,
    output_dir: Path,
    *,
    mock_responses: dict[str, Any] | None = None,
    budget: Budget | None = None,
) -> LLMService:
    """按 config 组装服务；mock/api 分别使用对应 provider。"""
    config.validate()
    cache = ResponseCache(output_dir / "llm_cache") if config.provider != "off" else None
    usage = UsageLog(output_dir / "audit" / "llm_usage.jsonl") if config.provider != "off" else None

    if config.provider == "off":
        provider: Provider = OffProvider()
    elif config.provider == "mock":
        from governance.llm.mock import MockProvider

        provider = MockProvider(mock_responses)
    else:
        provider = ApiProvider()

    return LLMService(config, provider, cache=cache, budget=budget, usage=usage)


def _stringify_keys(value: Any) -> Any:
    """YAML 会把 ``on`` 解析成布尔键，这里统一转成字符串，保证缓存键稳定。"""
    if isinstance(value, dict):
        return {str(key): _stringify_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_keys(item) for item in value]
    return value
