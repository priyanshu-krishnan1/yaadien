"""
types.py
~~~~~~~~
Shared protocol and enum types used across the SDK.

These definitions are dependency-light (stdlib + enum only) and are
imported by both models.py and the repository layer, so they live in
their own module to avoid circular imports.
"""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# EmbeddingProvider
# ---------------------------------------------------------------------------

@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for anything that turns text into a vector.

    The SDK never imports a specific embedding model. Callers inject their
    own provider — an OpenAI client wrapper, a sentence-transformers model,
    a stub for tests, etc.

    The returned list must have exactly the dimension that matches the
    VECTOR column in the target table (default: 1536).  The SDK does NOT
    validate the length; it passes the vector straight to Db2.

    Example::

        class OpenAIEmbedder:
            def __call__(self, text: str) -> list[float]:
                response = openai.embeddings.create(
                    model="text-embedding-3-small", input=text
                )
                return response.data[0].embedding

        store = MemoryStore(pool, embedding_provider=OpenAIEmbedder())
    """

    def __call__(self, text: str) -> list[float]:
        """Embed *text* and return a list of floats.

        Args:
            text: The plain-text string to embed.

        Returns:
            A list of float coordinates (the embedding vector).
        """
        ...


# ---------------------------------------------------------------------------
# DistanceMetric
# ---------------------------------------------------------------------------

class DistanceMetric(str, enum.Enum):
    """Vector distance metrics supported by Db2 ``VECTOR_DISTANCE``.

    The value of each member is the string that Db2 accepts as the
    third argument to ``VECTOR_DISTANCE(col, ?, '<metric>')``.

    Note: the distance metric used in a search query MUST match the
    ``WITH DISTANCE <metric>`` clause of the table's VECTOR INDEX.
    All five memory tables are indexed WITH DISTANCE COSINE (see
    0002_memory_tables.sql).  Passing a non-COSINE metric at query time
    will still return results, but the ANN index will NOT be used — Db2
    will fall back to a full scan.
    """

    COSINE = "COSINE"
    EUCLIDEAN = "EUCLIDEAN"
    DOT = "DOT"
    MANHATTAN = "MANHATTAN"


# ---------------------------------------------------------------------------
# SearchMode
# ---------------------------------------------------------------------------

class SearchMode(str, enum.Enum):
    """Controls whether Db2 uses the ANN vector index or an exact scan.

    APPROX  → ``FETCH FIRST n ROWS ONLY APPROX``
        Uses the DiskANN vector index.  Fast, sub-linear, approximate.
        Requires RUNSTATS to have been run on the table; requires the
        query metric to match the index's ``WITH DISTANCE`` clause.

    EXACT   → ``FETCH FIRST n ROWS ONLY``
        Full sequential scan; always returns the true top-k.  Slower on
        large tables but no RUNSTATS dependency and metric-agnostic.

    DEFAULT → standard ``FETCH FIRST n ROWS ONLY``
        Alias for EXACT (the optimizer chooses whether to use the index).
    """

    APPROX = "APPROX"
    EXACT = "EXACT"
    DEFAULT = "DEFAULT"
