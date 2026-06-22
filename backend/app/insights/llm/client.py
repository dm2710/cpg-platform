"""
DeepSeek LLM client.

DeepSeek exposes an OpenAI-compatible REST API at /chat/completions,
so we talk to it directly over httpx rather than pulling in the
openai SDK. Model used: deepseek-chat.

Handles:
  - Async completion with tenacity retry (network/timeout errors only)
  - Token usage tracking
  - Rate-limit (429) and insufficient-balance (402) error surfacing
  - Streaming (SSE) for the chat-style endpoints
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger

log      = get_logger(__name__)
settings = get_settings()

DEEPSEEK_MODEL  = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES     = 3


@dataclass
class LLMResponse:
    text:              str
    model:             str
    tokens_prompt:     int = 0
    tokens_completion: int = 0
    tokens_total:      int = 0
    latency_ms:        int = 0
    finish_reason:     str = "stop"


class DeepSeekError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(DeepSeekError):
    pass


class InsufficientBalanceError(DeepSeekError):
    pass


class DeepSeekClient:
    """Thin async wrapper around DeepSeek's OpenAI-compatible chat API."""

    def __init__(
        self,
        api_key:  Optional[str] = None,
        base_url: Optional[str] = None,
        model:    str = DEEPSEEK_MODEL,
        timeout:  float = DEFAULT_TIMEOUT,
    ):
        self.api_key  = api_key  or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model    = model
        self.timeout  = timeout

        if not self.api_key or self.api_key == "your-deepseek-key-here":
            log.warning("deepseek.no_api_key",
                       hint="Set DEEPSEEK_API_KEY in .env or docker-compose.yml")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    async def complete(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float = 0.3,
        max_tokens:    int   = 1500,
    ) -> LLMResponse:
        """Single-turn chat completion with retry on transient network errors."""
        t0      = time.monotonic()
        payload = {
            "model":       self.model,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=2, max=20),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                _raise_for_status(resp)
                data = resp.json()

        latency = int((time.monotonic() - t0) * 1000)
        choice  = data["choices"][0]
        usage   = data.get("usage", {})

        log.info(
            "deepseek.complete",
            model=self.model,
            tokens=usage.get("total_tokens", 0),
            latency_ms=latency,
        )

        return LLMResponse(
            text=choice["message"]["content"],
            model=data.get("model", self.model),
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            tokens_total=usage.get("total_tokens", 0),
            latency_ms=latency,
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def stream(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float = 0.3,
        max_tokens:    int   = 1500,
    ) -> AsyncIterator[str]:
        """Streaming completion — yields text chunks as they arrive."""
        payload = {
            "model":       self.model,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "stream":      True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                _raise_for_status(resp)
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        return
                    try:
                        import json
                        delta = json.loads(chunk)["choices"][0]["delta"]
                        if text := delta.get("content", ""):
                            yield text
                    except Exception:
                        continue


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    try:
        body = resp.json()
        msg  = body.get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text

    if resp.status_code == 429:
        raise RateLimitError(f"DeepSeek rate limited: {msg}", resp.status_code)
    if resp.status_code == 402 or "Insufficient Balance" in msg:
        raise InsufficientBalanceError(
            "DeepSeek account has no credits. Top up at platform.deepseek.com",
            resp.status_code,
        )
    raise DeepSeekError(f"DeepSeek API error {resp.status_code}: {msg}", resp.status_code)


# ── Module-level singleton ────────────────────────────────
_client: Optional[DeepSeekClient] = None


def get_llm_client() -> DeepSeekClient:
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client


def is_llm_configured() -> bool:
    """Return True only when a real (non-placeholder) API key is present."""
    key = get_settings().deepseek_api_key
    return bool(key) and key != "your-deepseek-key-here"
