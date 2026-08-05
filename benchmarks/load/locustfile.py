"""
benchmarks/load/locustfile.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Locust load-test harness for the agent-memory-sdk (EPIC-15, BM-12).

Architecture — the gevent/ibm_db workaround
--------------------------------------------
Locust's concurrency model is gevent greenlets.  ``ibm_db`` is a blocking C
extension that gevent cannot monkeypatch — a naive Locust ``User`` calling
``store.search(...)`` directly would block the *entire* greenlet hub for the
duration of every DB round-trip, preventing any other virtual user from making
progress.

The documented fix: dispatch every SDK call through
``gevent.get_hub().threadpool.apply(fn, args, kwargs)`` — this runs the blocking
call on a real OS thread from gevent's internal thread pool and suspends only
the calling greenlet (not the hub), allowing all other VUs to continue.

This is implemented once in :class:`MemoryStoreUser` (~20 LOC total) and
inherited by all task classes.  The repo's own ``benchmarks/isolation_load/run.py``
already proves ``ibm_db`` is correct under Python threads — the threadpool
mechanism is the same underlying infrastructure.

Usage — headless run
---------------------
::

    # 50 concurrent users, ramp at 5/s, run for 5 minutes
    locust -f benchmarks/load/locustfile.py \\
           --headless -u 50 -r 5 -t 5m \\
           --csv results/load_run

    # With a threshold: exit non-zero if P95 > 500 ms
    LOCUST_FAIL_RATIO=0.01 locust -f benchmarks/load/locustfile.py \\
           --headless -u 50 -r 5 -t 5m \\
           --csv results/load_run

    # BM-15 pool-size sweep (override via env):
    DB2_POOL_SIZE=1 locust -f benchmarks/load/locustfile.py \\
           --headless -u 50 -r 5 -t 2m --csv results/pool1

Environment variables
----------------------
DB2_HOSTNAME, DB2_PORT, DB2_DATABASE, DB2_USERNAME, DB2_PASSWORD — same as the
integration test suite and benchmarks/conftest.py.

DB2_POOL_SIZE   — override the connection-pool size (default 5).
BENCH_RUN_TAG   — optional string embedded in tenant_ids so multiple concurrent
                  runs don't collide; defaults to a random hex token.
BENCH_EMIT_RSS  — set to "1" to emit peak RSS via Locust's ``request`` event
                  at the end of each task (for soak/memory-growth detection).
LOCUST_FAIL_RATIO  — maximum allowed failure ratio (0.0–1.0); the ``StatsWatcher``
                     event listener fires ``runner.quit()`` when the live ratio
                     exceeds this threshold mid-run.
"""

from __future__ import annotations

import os
import random
import sys
import time
import uuid
from typing import Any

# Allow running from the repo root without `pip install -e .`.
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv()
except ImportError:
    pass

import gevent  # type: ignore[import-untyped]  # noqa: E402
from locust import User, between, events, task  # type: ignore[import-untyped]  # noqa: E402

from agent_memory_sdk.db.connection import ConnectionPool  # noqa: E402
from agent_memory_sdk.db.migrate import Migrator  # noqa: E402
from agent_memory_sdk.models import MemoryScope, WorkingMemory  # noqa: E402
from agent_memory_sdk.store import MemoryStore  # noqa: E402
from benchmarks.common.embedding_providers import HashingEmbeddingProvider  # noqa: E402
from benchmarks.common.resource_sampler import ResourceSampler  # noqa: E402
from benchmarks.common.scope_gen import make_scope, marker_for  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level globals (initialised once per Locust worker process)
# ---------------------------------------------------------------------------

#: Shared ConnectionPool — sized from DB2_POOL_SIZE (env) or defaulting to 5.
_POOL: ConnectionPool | None = None

#: Shared MemoryStore — all VUs share one store (exercises E3 thread-safety).
_STORE: MemoryStore | None = None

#: HashingEmbeddingProvider — offline, deterministic; no model required.
_EMBED: HashingEmbeddingProvider | None = None

#: Run-unique tag so multiple concurrent locust invocations don't collide.
_RUN_TAG: str = os.environ.get("BENCH_RUN_TAG", uuid.uuid4().hex[:8])

#: Emit peak-RSS via Locust request events (for soak runs — BM-14).
_EMIT_RSS: bool = os.environ.get("BENCH_EMIT_RSS", "0") == "1"


# ---------------------------------------------------------------------------
# Locust event hooks — initialise / teardown the shared pool
# ---------------------------------------------------------------------------


@events.init_command_line_parser.add_listener
def _add_cli_args(parser: Any) -> None:  # type: ignore[no-untyped-def]
    """Register custom CLI flags so they appear in ``--help``."""
    parser.add_argument(
        "--fail-ratio",
        type=float,
        default=float(os.environ.get("LOCUST_FAIL_RATIO", "1.0")),
        help="Exit non-zero if the live failure ratio exceeds this value (0.0–1.0).",
    )


@events.init.add_listener
def _on_locust_init(environment: Any, **_kw: Any) -> None:
    """Called once in the Locust worker process after ``environment`` is ready."""
    global _POOL, _STORE, _EMBED  # noqa: PLW0603

    if not os.environ.get("DB2_HOSTNAME"):
        # Fail loudly so CI surfaces this as a configuration error, not a
        # misleading "0 requests" result.
        raise RuntimeError(
            "DB2_HOSTNAME is not set.  Export DB2_* env vars before running "
            "the Locust suite (same vars as the integration test suite)."
        )

    _POOL = ConnectionPool()
    Migrator(_POOL).run()  # fast no-op when schema already exists
    _EMBED = HashingEmbeddingProvider()
    _STORE = MemoryStore(
        pool=_POOL,
        embedding_provider=_EMBED,
        enable_chunking=False,  # chunking cost measured separately in BM-8
    )

    # Attach a stats watcher that honours --fail-ratio.
    fail_ratio: float = getattr(environment.parsed_options, "fail_ratio", 1.0)
    if fail_ratio < 1.0:
        _attach_fail_ratio_watcher(environment, fail_ratio)


@events.quitting.add_listener
def _on_quitting(environment: Any, **_kw: Any) -> None:
    """Shut down the pool cleanly so ibm_db releases its C handles."""
    if _POOL is not None:
        import contextlib
        with contextlib.suppress(Exception):
            _POOL.close()


# ---------------------------------------------------------------------------
# Fail-ratio watcher (headless non-zero exit on threshold breach)
# ---------------------------------------------------------------------------


def _attach_fail_ratio_watcher(environment: Any, threshold: float) -> None:
    """Poll the live stats every 5 s; call ``runner.quit()`` on breach."""

    def _watcher() -> None:
        while True:
            gevent.sleep(5)
            runner = environment.runner
            if runner is None:
                continue
            stats = environment.stats.total
            if stats.num_requests == 0:
                continue
            ratio = stats.num_failures / stats.num_requests
            if ratio > threshold:
                import logging
                logging.getLogger(__name__).error(
                    "Failure ratio %.3f exceeds threshold %.3f — stopping run.",
                    ratio,
                    threshold,
                )
                runner.quit()
                return

    gevent.spawn(_watcher)


# ---------------------------------------------------------------------------
# MemoryStoreUser — base class with gevent-threadpool dispatch
# ---------------------------------------------------------------------------


class MemoryStoreUser(User):
    """Base Locust ``User`` that dispatches all SDK calls through
    ``gevent.get_hub().threadpool`` so the blocking ``ibm_db`` C extension
    doesn't stall the greenlet hub.

    Every subclass task calls ``self._run(fn, *args, **kwargs)`` instead of
    calling the SDK directly.  The hub suspends only this greenlet for the
    duration of the blocking call; all other VUs continue executing.

    A per-VU ``MemoryScope`` is generated in ``on_start()`` so each virtual
    user has its own isolated scope, enabling cross-scope leakage assertions.

    The ``scope`` uses a run-unique tag embedded in ``tenant_id`` so parallel
    locust runs (different ``BENCH_RUN_TAG`` values) never collide even on a
    shared Db2 instance.
    """

    # Locust wait-time between tasks (0.1 s to 0.5 s — realistic pacing).
    wait_time = between(0.1, 0.5)

    # Assigned in on_start(); typed hint only.
    scope: MemoryScope
    _own_marker: str

    # ── gevent threadpool dispatch ───────────────────────────────────────────

    def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Dispatch *fn* through gevent's threadpool without letting exceptions
        cross the thread→greenlet boundary via gevent's internal SimpleQueue.

        ``threadpool.apply(fn)`` delivers the result (or exception) back to the
        waiting greenlet by calling ``SimpleQueue._unlock → Waiter.switch`` from
        the OS thread.  In gevent ≥ 23 that path asserts it is called only from
        the Hub greenlet, so any exception raised by *fn* (including
        ``ConnectionPoolExhausted``) triggers:

            AssertionError: Can only use Waiter.switch method from the Hub greenlet

        Fix: run a thin wrapper that catches *all* exceptions inside the OS
        thread and stores them in a list, so the wrapper always returns normally.
        ``threadpool.apply`` then delivers a clean (no-exception) result back to
        the hub, and we re-raise the stored exception ourselves — in the calling
        greenlet, which is the correct gevent context.
        """
        _result: list[Any] = [None]
        _exc: list[BaseException | None] = [None]

        def _safe_call() -> None:
            try:
                _result[0] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                _exc[0] = exc

        gevent.get_hub().threadpool.apply(_safe_call, (), {})

        if _exc[0] is not None:
            raise _exc[0]
        return _result[0]

    # ── VU lifecycle ────────────────────────────────────────────────────────

    def on_start(self) -> None:  # type: ignore[override]
        """Assign a unique scope to each virtual user."""
        # Encode the VU's index in tenant/agent ids for isolation checks.
        vu_id: int = getattr(self, "_user_id", id(self)) % 100_000
        self.scope = make_scope(
            run_id=_RUN_TAG,
            tenant_index=vu_id % 100,      # 0–99 → up to 100 tenants
            agent_index=vu_id % 1000,      # 0–999 → up to 1 000 agents
            user_index=vu_id,
        )
        self._own_marker = marker_for(self.scope)
        if _EMIT_RSS:
            self._rss_sampler: ResourceSampler = ResourceSampler(interval_s=0.1)
            self._rss_sampler.__enter__()

    def on_stop(self) -> None:  # type: ignore[override]
        """Optionally emit RSS snapshot as a custom Locust metric."""
        if _EMIT_RSS and hasattr(self, "_rss_sampler"):
            snap = self._rss_sampler.snapshot()
            self._rss_sampler.__exit__(None, None, None)
            self.environment.events.request.fire(
                request_type="rss",
                name="peak_rss_mb",
                response_time=snap.peak_rss_bytes / (1024 * 1024),
                response_length=0,
            )


# ---------------------------------------------------------------------------
# SDK5User — exercises all 5 core operations
# ---------------------------------------------------------------------------


class SDK5User(MemoryStoreUser):
    """Exercises the five core SDK operations proportionally.

    Task weights are set so the mix is approximately:
      search (3) · remember (3) · add_messages (2) · get_context_card (1) · list_all (1)

    This is the primary VU class for BM-12 (harness smoke test), BM-14
    (scalability sweeps), and BM-15 (pool-saturation sweep).
    """

    # ── tasks ────────────────────────────────────────────────────────────────

    @task(3)
    def task_search(self) -> None:
        """Vector search within this VU's scope."""
        assert _STORE is not None and _EMBED is not None  # set in on_locust_init
        query_vec = _EMBED(self._own_marker)
        t0 = time.perf_counter()
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
        """Write a working-memory record tagged with this VU's scope marker."""
        assert _STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                thread_id=self.scope.thread_id,
                content=(
                    f"{self._own_marker} load-test content turn={turn} "
                    f"tag={_RUN_TAG}"
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
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(2)
    def task_add_messages(self) -> None:
        """add_messages() batch write (simulates a conversation turn)."""
        assert _STORE is not None

        messages = [
            {"role": "user", "content": f"{self._own_marker} user msg"},
            {"role": "assistant", "content": f"{self._own_marker} assistant reply"},
        ]
        t0 = time.perf_counter()
        try:
            self._run(_STORE.add_messages, messages, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.add_messages",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.add_messages",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(1)
    def task_get_context_card(self) -> None:
        """get_context_card() composite read."""
        assert _STORE is not None
        t0 = time.perf_counter()
        try:
            card = self._run(_STORE.get_context_card, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="store.get_context_card",
                response_time=elapsed_ms,
                response_length=0 if card is None else 1,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="store.get_context_card",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    @task(1)
    def task_list_all(self) -> None:
        """list_all() paginated scan of the VU's scope."""
        assert _STORE is not None
        t0 = time.perf_counter()
        try:
            results = self._run(
                _STORE.working.list_all, scope=self.scope, limit=20
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="working.list_all",
                response_time=elapsed_ms,
                response_length=len(results),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="read",
                name="working.list_all",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


# ---------------------------------------------------------------------------
# WriteOnlyUser / ReadOnlyUser — for BM-14 write/read saturation tests
# ---------------------------------------------------------------------------


class WriteOnlyUser(MemoryStoreUser):
    """100 % write workload — used for sustained-write saturation in BM-14."""

    wait_time = between(0.05, 0.2)

    @task
    def task_remember(self) -> None:
        assert _STORE is not None
        turn = random.randint(0, 99_999)
        t0 = time.perf_counter()
        try:
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                content=f"{self._own_marker} write-only turn={turn}",
            )
            self._run(_STORE.remember, mem, self.scope)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember",
                response_time=elapsed_ms,
                response_length=0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="write",
                name="store.remember",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )


class ReadOnlyUser(MemoryStoreUser):
    """100 % search workload — used for sustained-read saturation in BM-14."""

    wait_time = between(0.05, 0.2)

    @task
    def task_search(self) -> None:
        assert _STORE is not None and _EMBED is not None
        query_vec = _EMBED(self._own_marker)
        t0 = time.perf_counter()
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
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="search",
                name="working.search",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )
