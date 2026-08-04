"""
benchmarks/common/counting.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
DB round-trip counting proxy for the benchmark suite (EPIC-13, BM-5).

Provides a transparent ``CountingPool`` / ``CountingConnection`` /
``CountingCursor`` proxy stack that intercepts every ``cursor.execute()``
and ``cursor.fetch*()`` call and increments a shared ``RoundTripCounter``.
This is the highest-value custom primitive in the benchmarking strategy:
nothing off-the-shelf knows what constitutes "one SDK call," and this is
the *only* metric in the whole strategy that is genuinely immune to GHA
runner-speed noise — which is why Tier 1 (EPIC-17, BM-20) gates PRs on
round-trip counts instead of wall-clock time.

Usage
-----
::

    # In a benchmark test:
    from benchmarks.common.counting import CountingPool, round_trips

    # Wrap the session-scoped pool (done once, in conftest.py):
    counting = CountingPool(db_pool)

    # Within a test body, inject via the round_trips fixture:
    def test_get_by_id_costs_one_execute(round_trips, counting_pool):
        repo = WorkingMemoryRepository(pool=counting_pool)
        with round_trips:
            repo.get_by_id(some_id, scope)
        round_trips.assert_round_trips(1)

pytest fixtures
---------------
``counting_pool`` (session) — wraps the BM-3 ``db_pool``.
``round_trips``   (function) — resets the counter, yields a
                              ``RoundTripsFixture``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from dataclasses import dataclass

import pytest

# ---------------------------------------------------------------------------
# RoundTripCounter — shared mutable state
# ---------------------------------------------------------------------------


@dataclass
class RoundTripCounter:
    """Tracks execute and fetch calls made through the counting proxy."""

    executes: int = 0
    fetches: int = 0

    @property
    def total(self) -> int:
        """Total of executes + fetches."""
        return self.executes + self.fetches

    def reset(self) -> None:
        self.executes = 0
        self.fetches = 0


# ---------------------------------------------------------------------------
# CountingCursor — wraps ibm_db_dbi.Cursor
# ---------------------------------------------------------------------------


class CountingCursor:
    """Transparent proxy around an ``ibm_db_dbi.Cursor`` that increments a
    shared ``RoundTripCounter`` on every execute / fetch call.

    All other attributes are delegated to the real cursor unchanged so callers
    see a completely normal DB-API 2.0 cursor.
    """

    def __init__(self, real_cursor: object, counter: RoundTripCounter) -> None:
        self._cur = real_cursor
        self._counter = counter

    # ── interception points ──────────────────────────────────────────────

    def execute(self, operation: str, parameters: object = None) -> object:
        self._counter.executes += 1
        if parameters is None:
            return self._cur.execute(operation)  # type: ignore[union-attr]
        return self._cur.execute(operation, parameters)  # type: ignore[union-attr]

    def executemany(self, operation: str, seq_of_parameters: object) -> object:
        # Conservative: count one execute per parameter set.
        try:
            n = len(seq_of_parameters)  # type: ignore[arg-type]
        except TypeError:
            n = 1
        self._counter.executes += n
        return self._cur.executemany(operation, seq_of_parameters)  # type: ignore[union-attr]

    def fetchone(self) -> object:
        self._counter.fetches += 1
        return self._cur.fetchone()  # type: ignore[union-attr]

    def fetchall(self) -> object:
        self._counter.fetches += 1
        return self._cur.fetchall()  # type: ignore[union-attr]

    def fetchmany(self, size: int | None = None) -> object:
        self._counter.fetches += 1
        if size is None:
            return self._cur.fetchmany()  # type: ignore[union-attr]
        return self._cur.fetchmany(size)  # type: ignore[union-attr]

    # ── transparent delegation for everything else ──────────────────────

    def __getattr__(self, name: str) -> object:
        return getattr(self._cur, name)

    def __enter__(self) -> CountingCursor:
        return self

    def __exit__(self, *args: object) -> None:
        self._cur.close()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# CountingConnection — wraps ibm_db_dbi.Connection
# ---------------------------------------------------------------------------


class CountingConnection:
    """Proxy around an ``ibm_db_dbi.Connection``.

    Overrides ``cursor()`` to return a ``CountingCursor`` wired to the shared
    counter.  All other methods (commit, rollback, close) are delegated.
    """

    def __init__(self, real_conn: object, counter: RoundTripCounter) -> None:
        self._conn = real_conn
        self._counter = counter

    def cursor(self) -> CountingCursor:
        return CountingCursor(self._conn.cursor(), self._counter)  # type: ignore[union-attr]

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# CountingPool — wraps ConnectionPool
# ---------------------------------------------------------------------------


class CountingPool:
    """Wraps a ``ConnectionPool`` and injects ``CountingConnection`` on every
    ``get_connection()`` checkout when a counter is active.

    When ``counter`` is ``None`` (disabled), ``get_connection()`` forwards
    directly to the real pool with zero overhead — validated by a benchmark
    micro-test per the BM-5 acceptance criteria.
    """

    def __init__(self, real_pool: object) -> None:
        self._pool = real_pool
        self._counter: RoundTripCounter | None = None

    def enable(self, counter: RoundTripCounter) -> None:
        self._counter = counter

    def disable(self) -> None:
        self._counter = None

    @contextlib.contextmanager
    def get_connection(self) -> Generator[object, None, None]:
        if self._counter is None:
            # Fast path — zero overhead, direct delegation.
            with self._pool.get_connection() as conn:  # type: ignore[union-attr]
                yield conn
        else:
            counter = self._counter  # capture for thread safety
            with self._pool.get_connection() as conn:  # type: ignore[union-attr]
                yield CountingConnection(conn, counter)

    def close(self) -> None:
        self._pool.close()  # type: ignore[union-attr]

    def __enter__(self) -> CountingPool:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# RoundTripsFixture — yielded by the round_trips fixture
# ---------------------------------------------------------------------------


class RoundTripsFixture:
    """Object yielded by the ``round_trips`` pytest fixture.

    Attributes:
        counter: live ``RoundTripCounter`` (read ``executes`` / ``fetches``
                 directly for fine-grained assertions).

    Methods:
        assert_round_trips(n): assert ``counter.executes == n`` with a clear
            failure message.
        reset(): manual mid-test counter reset (rarely needed — the fixture
            resets automatically between tests).
    """

    def __init__(self, counter: RoundTripCounter) -> None:
        self.counter = counter

    def assert_round_trips(self, n: int) -> None:
        """Assert exactly *n* ``execute`` calls were made since the last reset."""
        actual = self.counter.executes
        assert actual == n, (
            f"Expected {n} DB round-trip(s) (execute calls), got {actual}. "
            f"Fetch calls: {self.counter.fetches}."
        )

    def reset(self) -> None:
        self.counter.reset()


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def counting_pool(db_pool: object) -> CountingPool:
    """Session-scoped.  Wraps the BM-3 ``db_pool`` in a ``CountingPool``.

    Counting is disabled by default; the ``round_trips`` fixture enables it
    per-test and disables it on teardown.
    """
    return CountingPool(db_pool)


@pytest.fixture()
def round_trips(counting_pool: CountingPool) -> Generator[RoundTripsFixture, None, None]:
    """Function-scoped.

    1. Creates a fresh ``RoundTripCounter``.
    2. Enables counting on the session-scoped ``CountingPool``.
    3. Yields a ``RoundTripsFixture`` for assertions.
    4. Disables counting (counter goes out of scope).
    """
    counter = RoundTripCounter()
    counting_pool.enable(counter)
    try:
        yield RoundTripsFixture(counter)
    finally:
        counting_pool.disable()
