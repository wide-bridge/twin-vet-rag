"""LLM access behind a small interface: JSON-schema output only (OpenAI Responses API structured outputs).

FakeLLM returns scripted outputs for tests; it never calls the network.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass
class LLMResult:
    data: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """Model call failed (network, timeout, refusal, invalid output). Retryable by the user."""


class LLM(Protocol):
    def complete_json(self, *, model: str, system: str, user: str, schema: dict, name: str,
                      reasoning: str = "minimal") -> LLMResult: ...


class OpenAILLM:
    def __init__(self, api_key: str, timeout: float = 60.0, max_retries: int = 2):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, name: str,
                      reasoning: str = "minimal") -> LLMResult:
        try:
            resp = self._client.responses.create(
                model=model,
                input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
                reasoning={"effort": reasoning},
            )
        except Exception as e:  # network / rate limit / server error after SDK retries
            raise LLMError(type(e).__name__) from e
        if getattr(resp, "status", "completed") != "completed":
            raise LLMError(f"incomplete:{getattr(resp, 'incomplete_details', None)}")
        try:
            data = json.loads(resp.output_text)
        except (json.JSONDecodeError, TypeError) as e:
            raise LLMError("invalid_json") from e
        u = resp.usage
        return LLMResult(data, model, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)


@dataclass
class FakeLLM:
    """Scripted responses keyed by schema name; a callable gets (system, user) and returns the dict."""
    responses: dict[str, dict | Callable[[str, str], dict] | Exception] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    delay: float = 0.0

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, name: str,
                      reasoning: str = "minimal") -> LLMResult:
        self.calls.append({"model": model, "name": name, "system": system, "user": user})
        if self.delay:
            time.sleep(self.delay)
        r = self.responses[name]
        if isinstance(r, Exception):
            raise r
        data = r(system, user) if callable(r) else r
        return LLMResult(dict(data), model, 100, 50)
