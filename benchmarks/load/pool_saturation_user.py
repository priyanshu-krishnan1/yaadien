"""
benchmarks/load/pool_saturation_user.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Locust harness for connection-pool saturation characterization (EPIC-15, BM-15).

Sweep DB2_POOL_SIZE in {1, 5, 10, 20} at fixed offered load. Run each pool
size for 2 min with 50 concurrent users:

    DB2_POOL_SIZE=1  locust -f benchmarks/load/pool_saturation_user.py \\
        --headless -u 50 -r 5 -t 2m --csv results/pool1
    DB2_POOL_SIZE=5  locust -f benchmarks/load/pool_saturation_user.py \\
        --headless -u 50 -r 5 -t 2m --csv results/pool5
    DB2_POOL_SIZE=10 locust -f benchmarks/load/pool_saturation_user.py \\
        --headless -u 50 -r 5 -t 2m --csv results/pool10
    DB2_POOL_SIZE=20 locust -f benchmarks/load/pool_saturation_user.py \\
        --headless -u 50 -r 5 -t 2m --csv results/pool20

Metrics collected per pool size:
  - Throughput (RPS from Locust CSV)
  - P50 / P95 / P99 latency (from Locust CSV)
  - ConnectionPoolExhausted rate (exhausted / total requests)
  - Queue wait time (approximated as P95 latency when pool is saturated)

Acceptance: the run confirms exhaustion is GRACEFUL — ConnectionPoolExhausted
is raised (not a hang), state is never corrupted. A single oversaturation
scenario is exercised by running with more VUs than pool size.
"""

from __future__ import annotations

import os
import random
import sys
import time

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv()
except ImportError:
    pass

from locust import between, constant, task  # type: ignore[import-untyped]  # noqa: E402

from agent_memory_sdk.db.connection import ConnectionPoolExhausted  # noqa: E402
from agent_memory_sdk.models import WorkingMemory  # noqa: E402

# Re-use the shared pool/store/embed globals and the base VU class from the
# primary locustfile.  Importing locustfile also registers its @events.init /
# @events.quitting hooks, so pool setup/teardown is handled exactly once.
from benchmarks.load.locustfile import (  # noqa: E402
    _EMBED,
    _RUN_TAG,
    _STORE,
    MemoryStoreUser,
)

# ---------------------------------------------------------------------------
# PoolSaturationUser — aggressive pacing to saturate the pool
# ---------------------------------------------------------------------------


class PoolSaturationUser(MemoryStoreUser):
    """Mixed search/remember workload with aggressive inter-task pacing.

    ``wait_time = between(0.0, 0.05)`` is intentionally close to zero so that
    50 concurrent VUs produce a sustained offered load that can saturate pool
    sizes of 1 and 5.

    ``ConnectionPoolExhausted`` is caught, re-fired as a labelled Locust
    failure event (so the exhaustion rate appears as its own category in the
    Locust CSV), and then re-raised so Locust counts it in the global failure
    counter.

    Per-VU ``_exhaustion_count`` / ``_total_count`` are tracked and emitted as
    a custom ``pool.exhaustion_rate`` metric on ``on_stop()``.
    """

    wait_time = between(0.0, 0.05)

    def on_start(self) -> None:  # type: ignore[override]
        super().on_start()
        self._exhaustion_count: int = 0
        self._total_count: int = 0

    def on_stop(self) -> None:  # type: ignore[override]
        super().on_stop()
        # Emit the per-VU exhaustion rate as a synthetic Locust metric so it
        # appears in the CSV alongside normal request stats.
        rate_pct = (
            self._exhaustion_count / self._total_count * 100
            if self._total_count > 0
            else 0.0
        )
        self.environment.events.request.fire(
            request_type="pool",
            name="pool.exhaustion_rate",
            response_time=rate_pct,  # % exhausted — abusing response_time as a gauge
            response_length=self._exhaustion_count,
        )

    # ── tasks ─────────────────────────────────────────────────────────────────

    @task(3)
    def task_search(self) -> None:
        """Vector search — weight 3 (same as SDK5User baseline)."""
        assert _STORE is not None and _EMBED is not None
        query_vec = _EMBED(self._own_marker)
        t0 = time.perf_counter()
        self._total_count += 1
        try:
            results = self._run(
                _STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="working.search",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except ConnectionPoolExhausted as exc:
            self._exhaustion_count += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="working.search [pool_exhausted]",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="working.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(3)
    def task_remember(self) -> None:
        """Write a working-memory record — weight 3 (same as SDK5User baseline)."""
        assert _STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        self._total_count += 1
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                thread_id=self.scope.thread_id,
                content=(
                    f"{self._own_marker} pool-sat turn={turn} tag={_RUN_TAG}"
                ),
            )
            self._run(_STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except ConnectionPoolExhausted as exc:
            self._exhaustion_count += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember [pool_exhausted]",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


# ---------------------------------------------------------------------------
# OverSaturationUser — zero wait, proves exhaustion is graceful (not a hang)
# ---------------------------------------------------------------------------


class OverSaturationUser(PoolSaturationUser):
    """Same workload as :class:`PoolSaturationUser` but with ``constant(0)``
    inter-task delay.

    Use this class when you want to *guarantee* pool exhaustion rather than
    just approach saturation — run more VUs than pool size, e.g.:

        DB2_POOL_SIZE=1 locust -f benchmarks/load/pool_saturation_user.py \\
            --headless -u 10 -r 10 -t 1m --csv results/oversaturate \\
            -T OverSaturationUser

    Acceptance: ``ConnectionPoolExhausted`` must appear in the Locust failure
    breakdown for every pool size < number of VUs — confirms the pool raises
    rather than hangs under over-saturation.
    """

    wait_time = constant(0)
