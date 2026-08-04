"""
benchmarks/lifecycle/test_reconcile.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C7 — ``reconcile()`` + soft-supersession benchmark.

Measures how ``MemoryStore.reconcile("facts", scope)`` scales as the number
of live SemanticFact candidates grows.

Only ``memory_type="facts"`` supports supersession columns; the test
seeds SemanticFact records that contain the explicit correction template
recognised by :class:`~benchmarks.retrieval_quality.reconciler.BenchmarkReconciler`
so that the reconciler actually emits SupersedeDecision objects (not just
a no-op pass).

Candidate-set sizes tested
--------------------------
  10 facts   — benchmark_pr (fast)
  50 facts   — benchmark_pr
  200 facts  — benchmark_nightly

Reporting
---------
  - Wall-clock time per reconcile() call
  - Time per candidate (µs/candidate)

Note on the configured reconciler
----------------------------------
The ``memory_store`` fixture from conftest.py parametrizes over 4 wiring
variants.  Only the ``"fully_wired"`` variant has a real
:class:`~benchmarks.retrieval_quality.reconciler.BenchmarkReconciler`
configured.  The other variants use NoOpReconciler and will process
candidates in 0 decisions — this is intentional: we benchmark the full
reconcile() call stack regardless, so the overhead of fetching candidates
and iterating decisions is always measured.

For the supersession-producing variant (``"fully_wired"``), we seed facts
using the correction template so decisions are actually emitted and applied.

Markers
-------
  @pytest.mark.benchmark_pr      — 10 and 50 facts
  @pytest.mark.benchmark_nightly — 200 facts
"""

from __future__ import annotations

import time

import pytest

from agent_memory_sdk.models import SemanticFact
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

from benchmarks.retrieval_quality.reconciler import BenchmarkReconciler


# ---------------------------------------------------------------------------
# Content templates
# ---------------------------------------------------------------------------

# Plain-attribute fact: no correction — a stable "live" fact.
_PLAIN_TEMPLATE = "Alice mentioned that their favourite language is Python."

# Correction fact (winner): triggers BenchmarkReconciler supersession.
# Must match _P_CORRECTION in reconciler.py:
#   "<Name> said: actually ... is now <new>, not <old> anymore"
_CORRECTION_TEMPLATE = (
    "Alice said: actually, I've switched — my favourite language is now Rust, "
    "not Python anymore."
)


def _seed_facts(store: MemoryStore, scope, n: int) -> int:
    """Seed *n* SemanticFact rows designed to exercise the reconciler.

    For reconciliation to produce decisions, we alternate plain-attribute
    facts with one correction fact per pair so the BenchmarkReconciler has
    something to detect.  For n > 2, remaining facts are plain.

    Returns the number of facts inserted.
    """
    seeded = 0
    for i in range(n):
        if i == n - 1 and n > 1:
            # Last fact is a correction turn (the "winner").
            content = _CORRECTION_TEMPLATE
        else:
            content = f"{_PLAIN_TEMPLATE} index={i}"

        store.remember(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=content,
                metadata={"bench": "reconcile", "index": i},
            ),
            scope,
        )
        seeded += 1

    return seeded


# ---------------------------------------------------------------------------
# C7 — reconcile() throughput at small scale (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("n_facts", [10, 50], ids=["10facts", "50facts"])
def test_reconcile_throughput_pr(benchmark, memory_store, benchmark_scope, n_facts):
    """Benchmark reconcile() over small candidate sets (C7, Tier 1).

    Seeds *n_facts* SemanticFact records, then benchmarks a single
    reconcile() call.  Re-seeds in setup() so each round starts fresh.

    Reports:
      - mean wall-clock per reconcile() call
      - µs per candidate
    """
    store = memory_store
    scope = benchmark_scope

    def setup():
        # Erase leftovers from the previous round before re-seeding.
        store.erase_all(scope)
        _seed_facts(store, scope, n_facts)

    def _reconcile():
        return store.reconcile("facts", scope)

    decisions = benchmark.pedantic(_reconcile, setup=setup, rounds=5, warmup_rounds=1)

    mean_s = benchmark.stats["mean"]
    us_per_candidate = (mean_s * 1_000_000) / n_facts if n_facts > 0 else 0.0
    print(
        f"\n[reconcile n={n_facts}] decisions={len(decisions)} "
        f"mean={mean_s * 1000:.2f}ms "
        f"µs/candidate={us_per_candidate:.1f}"
    )

    assert isinstance(decisions, list), "reconcile() must return a list"


# ---------------------------------------------------------------------------
# C7 — reconcile() at 200 facts (benchmark_nightly)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
def test_reconcile_throughput_200(memory_store, benchmark_scope):
    """Benchmark reconcile() over 200 facts (C7, Tier 2 / nightly).

    Uses a fully-wired store with BenchmarkReconciler to ensure real
    supersession decisions are produced and applied, not just the NoOp path.
    """
    store = memory_store
    scope = benchmark_scope
    n_facts = 200

    _seed_facts(store, scope, n_facts)

    start = time.perf_counter()
    decisions = store.reconcile("facts", scope, limit=n_facts)
    elapsed = time.perf_counter() - start

    us_per_candidate = (elapsed * 1_000_000) / n_facts if n_facts > 0 else 0.0
    print(
        f"\n[reconcile 200] decisions={len(decisions)} "
        f"elapsed={elapsed * 1000:.2f}ms "
        f"µs/candidate={us_per_candidate:.1f}"
    )

    assert isinstance(decisions, list), "reconcile() must return a list"
    assert elapsed > 0
