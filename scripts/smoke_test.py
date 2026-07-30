"""
smoke_test.py — minimal packaging smoke test for agent-memory-sdk.

Run against a wheel-installed (non-editable) copy of the package to verify:
  1. The top-level package is importable.
  2. One representative symbol from each of the three module groups
     (models, store, db) is accessible — confirming the package layout
     recorded in [tool.hatch.build.targets.wheel] packages is complete
     and no sub-package was accidentally excluded.

This script is intentionally kept dependency-free (stdlib only) and must
not require a live Db2 instance.  It is called from the package-check CI
job via:

    .smoke-venv/bin/python scripts/smoke_test.py

Exit 0 on success; non-zero (with a descriptive message) on any failure.
"""

import sys


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL {label}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    print("agent-memory-sdk smoke test")
    print("=" * 40)

    # ── 1. Top-level package import ──────────────────────────────────────────
    try:
        import agent_memory_sdk  # noqa: F401
    except ImportError as exc:
        print(f"FAIL: cannot import agent_memory_sdk: {exc}", file=sys.stderr)
        sys.exit(1)

    check("import agent_memory_sdk", True)

    # ── 2. __version__ is exposed ────────────────────────────────────────────
    from agent_memory_sdk import __version__  # noqa: F401

    check(f"__version__ == {__version__!r}", isinstance(__version__, str) and __version__)

    # ── 3. models — MemoryScope and WorkingMemory ────────────────────────────
    # MemoryScope is the core value object used by every public API.
    # WorkingMemory is the simplest concrete memory type (no embedding required).
    from agent_memory_sdk.models import MemoryScope, WorkingMemory

    scope = MemoryScope(agent_id="smoke-agent")
    check("models.MemoryScope(agent_id=...) constructs", scope.agent_id == "smoke-agent")

    wm = WorkingMemory(agent_id="smoke-agent", content="hello smoke")
    check("models.WorkingMemory(agent_id=..., content=...) constructs", wm.content == "hello smoke")

    # ── 4. models — remaining concrete types accessible ─────────────────────
    from agent_memory_sdk.models import (  # noqa: F401
        EntityProfile,
        EpisodicMemory,
        ProceduralMemory,
        SemanticFact,
    )

    check("models.EpisodicMemory importable", True)
    check("models.SemanticFact importable", True)
    check("models.EntityProfile importable", True)
    check("models.ProceduralMemory importable", True)

    # ── 5. store — MemoryStore class accessible ──────────────────────────────
    # We only check that the class is importable and is a class; constructing
    # it requires a live Db2 connection pool which is not available here.
    from agent_memory_sdk.store import MemoryStore

    check("store.MemoryStore importable and is a class", isinstance(MemoryStore, type))

    # ── 6. db — ConnectionPool class accessible ──────────────────────────────
    # Same — import-only, no live connection.
    from agent_memory_sdk.db.connection import ConnectionPool

    check("db.connection.ConnectionPool importable and is a class", isinstance(ConnectionPool, type))

    # ── 7. db — SchemaPolicy enum accessible ────────────────────────────────
    from agent_memory_sdk.db.migrate import SchemaPolicy

    check("db.migrate.SchemaPolicy importable", hasattr(SchemaPolicy, "__members__"))

    # ── 8. types — EmbeddingProvider protocol and DistanceMetric accessible ──
    from agent_memory_sdk.types import DistanceMetric, EmbeddingProvider

    check("types.EmbeddingProvider importable", True)
    check("types.DistanceMetric.COSINE accessible", hasattr(DistanceMetric, "COSINE"))

    # ── 9. top-level re-exports match __all__ ────────────────────────────────
    import agent_memory_sdk as sdk

    missing = [name for name in sdk.__all__ if not hasattr(sdk, name)]
    check(f"all __all__ symbols present (checked {len(sdk.__all__)})", not missing)

    print("=" * 40)
    print("All smoke-test assertions passed.")


if __name__ == "__main__":
    main()
