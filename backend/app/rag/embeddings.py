"""Embedding providers. OpenAI for real runs; a deterministic fake for tests and offline dev.

Fake vectors are NOT semantic — they exist only to exercise storage, versioning and filters.
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Protocol, Sequence

import numpy as np

from ..db.models import EMBEDDING_DIMENSIONS


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FakeEmbedder:
    model = "fake-hash-embedding"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:8], "big")
            v = np.random.default_rng(seed).standard_normal(self.dimensions)
            out.append((v / np.linalg.norm(v)).tolist())
        return out


class OpenAIEmbedder:
    """Batches by item count and token budget; retries transient errors with backoff."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", max_batch: int = 256, max_retries: int = 5):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.dimensions = EMBEDDING_DIMENSIONS
        self.max_batch = max_batch
        self.max_retries = max_retries
        self.usage_tokens = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for i in range(0, len(texts), self.max_batch):
            batch = list(texts[i : i + self.max_batch])
            for attempt in range(self.max_retries):
                try:
                    resp = self._client.embeddings.create(model=self.model, input=batch)
                    break
                except Exception as e:  # rate limit / transient network
                    status = getattr(e, "status_code", None)
                    if status is not None and status < 500 and status != 429:
                        raise
                    if attempt == self.max_retries - 1:
                        raise
                    time.sleep(min(30, 2 ** attempt))
            self.usage_tokens += getattr(resp.usage, "total_tokens", 0) or 0
            vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            if any(len(v) != self.dimensions for v in vectors):
                raise ValueError("unexpected embedding dimensions")
            result.extend(vectors)
        return result


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
