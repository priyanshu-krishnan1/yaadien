"""
benchmarks/common/embedding_providers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
EmbeddingProvider implementations for the benchmark harness.

The SDK's ``EmbeddingProvider`` protocol (agent_memory_sdk.types) is just
``text -> list[float]`` — callers bring their own. This module provides three
tiers so the harness is runnable with zero setup, while making the honest
quality tradeoff of each tier explicit:

1. :class:`HashingEmbeddingProvider` — dependency-free, no network, no API
   key. A feature-hashed bag-of-words vector (stable hash, not Python's
   randomized ``hash()``). Good enough to exercise the harness end-to-end
   and produce a *repeatable* retrieval-quality number, but it captures
   lexical overlap, not semantics — reported numbers using this provider are
   NOT comparable to vendor-reported LongMemEval figures (which use real
   embedding models) and BENCHMARKS.md must say so explicitly. This is the
   default so `scripts/run_benchmarks.py` works out of the box.
2. :class:`SentenceTransformersEmbeddingProvider` — free, local, real
   semantic embeddings via the ``sentence-transformers`` package (no API key,
   no rate limit, runs on CPU). Requires ``pip install sentence-transformers``
   (a heavy optional dependency — not installed by default). This is the
   recommended provider for a retrieval-quality number worth comparing.
3. :class:`GeminiEmbeddingProvider` — hosted, free-tier (Google AI Studio),
   real embeddings, requires a ``GEMINI_API_KEY`` and the
   ``google-generativeai`` package. Subject to free-tier rate limits.

All three implement the same ``__call__(text: str) -> list[float]`` shape
required by ``agent_memory_sdk.types.EmbeddingProvider``.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class HashingEmbeddingProvider:
    """Deterministic, dependency-free feature-hashed bag-of-words embedding.

    Not a semantic embedding — two lexically disjoint but semantically
    similar sentences will NOT score as similar. Provided as the zero-setup
    default so the harness can be smoke-tested without any ML dependency or
    API key; swap in a real provider for a retrieval-quality number that
    means anything.

    Args:
        dim: Output vector dimension. Must match the target Db2 table's
            VECTOR column dimension (default 1536, matching the SDK's
            default schema).
    """

    def __init__(self, dim: int = 1536) -> None:
        self._dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            # A zero vector is a valid (if useless) embedding; avoids a
            # division-by-zero on normalization below for empty input.
            return vec
        for tok in tokens:
            # md5 (not Python's hash()) so the mapping is stable across
            # processes/runs — required for a reproducible benchmark.
            digest = hashlib.md5(tok.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % self._dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class SentenceTransformersEmbeddingProvider:
    """Local, free, real semantic embeddings via ``sentence-transformers``.

    No API key, no network calls after the model is downloaded once, no
    rate limit. Requires ``pip install sentence-transformers`` (pulls in
    torch — a heavy optional dependency, hence not a default requirement of
    this package).

    Args:
        model_name: Any sentence-transformers model id. Defaults to
            ``all-MiniLM-L6-v2`` (384-dim, small, fast on CPU).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "SentenceTransformersEmbeddingProvider requires the "
                "'sentence-transformers' package: pip install sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.dim: int = self._model.get_sentence_embedding_dimension()

    def __call__(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()


class GeminiEmbeddingProvider:
    """Hosted embeddings via Google's free-tier Generative AI API.

    Requires ``pip install google-generativeai`` and a ``GEMINI_API_KEY``
    environment variable (create one for free at aistudio.google.com).
    Subject to free-tier requests-per-day limits — for a large dataset,
    batch runs over time or use :class:`SentenceTransformersEmbeddingProvider`
    instead.

    Args:
        model: Gemini embedding model id. Defaults to ``models/text-embedding-004``.
        api_key: Overrides the ``GEMINI_API_KEY`` env var if supplied.
    """

    def __init__(self, model: str = "models/text-embedding-004", api_key: str | None = None) -> None:
        import os

        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "GeminiEmbeddingProvider requires 'google-generativeai': "
                "pip install google-generativeai"
            ) from exc

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GeminiEmbeddingProvider requires a GEMINI_API_KEY environment "
                "variable (or an explicit api_key argument)."
            )
        genai.configure(api_key=key)
        self._genai = genai
        self._model = model

    def __call__(self, text: str) -> list[float]:
        result = self._genai.embed_content(model=self._model, content=text)
        return list(result["embedding"])


def build_embedding_provider(name: str, dim: int = 1536):
    """Factory used by the CLI entry point to select a provider by name.

    Args:
        name: One of ``"hashing"`` (default), ``"sentence-transformers"``,
            ``"gemini"``.
        dim:  Vector dimension, only used by ``HashingEmbeddingProvider``
            (the other two providers have a fixed model-defined dimension).

    Raises:
        ValueError: for an unrecognized name.
    """
    if name == "hashing":
        return HashingEmbeddingProvider(dim=dim)
    if name == "sentence-transformers":
        return SentenceTransformersEmbeddingProvider()
    if name == "gemini":
        return GeminiEmbeddingProvider()
    raise ValueError(
        f"Unknown embedding provider {name!r}. "
        "Expected one of: hashing, sentence-transformers, gemini."
    )
