"""
db/connection.py
~~~~~~~~~~~~~~~~
Db2 LUW connection pool for agent-memory-sdk.

ibm_db / ibm_db_dbi notes (confirmed against IBM docs + PyPI):

1. DRIVER INSTALL
   ``pip install ibm_db`` auto-downloads a bundled clidriver; no separate
   manual install is required for most platforms.
   - Set IBM_DB_HOME to an existing full DB2 client installation to use that
     instead of the bundled one.
   - On Windows with Python 3.8+, you must call
     ``os.add_dll_directory('<clidriver>/bin')`` before importing ibm_db.
     This module handles that guard automatically when IBM_DB_WIN_DLL_DIR
     is set.
   - On macOS ARM64, ibm_db pins the bundled clidriver to v12.1.0.

2. CONNECTION STRING FORMAT
   ibm_db supports only ODBC/CLI keyword pairs — NOT a JDBC URL.
   Minimal TCP/IP form:
     DATABASE=<db>;HOSTNAME=<host>;PORT=<port>;PROTOCOL=TCPIP;UID=<u>;PWD=<p>
   With SSL (required for IBM Cloud / Db2 SaaS):
     ... ;Security=SSL
   Call: ibm_db.connect(conn_str, '', '')
   (second/third args are legacy positional username/password; always pass
   empty strings when credentials are already in the keyword string)

3. DB-API 2.0 WRAPPER
   ibm_db_dbi.Connection is constructed from an already-open ibm_db handle:
     ibm_db_conn = ibm_db.connect(conn_str, '', '')
     dbi_conn    = ibm_db_dbi.Connection(ibm_db_conn)
   ibm_db_dbi has NO built-in pooling — this module provides a bounded
   queue-based pool.

4. POOLING STRATEGY
   A ``queue.Queue`` of ``ibm_db`` raw handles is pre-created at startup.
   ``get_connection()`` checks one out and wraps it in a ``ibm_db_dbi``
   ``Connection`` for the caller; on exit the raw handle is returned to the
   queue.  ``ibm_db_dbi.Connection`` is a thin wrapper with no teardown
   overhead, so we discard it on checkin and re-wrap on next checkout.

   The pool size is bounded by MAX_POOL_SIZE (default 5). If the pool is
   exhausted and POOL_TIMEOUT seconds elapse without a free connection,
   ``ConnectionPoolExhausted`` is raised rather than blocking indefinitely.

Environment variables (see .env.example):
  DB2_DATABASE  – database name (required)
  DB2_HOSTNAME  – server hostname or IP (required)
  DB2_PORT      – TCP port (default 50000)
  DB2_UID       – username (required)
  DB2_PWD       – password (required)
  DB2_SECURITY  – set to "SSL" to enable encrypted transport (optional)
  DB2_POOL_SIZE – pool size (default 5, max 20)
  DB2_POOL_TIMEOUT – seconds to wait for a free connection (default 30)
  IBM_DB_WIN_DLL_DIR – Windows only: path to clidriver/bin (optional)
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import sys
from collections.abc import Generator

# Windows Python 3.8+: must register the clidriver DLL directory before
# importing ibm_db, otherwise the import fails with a DLL load error.
if sys.platform == "win32":
    _dll_dir = os.environ.get("IBM_DB_WIN_DLL_DIR")
    if _dll_dir:
        os.add_dll_directory(_dll_dir)

import ibm_db  # noqa: E402  (import after optional DLL registration)
import ibm_db_dbi  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConnectionError(RuntimeError):
    """Raised when a new Db2 connection cannot be established."""


class ConnectionPoolExhausted(RuntimeError):
    """Raised when the pool has no free connections within the timeout."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_DEFAULT_PORT = "50000"
_DEFAULT_POOL_SIZE = 5
_DEFAULT_POOL_TIMEOUT = 30  # seconds


def _build_conn_str() -> str:
    """Build the ODBC keyword connection string from environment variables.

    Raises:
        EnvironmentError: if any required variable is missing.
    """
    required = {
        "DATABASE": os.environ.get("DB2_DATABASE"),
        "HOSTNAME": os.environ.get("DB2_HOSTNAME"),
        "UID":      os.environ.get("DB2_UID"),
        "PWD":      os.environ.get("DB2_PWD"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise OSError(
            f"Missing required Db2 env vars: {', '.join(missing)}. "
            "See .env.example for the full list."
        )

    port     = os.environ.get("DB2_PORT", _DEFAULT_PORT)
    security = os.environ.get("DB2_SECURITY", "").upper()

    parts = [
        f"DATABASE={required['DATABASE']}",
        f"HOSTNAME={required['HOSTNAME']}",
        f"PORT={port}",
        "PROTOCOL=TCPIP",
        f"UID={required['UID']}",
        f"PWD={required['PWD']}",
    ]
    if security == "SSL":
        parts.append("Security=SSL")

    return ";".join(parts)


def _open_raw_connection(conn_str: str) -> ibm_db.IBM_DBConnection:
    """Open a single non-persistent ibm_db connection.

    Args:
        conn_str: ODBC keyword connection string.

    Returns:
        An open ``ibm_db.IBM_DBConnection`` handle.

    Raises:
        ConnectionError: if ibm_db.connect fails.
    """
    try:
        # ibm_db.connect(conn_str, user, password)
        # Pass empty strings for user/password — credentials are already
        # embedded in the keyword string, which is the recommended form.
        conn = ibm_db.connect(conn_str, "", "")
    except Exception as exc:  # ibm_db raises a generic Exception subclass
        diag = ibm_db.conn_errormsg() or str(exc)
        raise ConnectionError(f"Failed to connect to Db2: {diag}") from exc
    if not conn:
        diag = ibm_db.conn_errormsg()
        raise ConnectionError(f"ibm_db.connect returned falsy; error: {diag}")
    return conn


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

class ConnectionPool:
    """Bounded pool of ``ibm_db`` connection handles.

    Usage::

        pool = ConnectionPool()          # reads env vars
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")

    Thread-safety: ``queue.Queue`` is thread-safe; each ``get_connection()``
    call checks out exactly one handle and returns it on exit, so concurrent
    callers each get a distinct connection from the pool.
    """

    def __init__(
        self,
        conn_str: str | None = None,
        pool_size: int | None = None,
        pool_timeout: int | None = None,
    ) -> None:
        """Create the pool and pre-open all connections.

        Args:
            conn_str:     Override the auto-built connection string.
            pool_size:    Number of connections (default: DB2_POOL_SIZE or 5).
            pool_timeout: Seconds to wait for a free slot (default: DB2_POOL_TIMEOUT or 30).
        """
        self._conn_str = conn_str or _build_conn_str()
        self._size = min(
            int(os.environ.get("DB2_POOL_SIZE", pool_size or _DEFAULT_POOL_SIZE)),
            20,
        )
        self._timeout = int(
            os.environ.get("DB2_POOL_TIMEOUT", pool_timeout or _DEFAULT_POOL_TIMEOUT)
        )
        self._pool: queue.Queue[ibm_db.IBM_DBConnection] = queue.Queue(maxsize=self._size)
        self._closed = False

        logger.debug("Opening %d Db2 connection(s)…", self._size)
        for _ in range(self._size):
            raw = _open_raw_connection(self._conn_str)
            self._pool.put_nowait(raw)
        logger.info(
            "ConnectionPool ready: %d connection(s) to %s",
            self._size,
            self._conn_str.split(";")[1] if ";" in self._conn_str else "(custom)",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def get_connection(self) -> Generator[ibm_db_dbi.Connection, None, None]:
        """Context manager that yields a DB-API 2.0 connection.

        Checks out a raw ``ibm_db`` handle from the pool, wraps it in an
        ``ibm_db_dbi.Connection``, yields to the caller, then returns the
        raw handle to the pool regardless of whether an exception occurred.

        Raises:
            ConnectionPoolExhausted: if no connection is available within
                ``pool_timeout`` seconds.
        """
        if self._closed:
            raise ConnectionError("ConnectionPool has been closed.")

        try:
            raw = self._pool.get(timeout=self._timeout)
        except queue.Empty as exc:
            raise ConnectionPoolExhausted(
                f"No free Db2 connection available after {self._timeout}s. "
                f"Pool size is {self._size}."
            ) from exc

        # Re-wrap in ibm_db_dbi each time — it's a lightweight proxy with no
        # per-instance teardown cost, so discarding and re-creating is fine.
        dbi_conn = ibm_db_dbi.Connection(raw)
        try:
            yield dbi_conn
        finally:
            # Roll back any uncommitted transaction so the connection is
            # clean when returned to the pool.
            with contextlib.suppress(Exception):
                dbi_conn.rollback()
            self._pool.put_nowait(raw)

    def close(self) -> None:
        """Close all pooled connections and mark the pool as unusable."""
        self._closed = True
        while not self._pool.empty():
            with contextlib.suppress(queue.Empty):
                raw = self._pool.get_nowait()
                with contextlib.suppress(Exception):
                    ibm_db.close(raw)
        logger.info("ConnectionPool closed.")

    def __enter__(self) -> ConnectionPool:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
