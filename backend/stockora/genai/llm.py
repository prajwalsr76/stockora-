"""LLM access: Groq (OpenAI-compatible API) with tracing, cost tracking, retries and routing.

* Cheap-first routing: a small fast model for routing/parsing, a large model only
  when needed, a vision model for bill photos.
* Every call is traced (tokens, latency, cost, success) to llm_traces.
* FakeLLM gives deterministic, offline behaviour for tests and evals.

Model IDs change over time: override them in .env (see console.groq.com/docs/models).
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# USD per 1M tokens (input, output). Indicative; update from Groq's pricing page.
PRICES = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.8-27b": (0.80, 4.00),
}

# Default model IDs, checked against console.groq.com/docs/models and /docs/deprecations on 11 Nov 2026.
# The earlier defaults were shut down by Groq for free and developer accounts:
#   llama-3.1-8b-instant and llama-3.3-70b-versatile on 16 Aug 2026,
#   meta-llama/llama-4-scout-17b-16e-instruct on 17 Jul 2026.
# NOT yet verified with a real key: run `python -m evals.assistant_eval --mode agent` on your PC.
DEFAULT_MODELS = {
    "fast": "openai/gpt-oss-20b",
    "smart": "openai/gpt-oss-120b",
    "vision": "qwen/qwen3.8-27b",      # preview model; Groq describes it as multimodal with tool use
}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict] = field(default_factory=list)

    def json(self) -> Any:
        """Parse a JSON answer, tolerating ```json fences."""
        t = self.text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(t)


class LLM(Protocol):
    def chat(self, messages: list[dict], *, tier: str = "smart", json_mode: bool = False,
             tools: list[dict] | None = None, agent: str = "unknown", max_tokens: int = 1024) -> LLMResponse: ...


TraceSink = Callable[[str, LLMResponse, bool, str | None], None]


class GroqLLM:
    def __init__(self, api_key: str | None = None, trace: TraceSink | None = None, timeout: float = 45.0):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.models = {
            "fast": os.getenv("GROQ_FAST_MODEL") or DEFAULT_MODELS["fast"],
            "smart": os.getenv("GROQ_MODEL") or DEFAULT_MODELS["smart"],
            "vision": os.getenv("GROQ_VISION_MODEL") or DEFAULT_MODELS["vision"],
        }
        # gpt-oss models think before answering; "low" keeps answers fast and leaves room for the reply.
        # Set GROQ_REASONING_EFFORT=off in .env to stop sending this setting.
        self.reasoning_effort = os.getenv("GROQ_REASONING_EFFORT") or "low"
        self.trace = trace
        self.timeout = timeout

    max_attempts = 5            # rate limits (429) are common on the free tier, so retry patiently
    max_wait_s = 30.0

    def _wait(self, r, attempt: int) -> float:
        """Seconds to wait before retrying: Groq's retry-after header if present, else exponential backoff."""
        try:
            hinted = float(r.headers.get("retry-after", ""))
        except (TypeError, ValueError):
            hinted = 0.0
        return min(self.max_wait_s, max(hinted, 2.0 * 2 ** attempt))

    def chat(self, messages: list[dict], *, tier: str = "smart", json_mode: bool = False,
             tools: list[dict] | None = None, agent: str = "unknown", max_tokens: int = 1024) -> LLMResponse:
        model = self.models[tier]
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1,
                                "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if model.startswith("openai/gpt-oss") and self.reasoning_effort.lower() != "off":
            body["reasoning_effort"] = self.reasoning_effort
        start = time.perf_counter()
        last_err = None
        for attempt in range(self.max_attempts):
            try:
                r = requests.post(GROQ_URL, json=body, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
                if r.status_code == 400 and "reasoning_effort" in body:
                    body.pop("reasoning_effort")      # model does not accept the setting: retry without it
                    r = requests.post(GROQ_URL, json=body, timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self.api_key}"})
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = RuntimeError(f"Groq {r.status_code}: {r.text[:200]}")
                    if attempt + 1 < self.max_attempts:
                        time.sleep(self._wait(r, attempt))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
                data = r.json()
                msg = data["choices"][0]["message"]
                usage = data.get("usage", {})
                pin, pout = PRICES.get(model, (0.0, 0.0))
                resp = LLMResponse(
                    text=msg.get("content") or "", model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    tool_calls=msg.get("tool_calls") or [])
                resp.cost_usd = round((resp.prompt_tokens * pin + resp.completion_tokens * pout) / 1e6, 6)
                if self.trace:
                    self.trace(agent, resp, True, None)
                return resp
            except Exception as e:  # noqa: BLE001 - retried then surfaced
                last_err = e
                if attempt + 1 < self.max_attempts:
                    time.sleep(1.5 * (attempt + 1))
        fail = LLMResponse("", model, latency_ms=int((time.perf_counter() - start) * 1000))
        if self.trace:
            self.trace(agent, fail, False, str(last_err)[:300])
        raise RuntimeError(f"LLM call failed after retries: {last_err}")


def image_message(prompt: str, image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]}


class FakeLLM:
    """Deterministic stand-in. `responder(messages, tier, agent)` returns the text."""

    def __init__(self, responder: Callable[[list[dict], str, str], str] | None = None):
        self.responder = responder or (lambda m, t, a: "")
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], *, tier: str = "smart", json_mode: bool = False,
             tools: list[dict] | None = None, agent: str = "unknown", max_tokens: int = 1024) -> LLMResponse:
        self.calls.append({"tier": tier, "agent": agent, "messages": messages})
        return LLMResponse(self.responder(messages, tier, agent), f"fake-{tier}")


def repo_trace_sink(repo) -> TraceSink:
    def sink(agent: str, resp: LLMResponse, ok: bool, err: str | None) -> None:
        repo.log_trace(agent, resp.model, resp.prompt_tokens, resp.completion_tokens,
                       resp.latency_ms, resp.cost_usd, ok, err)
        repo.conn.commit()
    return sink


def default_llm(repo=None) -> LLM | None:
    """Groq if a key is configured, else None (callers fall back to deterministic paths)."""
    if not os.getenv("GROQ_API_KEY"):
        return None
    return GroqLLM(trace=repo_trace_sink(repo) if repo is not None else None)
