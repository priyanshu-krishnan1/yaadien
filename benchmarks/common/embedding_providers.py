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
   (a heavy optional dependency — not installed by default). This is an
   alternative local provider for a retrieval-quality number worth comparing.
3. :class:`OllamaEmbeddingProvider` — local, free, real semantic embeddings
   via a locally-running Ollama daemon (``localhost:11434`` by default).
   No API key, no external network, no rate limit — the tradeoff is model
   quality and local compute. Requires ``pip install ollama`` and a running
   daemon with the chosen model pulled. Default model: ``nomic-embed-text``.

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


class OllamaEmbeddingProvider:
    """Local embeddings via a locally-running Ollama daemon.

    No API key, no external network call, no rate limit — the tradeoff vs.
    hosted providers is model quality and local compute. Requires
    ``pip install ollama`` and a running Ollama daemon (``ollama serve`` /
    the desktop app, default host ``http://localhost:11434``) with the
    chosen embedding model already pulled.

    The ``nomic-embed-text`` model (768-dim) is the recommended default: it
    is available from the Ollama registry, covers semantic similarity well,
    and runs fast on CPU/Apple Silicon. Note that Ollama embedding models
    return a fixed dimension determined by the model — the ``dim`` parameter
    below is only used to zero-pad or truncate to match the Db2 VECTOR
    column dimension if they differ (default: 1536).

    Args:
        model: Any Ollama embedding model that has been pulled locally.
            Defaults to ``"nomic-embed-text"``.
        host:  Override the Ollama daemon URL (default
            ``"http://localhost:11434"``).
        dim:   Target vector dimension for padding/truncation to match
            the Db2 VECTOR(1536, FLOAT32) column (default 1536).
    """

    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: str | None = None,
        dim: int = 1536,
    ) -> None:
        try:
            import ollama  # noqa: F401 — import-check only
        except ImportError as exc:
            raise ImportError(
                "OllamaEmbeddingProvider requires the 'ollama' package: "
                "pip install ollama"
            ) from exc
        self._model = model
        self._host = host
        self._dim = dim

    def __call__(self, text: str) -> list[float]:
        import ollama

        kwargs: dict = {"model": self._model, "input": text}
        if self._host:
            client = ollama.Client(host=self._host)
            response = client.embed(**kwargs)
        else:
            response = ollama.embed(**kwargs)

        # ollama.embed returns {"embeddings": [[float, ...]]}
        raw: list[float] = response["embeddings"][0]

        # Pad or truncate to the Db2 VECTOR column dimension.
        if len(raw) < self._dim:
            raw = raw + [0.0] * (self._dim - len(raw))
        elif len(raw) > self._dim:
            raw = raw[: self._dim]

        # Re-normalise after pad/truncate so cosine distance still makes sense.
        norm = math.sqrt(sum(v * v for v in raw))
        if norm > 0:
            raw = [v / norm for v in raw]
        return raw


def build_embedding_provider(
    name: str,
    dim: int = 1536,
) -> HashingEmbeddingProvider | SentenceTransformersEmbeddingProvider | OllamaEmbeddingProvider:
    """Factory used by the CLI entry point to select a provider by name.

    Args:
        name: One of ``"hashing"`` (default), ``"sentence-transformers"``,
            or ``"ollama"`` (uses ``nomic-embed-text`` model).
        dim:  Vector dimension, used by ``HashingEmbeddingProvider`` and
            ``OllamaEmbeddingProvider`` for pad/truncate to the Db2 column
            width (the sentence-transformers provider has a fixed model
            dimension and is not padded).

    Raises:
        ValueError: for an unrecognized name.
    """
    if name == "hashing":
        return HashingEmbeddingProvider(dim=dim)
    if name == "sentence-transformers":
        return SentenceTransformersEmbeddingProvider()
    if name == "ollama":
        return OllamaEmbeddingProvider(dim=dim)
    raise ValueError(
        f"Unknown embedding provider {name!r}. "
        "Expected one of: hashing, sentence-transformers, ollama."
    )
