# FILE: src/mint/llm.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: OpenAI-compatible LLM client for document generation
#   SCOPE: Send skill prompt to model API, return response text
#   DEPENDS: M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-CREATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LLMClient - httpx-based OpenAI-compatible client
#   call_model - send prompt to model and return response
# END_MODULE_MAP

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


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "glm-5",
        timeout: int = 120,
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
            "max_tokens": 4096,
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

        text = choices[0].get("message", {}).get("content", "")
        model = data.get("model", self._model)
        usage = data.get("usage", {})

        logger.info(
            "[LLM][call][BLOCK_CALL_MODEL] "
            "model=%s, tokens=%s, duration=%dms",
            model,
            usage,
            duration_ms,
        )

        return LLMResponse(
            text=text,
            model=model,
            usage=usage,
            duration_ms=duration_ms,
        )
    # END_BLOCK_CALL_MODEL
