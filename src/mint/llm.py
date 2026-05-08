# FILE: src/mint/llm.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: OpenAI-compatible LLM client for document generation
#   SCOPE: Send skill prompt to model API, return response text
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-LLM
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LLMClient - httpx-based OpenAI-compatible client
#   LLMResponse - response dataclass with text, model, usage, duration_ms
#   LLMCallError - exception for LLM call failures
#   call - send prompt to model and return response
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMCallError(Exception):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int]
    duration_ms: int
    # Reasoning emitted by thinking-capable models (Ollama: separate `reasoning`
    # field on the message). Empty for non-thinking models. Filled even when
    # `text` (content) is empty, which lets callers distinguish "model produced
    # nothing" from "model spent the whole budget on reasoning".
    reasoning: str = ""
    finish_reason: str = ""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "glm-5",
        timeout: int = 300,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    # START_BLOCK_CALL_MODEL
    def call(self, prompt: str, system: str | None = None) -> LLMResponse:
        import time

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 65536,
            "options": {
                "num_predict": 65536,
            },
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"

        start = time.monotonic()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMCallError(f"LLM request timed out after {self._timeout}s") from exc
        except httpx.ConnectError as exc:
            raise LLMCallError(f"Cannot connect to LLM at {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMCallError(f"LLM request failed: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            raise LLMCallError(
                f"LLM returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise LLMCallError(f"Invalid JSON from LLM: {exc}") from exc

        choices = data.get("choices", [])
        if not choices:
            raise LLMCallError("LLM returned no choices")

        message = choices[0].get("message", {}) or {}
        text = message.get("content", "") or ""
        # Ollama exposes reasoning on a separate field for thinking-capable
        # models (qwen3, glm-4.7-flash, gpt-oss, gemma4, …). Surface it on
        # LLMResponse so callers can distinguish "stuck in reasoning" from
        # "produced nothing".
        reasoning = message.get("reasoning", "") or ""
        finish_reason = choices[0].get("finish_reason", "") or ""
        model = data.get("model", self._model)
        usage = data.get("usage", {})

        if not text and reasoning and finish_reason == "length":
            logger.warning(
                "[LLM][call][BLOCK_CALL_MODEL] "
                "model=%s exhausted budget INSIDE reasoning "
                "(content_len=0, reasoning_len=%d, finish_reason=length, "
                "tokens=%s); the prompt is too long-tailed for this "
                "thinking-mode model",
                model,
                len(reasoning),
                usage,
            )
        else:
            logger.info(
                "[LLM][call][BLOCK_CALL_MODEL] "
                "model=%s, content_len=%d, reasoning_len=%d, "
                "finish_reason=%s, tokens=%s, duration=%dms",
                model,
                len(text),
                len(reasoning),
                finish_reason,
                usage,
                duration_ms,
            )

        return LLMResponse(
            text=text,
            model=model,
            usage=usage,
            duration_ms=duration_ms,
            reasoning=reasoning,
            finish_reason=finish_reason,
        )
    # END_BLOCK_CALL_MODEL
