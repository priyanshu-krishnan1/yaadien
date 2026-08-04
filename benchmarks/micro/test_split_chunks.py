"""Micro-benchmarks for ORC-2 content chunk splitting."""

from __future__ import annotations

import pytest

from agent_memory_sdk.repositories.base import _split_chunks


@pytest.mark.benchmark_micro
@pytest.mark.parametrize("content_size", [200, 1000, 5000, 20000, 60000])
def test_split_chunks(benchmark: pytest.BenchmarkFixture, content_size: int) -> None:
    """Benchmark `_split_chunks` across representative content sizes."""
    content = "x" * content_size

    benchmark(_split_chunks, content)
