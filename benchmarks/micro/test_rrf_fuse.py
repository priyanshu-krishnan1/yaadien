"""Micro-benchmarks for Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest

from agent_memory_sdk.repositories.base import _rrf_fuse


@pytest.mark.benchmark_micro
@pytest.mark.parametrize("list_size", [10, 100, 1000])
def test_rrf_fuse(benchmark: pytest.BenchmarkFixture, list_size: int) -> None:
    """Benchmark RRF fusion with overlapping ranked ID lists."""
    vector_order = [f"doc-{idx}" for idx in range(list_size)]
    keyword_order = [f"doc-{idx}" for idx in range(list_size // 2, list_size + (list_size // 2))]

    benchmark(_rrf_fuse, vector_order, keyword_order)
