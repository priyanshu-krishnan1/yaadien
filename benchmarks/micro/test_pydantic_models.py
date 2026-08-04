"""Micro-benchmarks for Pydantic model construction and validation."""

from __future__ import annotations

from typing import Any

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)

_MODEL_CASES: list[tuple[str, type[Any], dict[str, Any]]] = [
    (
        "working_memory",
        WorkingMemory,
        {
            "agent_id": "agent-1",
            "user_id": "user-1",
            "thread_id": "thread-1",
            "content": "User asked about benchmark coverage.",
            "metadata": {"role": "user", "source": "chat"},
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.95,
        },
    ),
    (
        "episodic_memory",
        EpisodicMemory,
        {
            "agent_id": "agent-1",
            "user_id": "user-1",
            "content": "Summarized previous support interaction.",
            "metadata": {"source_thread": "thread-1", "summary_model": "test"},
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.9,
        },
    ),
    (
        "semantic_fact",
        SemanticFact,
        {
            "agent_id": "agent-1",
            "user_id": "user-1",
            "content": "User prefers email notifications.",
            "metadata": {"source": "profile"},
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.85,
        },
    ),
    (
        "entity_profile",
        EntityProfile,
        {
            "agent_id": "agent-1",
            "user_id": "user-1",
            "content": "Power user who prefers concise responses.",
            "metadata": {"last_updated_from": "episode-1"},
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.92,
        },
    ),
    (
        "procedural_memory",
        ProceduralMemory,
        {
            "agent_id": "agent-1",
            "content": "Always confirm customer priority before escalation.",
            "metadata": {"skill": "support"},
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.88,
        },
    ),
]


@pytest.mark.benchmark_micro
@pytest.mark.parametrize(("model_name", "model_cls", "payload"), _MODEL_CASES)
def test_pydantic_model_construction(
    benchmark: pytest.BenchmarkFixture,
    model_name: str,
    model_cls: type[Any],
    payload: dict[str, Any],
) -> None:
    """Benchmark direct Pydantic model construction for each memory type."""
    benchmark.extra_info["model"] = model_name
    benchmark(model_cls, **payload)


@pytest.mark.benchmark_micro
@pytest.mark.parametrize(("model_name", "model_cls", "payload"), _MODEL_CASES)
def test_pydantic_model_validate(
    benchmark: pytest.BenchmarkFixture,
    model_name: str,
    model_cls: type[Any],
    payload: dict[str, Any],
) -> None:
    """Benchmark `model_validate()` for each memory type."""
    benchmark.extra_info["model"] = model_name
    benchmark(model_cls.model_validate, payload)
