"""Micro-benchmarks for vector literal serialization."""

from __future__ import annotations

import pytest

from agent_memory_sdk.repositories.base import _vec_to_str


@pytest.mark.benchmark_micro
@pytest.mark.parametrize("dimension", [384, 768, 1536, 3072])
def test_vec_to_str(benchmark: pytest.FixtureRequest, dimension: int) -> None:
    """Benchmark `_vec_to_str` and report statement size in bytes per dimension."""
    embedding = [float(i) / float(dimension) for i in range(dimension)]

    result = benchmark(_vec_to_str, embedding)

    benchmark.extra_info["dimension"] = dimension
    benchmark.extra_info["statement_size_bytes"] = len(result.encode("utf-8"))
