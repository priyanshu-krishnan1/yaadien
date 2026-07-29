"""
tests/test_connection.py
~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the ConnectionPool that do NOT require a live Db2 instance.
ibm_db / ibm_db_dbi are patched out entirely.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a fake ibm_db environment
# ---------------------------------------------------------------------------

def _make_fake_ibm_db(connect_ok: bool = True, close_raises: bool = False):
    """Return a mock ibm_db module."""
    mod = MagicMock()
    if connect_ok:
        fake_handle = MagicMock()
        mod.connect.return_value = fake_handle
    else:
        mod.connect.side_effect = Exception("simulated connect failure")
        mod.conn_errormsg.return_value = "simulated error message"
    if close_raises:
        mod.close.side_effect = Exception("close error")
    return mod


def _make_fake_ibm_db_dbi():
    """Return a mock ibm_db_dbi module whose Connection is a plain MagicMock."""
    mod = MagicMock()
    mod.Connection.return_value = MagicMock()
    return mod


# ---------------------------------------------------------------------------
# _build_conn_str
# ---------------------------------------------------------------------------

class TestBuildConnStr:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("DB2_DATABASE", "TESTDB")
        monkeypatch.setenv("DB2_HOSTNAME", "myhost")
        monkeypatch.setenv("DB2_UID", "user1")
        monkeypatch.setenv("DB2_PWD", "secret")
        monkeypatch.delenv("DB2_SECURITY", raising=False)
        monkeypatch.delenv("DB2_PORT", raising=False)

        fake_ibm_db = _make_fake_ibm_db()
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            cs = mod._build_conn_str()

        assert "DATABASE=TESTDB" in cs
        assert "HOSTNAME=myhost" in cs
        assert "PORT=50000" in cs
        assert "PROTOCOL=TCPIP" in cs
        assert "UID=user1" in cs
        assert "PWD=secret" in cs
        assert "Security" not in cs

    def test_ssl_added_when_security_set(self, monkeypatch):
        monkeypatch.setenv("DB2_DATABASE", "TESTDB")
        monkeypatch.setenv("DB2_HOSTNAME", "myhost")
        monkeypatch.setenv("DB2_UID", "user1")
        monkeypatch.setenv("DB2_PWD", "secret")
        monkeypatch.setenv("DB2_SECURITY", "SSL")

        fake_ibm_db = _make_fake_ibm_db()
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            cs = mod._build_conn_str()

        assert "Security=SSL" in cs

    def test_missing_required_vars_raises(self, monkeypatch):
        for var in ("DB2_DATABASE", "DB2_HOSTNAME", "DB2_UID", "DB2_PWD"):
            monkeypatch.delenv(var, raising=False)

        fake_ibm_db = _make_fake_ibm_db()
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            with pytest.raises(EnvironmentError, match="Missing required"):
                mod._build_conn_str()


# ---------------------------------------------------------------------------
# ConnectionPool
# ---------------------------------------------------------------------------

class TestConnectionPool:
    def _make_pool(self, monkeypatch, pool_size: int = 2, connect_ok: bool = True):
        """Patch ibm_db/ibm_db_dbi and return a live ConnectionPool."""
        monkeypatch.setenv("DB2_DATABASE", "TESTDB")
        monkeypatch.setenv("DB2_HOSTNAME", "localhost")
        monkeypatch.setenv("DB2_UID", "u")
        monkeypatch.setenv("DB2_PWD", "p")
        monkeypatch.delenv("DB2_SECURITY", raising=False)

        fake_ibm_db = _make_fake_ibm_db(connect_ok=connect_ok)
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            pool = mod.ConnectionPool(pool_size=pool_size, pool_timeout=1)
        return pool, fake_ibm_db, fake_dbi, mod

    def test_pool_opens_correct_number_of_connections(self, monkeypatch):
        pool, fake_ibm_db, _, _ = self._make_pool(monkeypatch, pool_size=3)
        assert fake_ibm_db.connect.call_count == 3
        pool.close()

    def test_get_connection_wraps_handle_in_dbi(self, monkeypatch):
        pool, fake_ibm_db, fake_dbi, mod = self._make_pool(monkeypatch, pool_size=1)
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}), pool.get_connection() as conn:
            assert conn is fake_dbi.Connection.return_value
        pool.close()

    def test_connection_returned_to_pool_after_context(self, monkeypatch):
        pool, fake_ibm_db, fake_dbi, mod = self._make_pool(monkeypatch, pool_size=1)
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            with pool.get_connection():
                pass
            # After the context exits the handle should be back
            assert pool._pool.qsize() == 1
        pool.close()

    def test_pool_exhaustion_raises(self, monkeypatch):
        pool, fake_ibm_db, fake_dbi, mod = self._make_pool(monkeypatch, pool_size=1)
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            # Drain the pool manually
            pool._pool.get_nowait()
            with pytest.raises(mod.ConnectionPoolExhausted), pool.get_connection():
                pass
        pool.close()

    def test_closed_pool_raises_on_get(self, monkeypatch):
        pool, fake_ibm_db, fake_dbi, mod = self._make_pool(monkeypatch, pool_size=1)
        pool.close()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}), pytest.raises(mod.ConnectionError, match="closed"), pool.get_connection():
            pass

    def test_connect_failure_raises_connection_error(self, monkeypatch):
        monkeypatch.setenv("DB2_DATABASE", "TESTDB")
        monkeypatch.setenv("DB2_HOSTNAME", "localhost")
        monkeypatch.setenv("DB2_UID", "u")
        monkeypatch.setenv("DB2_PWD", "p")
        fake_ibm_db = _make_fake_ibm_db(connect_ok=False)
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            with pytest.raises(mod.ConnectionError, match="Failed to connect"):
                mod.ConnectionPool(pool_size=1, pool_timeout=1)

    def test_context_manager_interface(self, monkeypatch):
        monkeypatch.setenv("DB2_DATABASE", "TESTDB")
        monkeypatch.setenv("DB2_HOSTNAME", "localhost")
        monkeypatch.setenv("DB2_UID", "u")
        monkeypatch.setenv("DB2_PWD", "p")
        fake_ibm_db = _make_fake_ibm_db()
        fake_dbi = _make_fake_ibm_db_dbi()
        with patch.dict("sys.modules", {"ibm_db": fake_ibm_db, "ibm_db_dbi": fake_dbi}):
            from importlib import reload

            import agent_memory_sdk.db.connection as mod
            reload(mod)
            with mod.ConnectionPool(pool_size=1, pool_timeout=1) as pool:
                assert not pool._closed
            assert pool._closed
