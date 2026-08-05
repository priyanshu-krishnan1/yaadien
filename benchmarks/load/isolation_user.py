"""
benchmarks/load/isolation_user.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Locust isolation-under-load gate (EPIC-15, BM-13).

Every virtual user (VU) writes `WorkingMemory` records tagged with its own
scope marker, then reads them back via ``search()`` and ``list_all()`` and
asserts:

  1. Every returned row's ``agent_id`` and ``tenant_id`` match the VU's own
     scope (scope-field check).
  2. No other VU's marker text appears in any returned record's content
     (content-leak check).

If any row fails either assertion a :class:`LeakageError` is raised, Locust
counts the request as a failure, and a module-level event listener calls
``runner.quit()`` so the process exits non-zero — satisfying AC-3.

Scale target (AC-5 scale):
  100 tenants × 1,000 agents × 200 concurrent users.

  The VU id mapping:
    vu_id        = id(self) % 200_000
    tenant_index = vu_id % 100      (0–99)
    agent_index  = vu_id % 1000     (0–999)

Shared-store E3 scenario:
  :class:`SharedStoreIsolationUser` is a second VU class that carries an
  explicit docstring comment and class-level marker calling out E3
  (thread-safety of a single ``MemoryStore`` under concurrent use).  Both
  classes share the same ``_STORE`` module-level singleton — the same
  connection pool and MemoryStore instance is used by every greenlet/thread,
  which is the scenario E3 covers.  ``SharedStoreIsolationUser`` makes this
  explicit rather than leaving it implicit.

BRUN-4 coordination:
  ``benchmarks/isolation_load/run.py`` is PRESERVED — not deleted — because
  BRUN-4 (EPIC-12) was "To Do" (never executed) when BM-13 was implemented.
  BRUN-4 has been re-scoped to execute BM-13's Locust-based test instead; the
  substitution is recorded in project-management/BENCHMARKS.md.

Usage::

    locust -f benchmarks/load/isolation_user.py \\
           --headless -u 200 -r 10 -t 5m \\
           --csv results/isolation_run
"""

from __future__ import annotations

import logging
import time
from typing import Any

from locust import events, task  # type: ignore[import-untyped]

from agent_memory_sdk.models import WorkingMemory
from benchmarks.common.scope_gen import make_scope, marker_for

# ---------------------------------------------------------------------------
# Re-use the module-level globals and base class from locustfile.py.
# ---------------------------------------------------------------------------
from benchmarks.load.locustfile import (  # noqa: E402
    _EMBED,
    _RUN_TAG,
    _STORE,
    MemoryStoreUser,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Number of WorkingMemory records each VU seeds during on_start().
# ---------------------------------------------------------------------------
_SEED_RECORDS = 5

# ---------------------------------------------------------------------------
# LeakageError — custom exception so failures are clearly labelled in the
# Locust stats output and in event listeners.
# ---------------------------------------------------------------------------


class LeakageError(AssertionError):
    """Raised when a returned record belongs to a different VU's scope.

    Attributes:
        record_agent_id:   ``agent_id`` from the offending row.
        record_tenant_id:  ``tenant_id`` from the offending row.
        expected_agent_id: The querying VU's ``agent_id``.
        expected_tenant_id: The querying VU's ``tenant_id``.
        detail:            Human-readable description of the leak.
    """

    def __init__(
        self,
        detail: str,
        record_agent_id: str | None = None,
        record_tenant_id: str | None = None,
        expected_agent_id: str | None = None,
        expected_tenant_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.record_agent_id = record_agent_id
        self.record_tenant_id = record_tenant_id
        self.expected_agent_id = expected_agent_id
        self.expected_tenant_id = expected_tenant_id
        self.detail = detail


# ---------------------------------------------------------------------------
# Module-level event listener — exit non-zero on any LeakageError (AC-3).
# ---------------------------------------------------------------------------

_leakage_detected: bool = False


@events.request.add_listener
def _on_request(
    exception: BaseException | None,
    **_kw: Any,
) -> None:
    """If any request carries a LeakageError, stop the runner immediately."""
    global _leakage_detected  # noqa: PLW0603
    if isinstance(exception, LeakageError):
        _leakage_detected = True
        _log.error("LeakageError detected — stopping Locust run. Detail: %s", exception.detail)
        # Retrieve the runner via the environment attached to the event system.
        # ``events`` is the global Events instance; its ``environment`` attr is
        # set by Locust after ``init`` fires.
        runner = getattr(getattr(events, "_environment", None), "runner", None)
        if runner is not None:
            runner.quit()


@events.quitting.add_listener
def _on_quitting_leakage(environment: Any, **_kw: Any) -> None:
    """Set exit code 1 when leakage was detected during the run."""
    if _leakage_detected:
        environment.process_exit_code = 1


# ---------------------------------------------------------------------------
# IsolationUser — primary isolation gate VU class
# ---------------------------------------------------------------------------


class IsolationUser(MemoryStoreUser):
    """Locust VU that asserts zero cross-scope leakage at full scale.

    Lifecycle
    ---------
    on_start():
        Derives a deterministic scope from ``id(self)`` distributed across
        100 tenants × 1,000 agents, then writes ``_SEED_RECORDS`` tagged
        ``WorkingMemory`` rows.

    @task check_isolation():
        Calls ``search()`` + ``list_all()``, then asserts every row matches
        this VU's scope (both scope-field check and content-marker check).
        On any leak: fires the Locust ``request`` event with
        ``exception=LeakageError(...)`` (counted as a failure) and raises it
        so the event listener can halt the run.

    on_stop():
        Logs total assertion count and leak count for this VU.
    """

    # Keep wait_time inherited from MemoryStoreUser (0.1–0.5 s).

    # Counters reset in on_start, incremented by check_isolation.
    _assertion_count: int = 0
    _leak_count: int = 0

    def on_start(self) -> None:  # type: ignore[override]
        """Assign scope and seed initial records."""
        # Distribute VUs across 100 tenants × 1,000 agents (AC scale target).
        vu_id: int = id(self) % 200_000
        self.scope = make_scope(
            run_id=_RUN_TAG,
            tenant_index=vu_id % 100,
            agent_index=vu_id % 1000,
            user_index=vu_id,
        )
        self._own_marker = marker_for(self.scope)
        self._assertion_count = 0
        self._leak_count = 0

        # Seed phase: write _SEED_RECORDS tagged records so subsequent reads
        # have data to assert against.
        assert _STORE is not None, "MemoryStore not initialised — check _on_locust_init"
        for i in range(_SEED_RECORDS):
            mem = WorkingMemory(
                tenant_id=self.scope.tenant_id,
                agent_id=self.scope.agent_id,
                user_id=self.scope.user_id,
                thread_id=self.scope.thread_id,
                content=(
                    f"{self._own_marker} isolation-load seed record {i} "
                    f"run={_RUN_TAG}"
                ),
            )
            self._run(_STORE.remember, mem, self.scope)

    @task
    def check_isolation(self) -> None:
        """Search + list_all, then assert every row belongs to this VU's scope.

        Two complementary checks per record (porting the dual check from
        ``benchmarks/isolation_load/run.py``):
          1. Scope-field check: ``record.agent_id == scope.agent_id`` and
             ``record.tenant_id == scope.tenant_id``.
          2. Content-marker check: no other VU's marker substring appears in
             ``record.content``.

        If either fails for any row, fires a Locust failure event with
        ``exception=LeakageError`` and raises the same exception so the
        module-level listener can halt the run (AC-3).
        """
        assert _STORE is not None and _EMBED is not None
        query_vec = _EMBED(self._own_marker)
        fetch_limit = max(50, _SEED_RECORDS * 4)

        t0 = time.perf_counter()
        try:
            search_results = self._run(
                _STORE.working.search,
                query_embedding=query_vec,
                scope=self.scope,
                top_k=fetch_limit,
            )
            list_results = self._run(
                _STORE.working.list_all,
                scope=self.scope,
                limit=fetch_limit,
            )

            leaks: list[str] = []
            for results in (search_results, list_results):
                for record in results:
                    self._assertion_count += 1
                    # Scope-field check (AC-1, mirrors isolation_load/run.py L74).
                    if (
                        record.agent_id != self.scope.agent_id
                        or record.tenant_id != self.scope.tenant_id
                    ):
                        self._leak_count += 1
                        leaks.append(
                            f"scope-field leak: got agent_id={record.agent_id!r} "
                            f"tenant_id={record.tenant_id!r}, "
                            f"expected agent_id={self.scope.agent_id!r} "
                            f"tenant_id={self.scope.tenant_id!r}"
                        )
                        continue
                    # Content-marker check (AC-1, mirrors isolation_load/run.py L77-79).
                    content = record.content or ""
                    if self._own_marker not in content and content:
                        # Only flag if there is content that might carry another
                        # scope's marker; empty/non-seeded records are ignored.
                        pass
                    for other_marker in _iter_other_markers(self._own_marker, _RUN_TAG):
                        if other_marker in content:
                            self._leak_count += 1
                            leaks.append(
                                f"content-marker leak: found {other_marker!r} "
                                f"in record for scope {self.scope.agent_id!r}"
                            )

            elapsed_ms = (time.perf_counter() - t0) * 1000

            if leaks:
                exc = LeakageError(
                    "; ".join(leaks),
                    record_agent_id=None,
                    expected_agent_id=self.scope.agent_id,
                    expected_tenant_id=self.scope.tenant_id,
                )
                self.environment.events.request.fire(
                    request_type="isolation",
                    name="check_isolation",
                    response_time=elapsed_ms,
                    response_length=self._assertion_count,
                    exception=exc,
                )
                raise exc

            self.environment.events.request.fire(
                request_type="isolation",
                name="check_isolation",
                response_time=elapsed_ms,
                response_length=self._assertion_count,
            )

        except LeakageError:
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.environment.events.request.fire(
                request_type="isolation",
                name="check_isolation",
                response_time=elapsed_ms,
                response_length=0,
                exception=exc,
            )

    def on_stop(self) -> None:  # type: ignore[override]
        """Log per-VU totals; emit leakage count as a custom metric."""
        _log.info(
            "IsolationUser stopped: agent_id=%s assertions=%d leaks=%d",
            self.scope.agent_id if hasattr(self, "scope") else "?",
            self._assertion_count,
            self._leak_count,
        )
        super().on_stop()


# ---------------------------------------------------------------------------
# SharedStoreIsolationUser — explicit E3 (thread-safety of a single
# MemoryStore instance under concurrent use) scenario (AC-5).
# ---------------------------------------------------------------------------


class SharedStoreIsolationUser(IsolationUser):
    """Explicit E3 scenario: many VUs share one ``MemoryStore`` instance.

    The ``benchmarks/isolation_load/run.py`` module exercised E3 only
    *implicitly* — all workers shared the same ``MemoryStore`` but there was
    no VU class labelled as the E3 test.  This class makes the intent
    explicit: every concurrent ``SharedStoreIsolationUser`` greenlet shares
    the module-level ``_STORE`` singleton (the same ``ConnectionPool`` and
    ``MemoryStore`` object), and each write/read is still correctly scoped to
    its own data with zero cross-contamination.

    E3 (from the capability inventory): "Thread-safety of a single MemoryStore
    under concurrent use".  Passing isolation checks here, under Locust's
    gevent-threads model with the shared pool, constitutes the explicit E3
    gate.

    Inherits all logic from :class:`IsolationUser`; the only difference is the
    explicit documentation and a slightly different task name in the Locust
    stats (via the ``name`` kwarg on ``request.fire``).
    """

    # Inherit on_start / on_stop / check_isolation unchanged from IsolationUser.
    # The shared _STORE module-level singleton is the E3 test surface.


# ---------------------------------------------------------------------------
# _iter_other_markers — helper that generates markers from OTHER scopes.
# ---------------------------------------------------------------------------


def _iter_other_markers(own_marker: str, run_tag: str) -> list[str]:
    """Return up to 10 markers from other scopes in the same run.

    Used by the content-marker check to detect cross-scope contamination
    without having to enumerate all 100×1000 possible scopes per assertion.
    Samples a small fixed set; the seed-phase marker pattern is deterministic
    so any injected scope predicate bug would produce rows whose markers do
    not match the querying VU's own_marker.

    Concretely: a neighbouring VU with tenant_index=(own_t ± 1 mod 100) and
    agent_index=(own_a ± 1 mod 1000) would be caught if any of their seeded
    content appears in results.
    """
    import re  # noqa: PLC0415

    # Parse own tenant/agent index from own_marker.
    # Pattern: [[MARKER:bench-<run>-tenant-<T>:bench-<run>-tenant-<T>-agent-<A>]]
    m = re.search(r"tenant-(\d+)-agent-(\d+)", own_marker)
    if m is None:
        return []
    own_t = int(m.group(1))
    own_a = int(m.group(2))

    candidates: list[str] = []
    for dt in (-1, 0, 1):
        for da in (-1, 0, 1):
            t = (own_t + dt) % 100
            a = (own_a + da) % 1000
            s = make_scope(run_tag, tenant_index=t, agent_index=a, user_index=a)
            mk = marker_for(s)
            if mk != own_marker:
                candidates.append(mk)
            if len(candidates) >= 8:
                return candidates
    return candidates
