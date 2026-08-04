"""
tests/test_counting.py
~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for benchmarks/common/counting.py (BM-5, EPIC-13).

All tests use ``unittest.mock`` to fake the ibm_db_dbi layer — no live Db2
instance required.  Covers the accepted round-trip semantics:

* Every ``cursor.execute()`` increments ``RoundTripCounter.executes`` by 1.
* Every ``cursor.fetch*()`` increments ``RoundTripCounter.fetches`` by 1.
* ``CountingPool`` disabled path has zero interception overhead.
* ``RoundTripsFixture.assert_round_trips(n)`` produces a readable failure
  message on mismatch.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.common.counting import (
    CountingConnection,
    CountingCursor,
    CountingPool,
    RoundTripCounter,
    RoundTripsFixture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_cursor():
    cur = MagicMock()
    cur.execute.return_value = None
    cur.fetchone.return_value = {"col": "val"}
    cur.fetchall.return_value = [{"col": "val"}]
    cur.fetchmany.return_value = [{"col": "val"}]
    cur.rowcount = 1
    return cur


def _make_mock_conn(cursor=None):
    conn = MagicMock()
    conn.cursor.return_value = cursor or _make_mock_cursor()
    return conn


@contextmanager
def _fake_pool_context(conn):
    """Helper: a ConnectionPool mock whose get_connection() yields conn."""
    pool = MagicMock()

    @contextmanager
    def _get_connection():
        yield conn

    pool.get_connection = _get_connection
    yield pool


# ---------------------------------------------------------------------------
# RoundTripCounter
# ---------------------------------------------------------------------------


class TestRoundTripCounter:
    def test_initial_state_is_zero(self):
        c = RoundTripCounter()
        assert c.executes == 0
        assert c.fetches == 0
        assert c.total == 0

    def test_reset_clears_both_counters(self):
        c = RoundTripCounter(executes=3, fetches=5)
        c.reset()
        assert c.executes == 0
        assert c.fetches == 0

    def test_total_is_sum(self):
        c = RoundTripCounter(executes=3, fetches=7)
        assert c.total == 10


# ---------------------------------------------------------------------------
# CountingCursor
# ---------------------------------------------------------------------------


class TestCountingCursor:
    def test_execute_increments_executes_only(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        assert counter.executes == 1
        assert counter.fetches == 0
        mock_cur.execute.assert_called_once()

    def test_execute_with_params_passes_through(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.execute("SELECT ? FROM T", ("x",))
        assert counter.executes == 1
        mock_cur.execute.assert_called_once_with("SELECT ? FROM T", ("x",))

    def test_fetchone_increments_fetches_only(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.fetchone()
        assert counter.executes == 0
        assert counter.fetches == 1

    def test_fetchall_increments_fetches_only(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.fetchall()
        assert counter.fetches == 1

    def test_fetchmany_increments_fetches_only(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.fetchmany(10)
        assert counter.fetches == 1

    def test_execute_then_fetchone_correct_totals(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        cur.fetchone()
        assert counter.executes == 1
        assert counter.fetches == 1
        assert counter.total == 2

    def test_getattr_delegation_passes_through(self):
        mock_cur = _make_mock_cursor()
        mock_cur.rowcount = 42
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        assert cur.rowcount == 42

    def test_executemany_counts_per_row(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        cur.executemany("INSERT INTO T VALUES (?)", [("a",), ("b",), ("c",)])
        assert counter.executes == 3

    def test_multiple_executes_accumulate(self):
        mock_cur = _make_mock_cursor()
        counter = RoundTripCounter()
        cur = CountingCursor(mock_cur, counter)
        for _ in range(5):
            cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        assert counter.executes == 5


# ---------------------------------------------------------------------------
# CountingConnection
# ---------------------------------------------------------------------------


class TestCountingConnection:
    def test_cursor_returns_counting_cursor(self):
        mock_cur = _make_mock_cursor()
        mock_conn = _make_mock_conn(mock_cur)
        counter = RoundTripCounter()
        conn = CountingConnection(mock_conn, counter)
        cur = conn.cursor()
        assert isinstance(cur, CountingCursor)

    def test_commit_delegated(self):
        mock_conn = _make_mock_conn()
        conn = CountingConnection(mock_conn, RoundTripCounter())
        conn.commit()
        mock_conn.commit.assert_called_once()

    def test_rollback_delegated(self):
        mock_conn = _make_mock_conn()
        conn = CountingConnection(mock_conn, RoundTripCounter())
        conn.rollback()
        mock_conn.rollback.assert_called_once()

    def test_counting_cursor_shares_counter(self):
        mock_cur = _make_mock_cursor()
        mock_conn = _make_mock_conn(mock_cur)
        counter = RoundTripCounter()
        conn = CountingConnection(mock_conn, counter)
        cur1 = conn.cursor()
        cur2 = conn.cursor()
        cur1.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        cur2.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        assert counter.executes == 2


# ---------------------------------------------------------------------------
# CountingPool
# ---------------------------------------------------------------------------


class TestCountingPool:
    def test_disabled_yields_real_connection_directly(self):
        """When counter is None, the pool is a transparent passthrough."""
        mock_conn = _make_mock_conn()
        with _fake_pool_context(mock_conn) as pool:
            cp = CountingPool(pool)
            with cp.get_connection() as conn:
                assert conn is mock_conn  # direct reference, not wrapped

    def test_enabled_yields_counting_connection(self):
        mock_conn = _make_mock_conn()
        with _fake_pool_context(mock_conn) as pool:
            cp = CountingPool(pool)
            counter = RoundTripCounter()
            cp.enable(counter)
            with cp.get_connection() as conn:
                assert isinstance(conn, CountingConnection)

    def test_counter_shared_across_multiple_cursors_same_connection(self):
        mock_cur = _make_mock_cursor()
        mock_conn = _make_mock_conn(mock_cur)
        with _fake_pool_context(mock_conn) as pool:
            cp = CountingPool(pool)
            counter = RoundTripCounter()
            cp.enable(counter)
            with cp.get_connection() as conn:
                conn.cursor().execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
                conn.cursor().execute("SELECT 2 FROM SYSIBM.SYSDUMMY1")
            assert counter.executes == 2

    def test_disable_stops_counting(self):
        mock_cur = _make_mock_cursor()
        mock_conn = _make_mock_conn(mock_cur)
        with _fake_pool_context(mock_conn) as pool:
            cp = CountingPool(pool)
            counter = RoundTripCounter()
            cp.enable(counter)
            cp.disable()
            # After disable, get_connection yields the real connection
            with cp.get_connection() as conn:
                assert conn is mock_conn

    def test_close_delegates_to_real_pool(self):
        mock_conn = _make_mock_conn()
        with _fake_pool_context(mock_conn) as pool:
            cp = CountingPool(pool)
            cp.close()
            pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# RoundTripsFixture
# ---------------------------------------------------------------------------


class TestRoundTripsFixture:
    def test_assert_round_trips_passes_on_exact_match(self):
        counter = RoundTripCounter(executes=1)
        fixture = RoundTripsFixture(counter)
        fixture.assert_round_trips(1)  # must not raise

    def test_assert_round_trips_fails_on_mismatch_with_clear_message(self):
        counter = RoundTripCounter(executes=3)
        fixture = RoundTripsFixture(counter)
        with pytest.raises(AssertionError, match="Expected 1 DB round-trip"):
            fixture.assert_round_trips(1)

    def test_assert_message_includes_fetch_count(self):
        counter = RoundTripCounter(executes=2, fetches=4)
        fixture = RoundTripsFixture(counter)
        with pytest.raises(AssertionError, match="Fetch calls: 4"):
            fixture.assert_round_trips(0)

    def test_reset_zeroes_counter(self):
        counter = RoundTripCounter(executes=5, fetches=3)
        fixture = RoundTripsFixture(counter)
        fixture.reset()
        assert fixture.counter.executes == 0
        assert fixture.counter.fetches == 0
