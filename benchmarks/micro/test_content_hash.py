"""Micro-benchmarks for normalized content hashing."""

from __future__ import annotations

import pytest

from agent_memory_sdk.repositories.base import _content_hash


@pytest.mark.benchmark_micro
@pytest.mark.parametrize(
    ("size_name", "content"),
    [
        ("short", "a" * 50),
        ("medium", "a" * 500),
        ("long", "a" * 5000),
    ],
)
def test_content_hash(
    benchmark: pytest.BenchmarkFixture,
    size_name: str,
    content: str,
) -> None:
    """Benchmark `_content_hash` across short, medium, and long content."""
    benchmark.extra_info["content_size"] = size_name
    benchmark.extra_info["content_length"] = len(content)
    benchmark(_content_hash, content)
