"""本机 Ollama provider：调用原生 ``/api/chat``，并在请求里关闭思考。

原生接口的好处是可以按请求传 ``think`` 与 ``options.num_ctx``：
前者让思考型模型直接给答案，后者让长提示词不必裁剪。

它只实现 :class:`governance.llm.client.Provider` 协议，复用原有的
缓存、预算与审计；``off`` / ``mock`` / ``api`` 三个 provider 的行为不受影响。
"""

from __future__ import annotations

from typing import Any

from governance.llm.client import (
    LLMConfig,
    LLMError,
    LLMRequest,
    LLMResponse,
    _parse_json,
    _system_with_schema,
)

#: 本机模型默认上下文；qwen3:8b 支持 40960，但 16k 已足够放下候选视图 + 输出
DEFAULT_NUM_CTX = 16384


def ollama_url(base_url: str) -> str:
    """把 OpenAI 风格的 ``/v1`` 路径归一成原生 ``/api/chat``。"""
    root = str(base_url).rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root + "/api/chat"


class OllamaProvider:
    """调用 ``POST {base_url}/api/chat``，强制 ``think=false``。"""

    def complete(self, request: LLMRequest, config: LLMConfig) -> LLMResponse:
        import requests  # 仅在真实调用时导入

        body: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": _system_with_schema(request)},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "think": False,  # 关键：关闭思考型模型的思维链（qwen3 等）
            "options": {
                "temperature": (
                    request.temperature
                    if request.temperature is not None
                    else config.temperature
                ),
                "num_predict": request.max_tokens or config.max_tokens,
                "num_ctx": config.num_ctx or DEFAULT_NUM_CTX,
            },
        }
        url = ollama_url(config.base_url)
        last_error: Exception | None = None

        for _ in range(2):  # 一次重试，覆盖模型首次加载
            try:
                response = requests.post(url, json=body, timeout=config.timeout_s)
                response.raise_for_status()
                data = response.json()
                message = data.get("message") or {}
                text = message.get("content") or ""
                if not text:
                    raise LLMError(
                        "Ollama 返回空内容"
                        f"（done_reason={data.get('done_reason')}，"
                        f"eval_count={data.get('eval_count')}）；"
                        "检查是否误开了思考、或 num_predict 太小"
                    )
                return LLMResponse(
                    text=text,
                    parsed=_parse_json(text),
                    prompt_tokens=int(data.get("prompt_eval_count") or 0),
                    completion_tokens=int(data.get("eval_count") or 0),
                    model=str(data.get("model") or config.model),
                    finish_reason=str(data.get("done_reason") or ""),
                )
            except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError
                last_error = exc

        raise LLMError(
            f"Ollama 调用失败（{url}）：{last_error}；"
            "请确认 `ollama serve` 在跑、模型已 pull（ollama list），"
            "并把 --llm-base-url 指向 http://127.0.0.1:11434（不要带 /v1）"
        )
