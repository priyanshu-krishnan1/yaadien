"""
tests/test_tru1_provenance.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for TRU-1: MemoryOrigin enum + origin field on _MemoryBase.

All tests use mocked ibm_db — no live Db2 instance required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_memory_sdk.models import (
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.types import MemoryOrigin

# ---------------------------------------------------------------------------
# 1. MemoryOrigin enum values
# ---------------------------------------------------------------------------

class TestMemoryOriginEnum:
    def test_enum_values_exist(self) -> None:
        assert MemoryOrigin.DIRECT_WRITE.value == "DIRECT_WRITE"
        assert MemoryOrigin.EXTRACTION.value == "EXTRACTION"
        assert MemoryOrigin.CONSOLIDATION.value == "CONSOLIDATION"
        assert MemoryOrigin.RECONCILIATION.value == "RECONCILIATION"
        assert MemoryOrigin.INGEST_RESOLVER.value == "INGEST_RESOLVER"

    def test_enum_is_str_subclass(self) -> None:
        assert isinstance(MemoryOrigin.DIRECT_WRITE, str)

    def test_roundtrip_from_value_string(self) -> None:
        assert MemoryOrigin("CONSOLIDATION") == MemoryOrigin.CONSOLIDATION


# ---------------------------------------------------------------------------
# 2. _MemoryBase.origin field defaults and assignment
# ---------------------------------------------------------------------------

class TestMemoryBaseOriginField:
    def test_default_is_direct_write(self) -> None:
        wm = WorkingMemory(agent_id="a", content="hello")
        assert wm.origin == MemoryOrigin.DIRECT_WRITE

    def test_explicit_consolidation_origin(self) -> None:
        fact = SemanticFact(
            agent_id="a",
            content="user likes python",
            origin=MemoryOrigin.CONSOLIDATION,
        )
        assert fact.origin == MemoryOrigin.CONSOLIDATION

    def test_origin_can_be_overwritten(self) -> None:
        proc = ProceduralMemory(agent_id="a", content="debug python: check traceback")
        assert proc.origin == MemoryOrigin.DIRECT_WRITE
        proc.origin = MemoryOrigin.EXTRACTION
        assert proc.origin == MemoryOrigin.EXTRACTION

    def test_origin_present_on_all_five_types(self) -> None:
        from agent_memory_sdk.models import EntityProfile
        records = [
            WorkingMemory(agent_id="a", content="c"),
            EpisodicMemory(agent_id="a", content="c"),
            SemanticFact(agent_id="a", content="c"),
            EntityProfile(agent_id="a", content="c"),
            ProceduralMemory(agent_id="a", content="c"),
        ]
        for r in records:
            assert hasattr(r, "origin")
            assert r.origin == MemoryOrigin.DIRECT_WRITE


# ---------------------------------------------------------------------------
# 3. MemoryOrigin is exported from the top-level package
# ---------------------------------------------------------------------------

class TestPublicExport:
    def test_memory_origin_importable_from_package(self) -> None:
        from agent_memory_sdk import MemoryOrigin as _MO
        assert _MO is MemoryOrigin


# ---------------------------------------------------------------------------
# 4. Migration file exists with expected content
# ---------------------------------------------------------------------------

class TestMigrationFile:
    def test_migration_0008_exists(self) -> None:
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "agent_memory_sdk", "db", "migrations",
            "0008_provenance.sql",
        )
        assert os.path.isfile(path), "Migration 0008_provenance.sql not found"

    def test_migration_adds_origin_to_all_five_tables(self) -> None:
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "src", "agent_memory_sdk", "db", "migrations",
            "0008_provenance.sql",
        )
        with open(path) as f:
            content = f.read()
        for table in (
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
        ):
            assert f"ALTER TABLE {table}" in content, f"Missing ALTER TABLE {table}"
        assert "origin" in content


# ---------------------------------------------------------------------------
# 5. MemoryStore stamps CONSOLIDATION origin on Consolidator-derived records
# ---------------------------------------------------------------------------

def _make_fake_pool() -> MagicMock:
    """Return a pool mock whose get_connection().__enter__ yields a mock conn."""
    pool = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.rowcount = 1
    conn.cursor.return_value = cursor
    pool.get_connection.return_value.__enter__ = MagicMock(return_value=conn)
    pool.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    return pool


class TestConsolidatorOriginStamping:
    def test_consolidator_derived_records_get_consolidation_origin(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        derived_fact = SemanticFact(agent_id="a", content="user likes coffee")
        assert derived_fact.origin == MemoryOrigin.DIRECT_WRITE  # initial default

        def fake_consolidator(raw_memories):
            return [derived_fact]

        pool = _make_fake_pool()
        store = MemoryStore(pool, consolidator=fake_consolidator)
        scope = MemoryScope(agent_id="a")

        record = WorkingMemory(agent_id="a", content="I love coffee")
        # Intercept the facts.create() call to check origin before it's "written".
        original_create = store.facts.create

        captured_origins: list[MemoryOrigin] = []

        def capturing_create(rec, sc):
            captured_origins.append(rec.origin)
            return original_create(rec, sc)

        store.facts.create = capturing_create

        store.remember(record, scope)
        assert len(captured_origins) == 1
        assert captured_origins[0] == MemoryOrigin.CONSOLIDATION


# ---------------------------------------------------------------------------
# 6. MemoryStore stamps EXTRACTION origin on MemoryExtractor-derived records
# ---------------------------------------------------------------------------

class TestExtractorOriginStamping:
    def test_extractor_derived_records_get_extraction_origin(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        derived_fact = SemanticFact(agent_id="a", content="user likes tea")

        def fake_extractor(messages, scope):
            return [derived_fact]

        pool = _make_fake_pool()
        store = MemoryStore(pool, memory_extractor=fake_extractor)
        scope = MemoryScope(agent_id="a")

        original_create = store.facts.create
        captured_origins: list[MemoryOrigin] = []

        def capturing_create(rec, sc):
            captured_origins.append(rec.origin)
            return original_create(rec, sc)

        store.facts.create = capturing_create

        store.add_messages([{"content": "I love tea"}], scope, extract_memories=True)

        assert any(o == MemoryOrigin.EXTRACTION for o in captured_origins), (
            f"Expected EXTRACTION origin, got: {captured_origins}"
        )


# ---------------------------------------------------------------------------
# 7. DECISIONS.md contains the TRU-1 entry
# ---------------------------------------------------------------------------

class TestDecisionsEntry:
    def test_decisions_md_contains_tru1(self) -> None:
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "project-management", "DECISIONS.md"
        )
        with open(path) as f:
            content = f.read()
        assert "TRU-1" in content
        assert "MemoryOrigin" in content
        assert "0008_provenance.sql" in content
