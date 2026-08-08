"""
benchmarks/load/scalability_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Locust User classes for the full scalability matrix — EPIC-15, BM-14.

Phase 5.3 of BENCHMARK_STRATEGY.md defines six sweeps:

  1. User ramp           1→10→50→200 concurrent VUs
  2. Agent-count sweep   10→100→1 000 distinct agent_id slots
  3. Mixed read/write    70/30, 50/50, 30/70 — with writer-starvation detection
  4. Sustained write     100 % write saturation (WriteOnlyUser in locustfile.py)
  5. 60-minute soak      RSS growth + error-rate drift detection
  6. Long-session sweep  10→10 000 turns, get_context_card / get_summary P95

Each class is driven as a standalone locustfile via ``-f scalability_tasks.py``
**or** alongside the primary locustfile when Locust's ``--user-classes`` flag
selects specific classes.

Usage examples
--------------
User ramp (1 → 200 VUs, ramp 2/s, run 10 min)::

    locust -f benchmarks/load/scalability_tasks.py \\
           --user-classes UserRampUser \\
           --headless -u 200 -r 2 -t 10m \\
           --csv results/user_ramp

Agent-count sweep (1 000 agent slots, 50 VUs)::

    BENCH_N_AGENTS=1000 locust -f benchmarks/load/scalability_tasks.py \\
           --user-classes AgentSweepUser \\
           --headless -u 50 -r 5 -t 5m \\
           --csv results/agent_sweep_1000

Mixed read/write — 30 % write, 70 % read (read-heavy, starvation check active)::

    BENCH_RW_RATIO=70/30 locust -f benchmarks/load/scalability_tasks.py \\
           --user-classes MixedReadWriteUser \\
           --headless -u 50 -r 5 -t 5m \\
           --csv results/mix_70_30

Long-session sweep (1 000-turn sessions)::

    BENCH_SESSION_LENGTH=1000 locust -f benchmarks/load/scalability_tasks.py \\
           --user-classes LongSessionUser \\
           --headless -u 20 -r 2 -t 5m \\
           --csv results/long_session_1000

60-minute soak::

    BENCH_EMIT_RSS=1 locust -f benchmarks/load/scalability_tasks.py \\
           --user-classes SoakUser \\
           --headless -u 50 -r 2 -t 60m \\
           --csv results/soak_60m

Environment variables
----------------------
BENCH_N_AGENTS      Number of distinct agent slots for AgentSweepUser (default 1000).
BENCH_RW_RATIO      Read/write ratio for MixedReadWriteUser, e.g. "70/30" (default).
                    Parsed as read_pct/write_pct; values are used as Locust task weights.
BENCH_SESSION_LENGTH  Number of pre-seeded conversation turns for LongSessionUser
                    (default 1000).
BENCH_EMIT_RSS      Set to "1" to emit peak-RSS metrics via Locust request events
                    (handled in MemoryStoreUser.on_stop() for basic RSS; SoakUser
                    additionally fires an explicit RSS-growth delta).
"""

from __future__ import annotations

import logging
import os
import random
import time

from locust import between, task  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Re-use the shared infrastructure from the primary locustfile.
# Import the MODULE (not individual names) so that every access at task
# runtime gets the current value that _on_locust_init() sets via
#   global _POOL, _STORE, _EMBED
# A bare "from locustfile import _STORE" would create a local None binding
# that is never updated when the @events.init hook later reassigns the
# module-level global — causing AssertionError in every task.
# ---------------------------------------------------------------------------
import benchmarks.load.locustfile as _lf  # noqa: E402
from agent_memory_sdk.models import WorkingMemory
from benchmarks.common.resource_sampler import ResourceSampler
from benchmarks.common.scope_gen import make_scope, marker_for
from benchmarks.load.locustfile import MemoryStoreUser  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — read once from the environment at module import time.
# Individual on_start() methods may re-read them for per-VU configuration.
# ---------------------------------------------------------------------------

N_AGENTS: int = int(os.environ.get("BENCH_N_AGENTS", "1000"))
_SESSION_LENGTH: int = int(os.environ.get("BENCH_SESSION_LENGTH", "1000"))
_RW_RATIO_RAW: str = os.environ.get("BENCH_RW_RATIO", "70/30")


def _parse_rw_ratio(raw: str) -> tuple[int, int]:
    """Parse a ``"READ/WRITE"`` ratio string into ``(read_weight, write_weight)``.

    Accepts values like ``"70/30"``, ``"50/50"``, ``"30/70"``.  The two
    components must be positive integers; they do not need to sum to 100 — they
    are used directly as Locust task-weight integers.

    Returns ``(70, 30)`` if the string cannot be parsed.
    """
    try:
        parts = raw.split("/")
        if len(parts) != 2:
            raise ValueError("expected exactly one '/'")
        read_w = int(parts[0].strip())
        write_w = int(parts[1].strip())
        if read_w <= 0 or write_w <= 0:
            raise ValueError("both weights must be positive")
        return read_w, write_w
    except Exception:  # noqa: BLE001
        logger.warning(
            "BENCH_RW_RATIO=%r could not be parsed; falling back to 70/30.",
            raw,
        )
        return 70, 30


# ---------------------------------------------------------------------------
# WriterStarvationWarning — used as the exception= payload in the Locust
# request event so it appears as a named failure in Locust's stats table.
# ---------------------------------------------------------------------------


class WriterStarvationWarning(Exception):
    """Fired via Locust's request event when no write task has completed in
    the last STARVATION_CHECK_INTERVAL read completions.

    This is not a hard error — the VU continues running — but it is recorded
    as a failed request so the HTML/CSV report makes starvation visible.
    """


# ---------------------------------------------------------------------------
# 1. UserRampUser — 1→10→50→200 concurrent VU ramp test
# ---------------------------------------------------------------------------


class UserRampUser(MemoryStoreUser):
    """Standard mixed workload for the user-ramp dimension of BM-14.

    Run with ``-u 200 -r 2`` to exercise the 1→10→50→200 step-ramp.
    P95 inflection is identified by comparing Locust's per-step P95 values.
    """

    wait_time = between(0.05, 0.3)

    @task(3)
    def task_search(self) -> None:
        """Vector search within this VU's scope."""
        assert _lf._STORE is not None and _lf._EMBED is not None
        query_vec = _lf._EMBED(self._own_marker)
        t0 = time.perf_counter()
        try:
            results = self._run(
                _lf._STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="user_ramp.search",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="user_ramp.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(3)
    def task_remember(self) -> None:
        """Write a working-memory record tagged with this VU's scope marker."""
        assert _lf._STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                thread_id=self.scope.thread_id,
                content=(
                    f"{self._own_marker} user-ramp turn={turn} tag={_lf._RUN_TAG}"
                ),
            )
            self._run(_lf._STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="user_ramp.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="user_ramp.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


# ---------------------------------------------------------------------------
# 2. AgentSweepUser — 10→100→1 000 distinct agent_id slots
# ---------------------------------------------------------------------------


class AgentSweepUser(MemoryStoreUser):
    """Exercises many distinct ``agent_id`` values to cover the agent-count sweep.

    Each VU is assigned to one of ``N_AGENTS`` (= ``BENCH_N_AGENTS`` env var,
    default 1 000) agent slots using a modulo distribution over the VU's
    internal index.  All reads and writes are strictly scoped to that slot,
    which makes cross-agent leakage detectable by substring-searching results
    for another VU's marker (same approach as the isolation_load suite).

    Run three separate locust invocations with ``BENCH_N_AGENTS=10``,
    ``100``, and ``1000`` to produce the agent-count sweep data points.
    """

    wait_time = between(0.05, 0.3)

    def on_start(self) -> None:
        """Assign this VU to a deterministic agent slot within [0, N_AGENTS)."""
        super().on_start()

        # Distribute VUs across agent slots.
        vu_id: int = getattr(self, "_user_id", id(self)) % 100_000
        agent_index = vu_id % N_AGENTS

        # Override the scope set by the parent to use the sweep-specific slot.
        self.scope = make_scope(
            run_id=_lf._RUN_TAG,
            tenant_index=0,          # single tenant for agent sweep
            agent_index=agent_index,
            user_index=vu_id,
        )
        self._own_marker = marker_for(self.scope)

    @task(3)
    def task_search(self) -> None:
        assert _lf._STORE is not None and _lf._EMBED is not None
        query_vec = _lf._EMBED(self._own_marker)
        t0 = time.perf_counter()
        try:
            results = self._run(
                _lf._STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="agent_sweep.search",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="agent_sweep.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(3)
    def task_remember(self) -> None:
        assert _lf._STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                content=f"{self._own_marker} agent-sweep turn={turn}",
            )
            self._run(_lf._STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="agent_sweep.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="agent_sweep.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


# ---------------------------------------------------------------------------
# 3. MixedReadWriteUser — configurable read/write ratio + starvation detection
# ---------------------------------------------------------------------------

#: After this many consecutive reads with zero writes, fire a starvation warning.
_STARVATION_CHECK_INTERVAL: int = 100

#: Reset counters after this many total operations to keep memory bounded.
_COUNTER_RESET_INTERVAL: int = 1000


class MixedReadWriteUser(MemoryStoreUser):
    """Mixed read/write workload with ratio controlled by ``BENCH_RW_RATIO``.

    The read:write task weights are set dynamically in ``on_start()`` by
    parsing ``BENCH_RW_RATIO`` (e.g. ``"70/30"``).  Because Locust picks task
    weights at class definition time, this class provides two concrete task
    methods and adjusts their effective frequency by skipping the minor
    operation probabilistically inside the task body — matching the requested
    ratio while staying within Locust's task-dispatch model.

    Writer-starvation detection: after every ``_STARVATION_CHECK_INTERVAL``
    consecutive reads, if no write has completed in that window, a
    ``WriterStarvationWarning`` is fired via the Locust request event.
    Counters reset every ``_COUNTER_RESET_INTERVAL`` total ops.
    """

    wait_time = between(0.05, 0.3)

    # Per-VU counters — set in on_start().
    _read_count: int
    _write_count: int
    _read_weight: int
    _write_weight: int

    def on_start(self) -> None:
        super().on_start()
        self._read_weight, self._write_weight = _parse_rw_ratio(_RW_RATIO_RAW)
        self._read_count = 0
        self._write_count = 0

    @task(1)
    def task_mixed(self) -> None:
        """Single dispatched task; internally routes to read or write based on
        the configured ratio using weighted random selection.
        """
        total = self._read_weight + self._write_weight
        if random.randint(1, total) <= self._read_weight:
            self._do_read()
        else:
            self._do_write()

    # ── internal helpers ────────────────────────────────────────────────────

    def _do_read(self) -> None:
        assert _lf._STORE is not None and _lf._EMBED is not None
        query_vec = _lf._EMBED(self._own_marker)
        t0 = time.perf_counter()
        try:
            results = self._run(
                _lf._STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="mixed_rw.search",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="mixed_rw.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
            return  # don't count failed reads toward starvation check

        self._read_count += 1
        self._check_writer_starvation()
        self._maybe_reset_counters()

    def _do_write(self) -> None:
        assert _lf._STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                content=f"{self._own_marker} mixed-rw turn={turn}",
            )
            self._run(_lf._STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="mixed_rw.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="mixed_rw.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
            return  # don't count failed writes toward starvation resolution

        self._write_count += 1
        self._maybe_reset_counters()

    def _check_writer_starvation(self) -> None:
        """After every _STARVATION_CHECK_INTERVAL reads, check for write starvation."""
        if self._read_count % _STARVATION_CHECK_INTERVAL != 0:
            return
        if self._write_count == 0:
            warning = WriterStarvationWarning(
                f"Writer starvation detected: {self._read_count} reads completed "
                f"with 0 writes in the current window (ratio={_RW_RATIO_RAW}). "
                f"VU scope: {self.scope.agent_id}"
            )
            logger.warning(str(warning))
            self.environment.events.request.fire(
                request_type="starvation",
                name="mixed_rw.writer_starvation",
                response_time=0,
                response_length=0,
                exception=warning,
            )

    def _maybe_reset_counters(self) -> None:
        """Reset counters every _COUNTER_RESET_INTERVAL ops to keep them bounded."""
        if (self._read_count + self._write_count) >= _COUNTER_RESET_INTERVAL:
            self._read_count = 0
            self._write_count = 0


# ---------------------------------------------------------------------------
# 4. LongSessionUser — long-conversation session sweep (10→10 000 turns)
# ---------------------------------------------------------------------------


class LongSessionUser(MemoryStoreUser):
    """Simulates long conversation sessions for the session-length sweep.

    ``on_start()`` seeds the scope with ``BENCH_SESSION_LENGTH`` working-memory
    messages.  The main task calls ``get_context_card()`` and ``get_summary()``
    and records their latency so Locust tracks P95 growth as the session length
    increases.

    Run with ``BENCH_SESSION_LENGTH=10``, ``100``, ``1000``, and ``10000`` to
    produce the four data points for the long-session sweep.

    Note: The seed phase blocks the VU's ``on_start()`` for the duration of all
    ``BENCH_SESSION_LENGTH`` writes — this is intentional: each VU must have its
    full session history in place before any read tasks fire.  Use a small
    ``-u`` value (e.g. ``-u 20``) to keep the seed phase reasonable.
    """

    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        super().on_start()
        assert _lf._STORE is not None, "_STORE must be initialised by on_locust_init"
        session_length = int(os.environ.get("BENCH_SESSION_LENGTH", str(_SESSION_LENGTH)))
        # Seed the session with synthetic conversation turns.
        messages = [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": (
                    f"{self._own_marker} session-turn={i} tag={_lf._RUN_TAG}"
                ),
            }
            for i in range(session_length)
        ]
        # Write in batches of 50 to avoid overwhelming a single call.
        batch_size = 50
        for start in range(0, len(messages), batch_size):
            batch = messages[start : start + batch_size]
            self._run(_lf._STORE.add_messages, batch, self.scope)

    @task
    def task_context_card(self) -> None:
        """Call get_context_card() and record latency — the primary P95 metric."""
        assert _lf._STORE is not None
        t0 = time.perf_counter()
        try:
            card = self._run(_lf._STORE.get_context_card, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="long_session.get_context_card",
                response_time=elapsed_ms,
                response_length=0 if card is None else 1,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="long_session.get_context_card",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task
    def task_get_summary(self) -> None:
        """Call get_summary() and record latency."""
        assert _lf._STORE is not None
        t0 = time.perf_counter()
        try:
            summary = self._run(_lf._STORE.get_summary, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="long_session.get_summary",
                response_time=elapsed_ms,
                response_length=getattr(summary, "message_count", 0),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="long_session.get_summary",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


# ---------------------------------------------------------------------------
# 5. SoakUser — 60-minute soak variant with explicit RSS-growth reporting
# ---------------------------------------------------------------------------


class SoakUser(MemoryStoreUser):
    """60-minute soak variant.  Realistic pacing (0.2–1.0 s think-time).

    Uses ``ResourceSampler`` to track RSS growth across the VU's lifetime.
    On ``on_stop()``, fires an explicit ``"rss_growth_mb"`` request event
    containing the delta between start-RSS and end-RSS so the soak report
    can confirm no per-VU memory growth trend.

    Standard mixed write/search tasks — identical to UserRampUser but with
    slower pacing appropriate for a long-running soak.

    Acceptance criteria (BM-14 AC3):
      * No RSS growth trend across the 60-minute window.
      * No error-rate drift (Locust's built-in failure ratio tracks this).
    """

    wait_time = between(0.2, 1.0)

    # Start-of-VU RSS snapshot (bytes).
    _soak_start_rss: int

    def on_start(self) -> None:
        super().on_start()
        # Capture the RSS at VU-start so we can compute the delta on stop.
        # ResourceSampler is started by the parent when BENCH_EMIT_RSS=1;
        # we additionally record the baseline RSS independently.
        self._soak_sampler = ResourceSampler(interval_s=0.5).__enter__()
        snap = self._soak_sampler.snapshot()
        self._soak_start_rss = snap.peak_rss_bytes

    def on_stop(self) -> None:
        """Fire a ``rss_growth_mb`` event encoding the start-to-end RSS delta."""
        super().on_stop()
        if hasattr(self, "_soak_sampler"):
            end_snap = self._soak_sampler.snapshot()
            self._soak_sampler.__exit__(None, None, None)
            start_rss = self._soak_start_rss
            end_rss = end_snap.peak_rss_bytes
            growth_mb = (end_rss - start_rss) / (1024 * 1024)
            self.environment.events.request.fire(
                request_type="rss",
                name="soak.rss_growth_mb",
                response_time=growth_mb,
                response_length=0,
            )

    @task(3)
    def task_search(self) -> None:
        assert _lf._STORE is not None and _lf._EMBED is not None
        query_vec = _lf._EMBED(self._own_marker)
        t0 = time.perf_counter()
        try:
            results = self._run(
                _lf._STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="soak.search",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="soak.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(3)
    def task_remember(self) -> None:
        assert _lf._STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                content=f"{self._own_marker} soak turn={turn} tag={_lf._RUN_TAG}",
            )
            self._run(_lf._STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="soak.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="soak.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
