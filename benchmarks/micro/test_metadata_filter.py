"""Micro-benchmarks for metadata-filter SQL generation."""

from __future__ import annotations

import pytest

from agent_memory_sdk.repositories.base import _build_metadata_filter


@pytest.mark.benchmark_micro
def test_metadata_filter_exact(benchmark: pytest.BenchmarkFixture) -> None:
    """Benchmark exact-match metadata filter construction."""
    benchmark(_build_metadata_filter, {"source": "support"})


@pytest.mark.benchmark_micro
def test_metadata_filter_not(benchmark: pytest.BenchmarkFixture) -> None:
    """Benchmark `$not` metadata filter construction."""
    benchmark(_build_metadata_filter, {"status": {"$not": "archived"}})


@pytest.mark.benchmark_micro
def test_metadata_filter_array_contains(benchmark: pytest.BenchmarkFixture) -> None:
    """Benchmark `$array_contains` metadata filter construction."""
    benchmark(_build_metadata_filter, {"tags": {"$array_contains": "urgent"}})


@pytest.mark.benchmark_micro
def test_metadata_filter_array_contains_any(benchmark: pytest.BenchmarkFixture) -> None:
    """Benchmark `$array_contains_any` metadata filter construction."""
    benchmark(
        _build_metadata_filter,
        {"tags": {"$array_contains_any": ["urgent", "bug", "high-priority"]}},
    )
