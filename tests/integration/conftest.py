"""
tests/integration/conftest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared fixtures for integration tests.

All integration tests are gated behind the ``integration`` pytest marker and
skipped automatically when the ``DB2_DATABASE`` environment variable is not
set.  Set it (and the other ``DB2_*`` variables, or point to a .env file
loaded before running pytest) to run them against a real Db2 instance.

See INTEGRATION_TESTING.md for Docker setup instructions.
"""

from __future__ import annotations

import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Skip guard — applied at collection time via pytestmark in each test module,
# and also enforced here so a bare ``pytest tests/integration/`` still works.
# ---------------------------------------------------------------------------

def _db2_available() -> bool:
    return bool(os.environ.get("DB2_DATABASE"))


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-skip integration tests when Db2 env vars are absent."""
    skip_marker = pytest.mark.skip(
        reason=(
            "Integration tests require a live Db2 instance.  "
            "Set DB2_DATABASE (and other DB2_* vars) to enable.  "
            "See INTEGRATION_TESTING.md."
        )
    )
    if _db2_available():
        return
    for item in items:
        if "integration" in item.nodeid:
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db2_pool():
    """Open a real ConnectionPool for the test session.

    Skips the entire test session if DB2_DATABASE is not set.
    """
    if not _db2_available():
        pytest.skip("DB2_DATABASE not set — skipping integration tests")

    # Load .env if present (handy for local dev)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.db.connection import ConnectionPool

    pool = ConnectionPool(pool_size=2, pool_timeout=15)
    yield pool
    pool.close()


@pytest.fixture(scope="session")
def migrated_pool(db2_pool):
    """Return a pool after running all migrations exactly once per session."""
    from agent_memory_sdk.db.migrate import Migrator

    Migrator(db2_pool).run()
    return db2_pool


@pytest.fixture(scope="session")
def store(migrated_pool):
    """A MemoryStore backed by the migrated test database."""
    from agent_memory_sdk import MemoryStore

    return MemoryStore(migrated_pool)


@pytest.fixture()
def unique_agent_id() -> str:
    """Return a fresh agent_id UUID so each test is fully isolated."""
    return f"test-agent-{uuid.uuid4()}"


@pytest.fixture()
def scope(unique_agent_id):
    """A MemoryScope scoped to a unique agent (fully isolated per test)."""
    from agent_memory_sdk.models import MemoryScope

    return MemoryScope(agent_id=unique_agent_id, user_id="test-user-1")


@pytest.fixture()
def thread_scope(unique_agent_id):
    """A MemoryScope with a thread_id for conversation-scoped tests."""
    from agent_memory_sdk.models import MemoryScope

    return MemoryScope(
        agent_id=unique_agent_id,
        user_id="test-user-1",
        thread_id="thread-abc",
    )


@pytest.fixture()
def vec_dim() -> int:
    """Embedding dimension matching the schema default (1536)."""
    return 1536


@pytest.fixture()
def zero_vec(vec_dim: int) -> list[float]:
    """A zero-vector sentinel for tests that don't need real embeddings."""
    return [0.0] * vec_dim


def make_unit_vec(dim: int, hot_index: int) -> list[float]:
    """Return a unit vector with 1.0 at *hot_index* and 0.0 elsewhere.

    Cosine similarity between two such vectors is 1.0 if hot_index matches,
    0.0 if they differ — a reliable deterministic nearest-neighbour test.
    """
    v = [0.0] * dim
    v[hot_index] = 1.0
    return v
