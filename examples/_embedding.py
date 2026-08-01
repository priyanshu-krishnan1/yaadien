"""Tiny, dependency-free deterministic pseudo-embedding used by the examples.

Not a real semantic embedding model — it exists so these scripts run
end-to-end with zero API keys and zero extra installs. Swap in a real
EmbeddingProvider (OpenAI, sentence-transformers, etc.) for actual use;
see agent_memory_sdk.types.EmbeddingProvider.
"""

from __future__ import annotations

import hashlib


def fake_embedding(text: str, dim: int = 1536) -> list[float]:
    vec = [0.0] * dim
    for token in text.lower().split():
        h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]
