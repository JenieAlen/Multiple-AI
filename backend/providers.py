"""
AI provider integrations.

Each provider exposes an async `ask(question, system=None, role="ask")`
coroutine and an `is_available()` helper. Missing API keys make the provider
gracefully unavailable rather than crashing the app.

Providers:
  * GroqProvider        — Llama models via Groq (OpenAI-compatible API)
  * OpenRouterProvider  — Free/cheap models via OpenRouter (OpenAI-compatible)
  * GoogleProvider      — Gemini (judge only, not in build_providers)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

from . import admin as _admin

# ---- Optional SDK imports -------------------------------------------------
# Groq and Together both speak the OpenAI chat-completions wire format, so we
# reuse `openai.AsyncOpenAI` and just point base_url at their endpoints.
try:
    from openai import AsyncOpenAI  # type: ignore
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore

try:
    from google import genai as genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore
except ImportError:  # pragma: no cover
    genai = None  # type: ignore
    genai_types = None  # type: ignore


DEFAULT_SYSTEM = (
    "You are a helpful, accurate, and concise assistant. "
    "Answer the user's question directly and clearly. "
    "If the question is ambiguous, state your assumption and answer."
)


@dataclass
class ProviderAnswer:
    provider: str          # e.g. "groq"
    model: str             # the model id actually used
    label: str             # human-readable label (e.g. "Groq")
    answer: str            # text answer (or error message)
    latency_ms: int        # wall-clock latency
    error: Optional[str] = None  # set if the call failed
    input_tokens: int = 0
    output_tokens: int = 0


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _log_usage(answer: ProviderAnswer, role: str) -> None:
    """Persist one usage row into the admin in-memory log."""
    try:
        _admin.record_usage(
            provider=answer.provider,
            label=answer.label,
            model=answer.model,
            role=role,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            latency_ms=answer.latency_ms,
            error=answer.error,
        )
    except Exception:
        pass


# ---- OpenAI-compatible base class -----------------------------------------
class _OpenAICompatProvider:
    """
    Shared logic for providers that expose an OpenAI-compatible
    /v1/chat/completions endpoint. Subclasses only set class-level attributes.
    """

    name = ""
    label = ""
    base_url = ""
    env_key = ""
    env_model = ""
    default_model = ""

    def __init__(self) -> None:
        self.api_key = os.getenv(self.env_key, "").strip()
        self.model = os.getenv(self.env_model, self.default_model).strip()
        self._client = None
        if self.api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def is_available(self) -> bool:
        return self._client is not None

    async def ask(
        self,
        question: str,
        system: str = DEFAULT_SYSTEM,
        role: str = "ask",
    ) -> ProviderAnswer:
        start = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(  # type: ignore[union-attr]
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                max_tokens=1024,
            )
            text = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            in_tok = _safe_int(getattr(usage, "prompt_tokens", 0))
            out_tok = _safe_int(getattr(usage, "completion_tokens", 0))
            latency_ms = int((time.monotonic() - start) * 1000)
            answer = ProviderAnswer(
                provider=self.name,
                model=self.model,
                label=self.label,
                answer=text or "(empty response)",
                latency_ms=latency_ms,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            answer = ProviderAnswer(
                provider=self.name,
                model=self.model,
                label=self.label,
                answer="",
                latency_ms=latency_ms,
                error=str(exc),
            )
        _log_usage(answer, role)
        return answer


# ---- Groq -----------------------------------------------------------------
class GroqProvider(_OpenAICompatProvider):
    name = "groq"
    label = "Groq"
    base_url = "https://api.groq.com/openai/v1"
    env_key = "GROQ_API_KEY"
    env_model = "GROQ_MODEL"
    default_model = "llama-3.3-70b-versatile"


# ---- OpenRouter -----------------------------------------------------------
class OpenRouterProvider(_OpenAICompatProvider):
    name = "openrouter"
    label = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    env_key = "OPENROUTER_API_KEY"
    env_model = "OPENROUTER_MODEL"
    default_model = "openrouter/auto"


# ---- Google (Gemini) ------------------------------------------------------
class GoogleProvider:
    name = "google"
    label = "Gemini"

    def __init__(self) -> None:
        self.api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        self.model = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash").strip()
        self._client = None
        if self.api_key and genai is not None:
            self._client = genai.Client(api_key=self.api_key)

    def is_available(self) -> bool:
        return self._client is not None

    async def ask(
        self,
        question: str,
        system: str = DEFAULT_SYSTEM,
        role: str = "ask",
    ) -> ProviderAnswer:
        start = time.monotonic()
        try:
            cfg = genai_types.GenerateContentConfig(  # type: ignore[union-attr]
                system_instruction=system,
            )
            resp = await self._client.aio.models.generate_content(  # type: ignore[union-attr]
                model=self.model,
                contents=question,
                config=cfg,
            )
            text = (getattr(resp, "text", "") or "").strip()
            meta = getattr(resp, "usage_metadata", None)
            in_tok = _safe_int(getattr(meta, "prompt_token_count", 0))
            out_tok = _safe_int(getattr(meta, "candidates_token_count", 0))
            latency_ms = int((time.monotonic() - start) * 1000)
            answer = ProviderAnswer(
                provider=self.name,
                model=self.model,
                label=self.label,
                answer=text or "(empty response)",
                latency_ms=latency_ms,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            answer = ProviderAnswer(
                provider=self.name,
                model=self.model,
                label=self.label,
                answer="",
                latency_ms=latency_ms,
                error=str(exc),
            )
        _log_usage(answer, role)
        return answer


# ---- Registry -------------------------------------------------------------
def build_providers() -> list:
    """Return providers that are available and not manually disabled."""
    disabled = _admin.state.get_disabled_providers()
    candidates = [GroqProvider(), OpenRouterProvider()]
    return [p for p in candidates if p.is_available() and p.name not in disabled]


async def ask_all(question: str) -> list[ProviderAnswer]:
    providers = build_providers()
    if not providers:
        return []
    results = await asyncio.gather(*(p.ask(question) for p in providers))
    return list(results)
