"""
benchmarks/common/report.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Result dataclasses for the retrieval-quality benchmark suite.

Restored from git history (BM-2 retired the bespoke harness; the dataclasses
are still required by benchmarks/retrieval_quality/run.py which was kept).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunMetadata:
    run_id: str
    embedding_provider: str
    embedding_dim: int
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class CategoryScore:
    category: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0


@dataclass
class RetrievalQualityResult:
    judge_name: str
    embedding_provider: str
    top_k: int
    category_scores: list[CategoryScore]
    is_longmemeval_comparable: bool
    deviation_notes: list[str]

    @property
    def overall_correct(self) -> int:
        return sum(c.correct for c in self.category_scores)

    @property
    def overall_total(self) -> int:
        return sum(c.total for c in self.category_scores)

    @property
    def overall_accuracy(self) -> float:
        return (self.overall_correct / self.overall_total) if self.overall_total else 0.0


@dataclass
class BaselineResult:
    """Flat-context (no SDK) baseline result from ``run_baseline()``."""

    judge_name: str
    category_scores: list[CategoryScore]

    @property
    def overall_correct(self) -> int:
        return sum(c.correct for c in self.category_scores)

    @property
    def overall_total(self) -> int:
        return sum(c.total for c in self.category_scores)

    @property
    def overall_accuracy(self) -> float:
        return (self.overall_correct / self.overall_total) if self.overall_total else 0.0


@dataclass
class LatencyCostResult:
    remember_summary: dict[str, Any]
    search_summary: dict[str, Any]
    hook_summaries: dict[str, dict[str, Any]]
    hook_configured: bool


@dataclass
class IsolationLoadResult:
    tenants: int
    agents_per_tenant: int
    concurrent_workers: int
    ops_per_worker: int
    total_write_ops: int
    total_read_assertions: int
    leakage_incidents: int
    elapsed_s: float

    @property
    def passed(self) -> bool:
        return self.leakage_incidents == 0
