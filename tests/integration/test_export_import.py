"""
tests/integration/test_export_import.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-11: export_scope() / import_scope() round-trip
fidelity (Epic-10).

Covers:
- Full round-trip field fidelity across all five memory types + memory_chunks.
- ``_type`` discriminator present in every exported dict.
- Scope mismatch rejection (ScopeMismatchError raised on agent_id collision).
- Vector embedding round-trip within float32 tolerance (1e-4).
- Streaming Iterator correctness: consuming one record at a time without
  premature cursor close.

Each test class uses a fresh ``unique_agent_id`` / scope pair for full
isolation.  Chunk tests construct a MemoryStore with an explicit
ChunkRepository (and a no-op embedding provider) so that ``self.chunks``
is not None and export_scope() includes memory_chunks rows.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Known discriminator names (mirrors _EXPORT_TYPE_TO_REPO_ATTR + _CHUNKS_TYPE
# in store.py) — used in assertions so a typo in the test is caught quickly.
# ---------------------------------------------------------------------------
_KNOWN_TYPES = {
    "working_memory",
    "episodic_memory",
    "semantic_facts",
    "entity_profiles",
    "procedural_memory",
    "memory_chunks",
}

# Embedding dimension matching the schema default
_DIM = 1536


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(agent_id: str, user_id: str = "test-user-export"):
    """Return a MemoryScope with the given agent_id."""
    from agent_memory_sdk.models import MemoryScope

    return MemoryScope(agent_id=agent_id, user_id=user_id)


def _make_store_with_chunks(migrated_pool):
    """Construct a MemoryStore that has a live ChunkRepository so that
    export_scope() includes memory_chunks rows.

    Uses a no-op embedding provider (returns a fixed zero-vector) so that
    chunking is wired but tests remain deterministic with hand-crafted
    embeddings.
    """
    from agent_memory_sdk import MemoryStore

    def _noop_embed(text: str) -> list[float]:
        return [0.0] * _DIM

    return MemoryStore(migrated_pool, embedding_provider=_noop_embed)


# ---------------------------------------------------------------------------
# Test 1 & 2 — Full round-trip field fidelity + _type discriminator presence
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """Seed one row of every memory type + 2 chunks, export, import, verify."""

    def test_round_trip_all_types(self, migrated_pool, unique_agent_id):
        """All five memory tables + memory_chunks survive export → import with
        correct field values and _type tags."""
        from agent_memory_sdk.models import (
            EntityProfile,
            EpisodicMemory,
            ProceduralMemory,
            SemanticFact,
            WorkingMemory,
        )

        # ------------------------------------------------------------------
        # Build two independent stores that share the migrated pool.
        # chunk_store has a live ChunkRepository so chunks are exported.
        # ------------------------------------------------------------------
        chunk_store = _make_store_with_chunks(migrated_pool)

        source_agent = unique_agent_id
        target_agent = f"{unique_agent_id}-target"
        source_scope = _make_scope(source_agent)
        target_scope = _make_scope(target_agent)

        # ------------------------------------------------------------------
        # 1. Seed one row of each memory type into source_scope
        # ------------------------------------------------------------------
        chunk_store.working.create(
            WorkingMemory(
                agent_id=source_agent,
                user_id=source_scope.user_id,
                content="working-content-round-trip",
                metadata={"role": "user"},
                embedding=make_unit_vec(_DIM, 0),
            ),
            source_scope,
        )

        chunk_store.episodic.create(
            EpisodicMemory(
                agent_id=source_agent,
                user_id=source_scope.user_id,
                content="episodic-content-round-trip",
                metadata={"source": "test"},
                embedding=make_unit_vec(_DIM, 1),
            ),
            source_scope,
        )

        chunk_store.facts.create(
            SemanticFact(
                agent_id=source_agent,
                user_id=source_scope.user_id,
                content="semantic-fact-round-trip",
                confidence=0.85,
                metadata={"tag": "export-test", "priority": 3},
                embedding=make_unit_vec(_DIM, 2),
            ),
            source_scope,
        )

        chunk_store.profiles.create(
            EntityProfile(
                agent_id=source_agent,
                user_id=source_scope.user_id,
                content="entity-profile-round-trip",
                metadata={"entity": "test-entity"},
                embedding=make_unit_vec(_DIM, 3),
            ),
            source_scope,
        )

        chunk_store.procedures.create(
            ProceduralMemory(
                agent_id=source_agent,
                user_id=source_scope.user_id,
                content="procedural-memory-round-trip",
                metadata={"skill": "test-skill"},
                embedding=make_unit_vec(_DIM, 4),
            ),
            source_scope,
        )

        # ------------------------------------------------------------------
        # 2. Seed 2 memory_chunks directly via ChunkRepository
        # ------------------------------------------------------------------
        chunk_repo = chunk_store.chunks
        assert chunk_repo is not None, "chunk_store must have a live ChunkRepository"

        chunk_source_id = str(uuid.uuid4())
        chunk_repo.insert_chunk(
            source_table="semantic_facts",
            source_id=chunk_source_id,
            chunk_index=0,
            chunk_text="chunk-zero-text",
            embedding=make_unit_vec(_DIM, 10),
            scope=source_scope,
        )
        chunk_repo.insert_chunk(
            source_table="semantic_facts",
            source_id=chunk_source_id,
            chunk_index=1,
            chunk_text="chunk-one-text",
            embedding=make_unit_vec(_DIM, 11),
            scope=source_scope,
        )

        # ------------------------------------------------------------------
        # 3. Export and materialise
        # ------------------------------------------------------------------
        records = list(chunk_store.export_scope(source_scope))

        # Should have exactly 5 memory rows + 2 chunk rows = 7
        assert len(records) == 7, (
            f"Expected 7 exported records (5 memory + 2 chunks), got {len(records)}"
        )

        # ------------------------------------------------------------------
        # 4. Confirm every record carries a _type discriminator
        # ------------------------------------------------------------------
        for rec in records:
            assert "_type" in rec, f"Record missing '_type': {rec}"
            assert rec["_type"] in _KNOWN_TYPES, (
                f"Unknown _type {rec['_type']!r}; expected one of {_KNOWN_TYPES}"
            )

        # ------------------------------------------------------------------
        # 5. Import into target_scope
        # ------------------------------------------------------------------
        # Build a fresh MemoryStore for the target; it also needs chunks wired
        # so that memory_chunks records can be inserted on import.
        target_store = _make_store_with_chunks(migrated_pool)
        counts = target_store.import_scope(records, target_scope)

        assert counts["working_memory"] == 1
        assert counts["episodic_memory"] == 1
        assert counts["semantic_facts"] == 1
        assert counts["entity_profiles"] == 1
        assert counts["procedural_memory"] == 1
        assert counts["memory_chunks"] == 2

        # ------------------------------------------------------------------
        # 6. Verify field round-trip fidelity for each type
        # ------------------------------------------------------------------

        # working_memory — content + metadata
        wm_rows = target_store.working.list_all(target_scope)
        assert len(wm_rows) == 1
        assert wm_rows[0].content == "working-content-round-trip"
        assert wm_rows[0].metadata.get("role") == "user"

        # episodic_memory — content
        ep_rows = target_store.episodic.list_all(target_scope)
        assert len(ep_rows) == 1
        assert ep_rows[0].content == "episodic-content-round-trip"

        # semantic_facts — content, confidence, metadata (exact dict match)
        sf_rows = target_store.facts.list_all(target_scope)
        assert len(sf_rows) == 1
        imported_sf = sf_rows[0]
        assert imported_sf.content == "semantic-fact-round-trip"
        assert imported_sf.confidence == 0.85, (
            f"Expected confidence=0.85, got {imported_sf.confidence}"
        )
        assert imported_sf.metadata.get("tag") == "export-test"
        assert imported_sf.metadata.get("priority") == 3

        # entity_profiles — content + metadata
        ep_prof_rows = target_store.profiles.list_all(target_scope)
        assert len(ep_prof_rows) == 1
        assert ep_prof_rows[0].content == "entity-profile-round-trip"
        assert ep_prof_rows[0].metadata.get("entity") == "test-entity"

        # procedural_memory — content + metadata
        proc_rows = target_store.procedures.list_all(target_scope)
        assert len(proc_rows) == 1
        assert proc_rows[0].content == "procedural-memory-round-trip"
        assert proc_rows[0].metadata.get("skill") == "test-skill"

        # Embeddings — within float32 tolerance (1e-4)
        _assert_vec_close(wm_rows[0].embedding, make_unit_vec(_DIM, 0))
        _assert_vec_close(ep_rows[0].embedding, make_unit_vec(_DIM, 1))
        _assert_vec_close(imported_sf.embedding, make_unit_vec(_DIM, 2))
        _assert_vec_close(ep_prof_rows[0].embedding, make_unit_vec(_DIM, 3))
        _assert_vec_close(proc_rows[0].embedding, make_unit_vec(_DIM, 4))

        # memory_chunks — confirm they were imported (count via chunk repo)
        imported_chunks = target_store.chunks.list_all(target_scope)
        assert len(imported_chunks) == 2, (
            f"Expected 2 imported chunks, got {len(imported_chunks)}"
        )
        chunk_texts = {c["chunk_text"] for c in imported_chunks}
        assert "chunk-zero-text" in chunk_texts
        assert "chunk-one-text" in chunk_texts


# ---------------------------------------------------------------------------
# Test 2 — _type discriminator is present in every exported dict (standalone)
# ---------------------------------------------------------------------------


class TestTypeDiscriminator:
    """Every exported record must carry a _type key that names a known table."""

    def test_all_records_have_type_key(self, store, unique_agent_id):
        """Seed rows of each type, export, and verify _type on every dict."""
        from agent_memory_sdk.models import (
            EntityProfile,
            EpisodicMemory,
            ProceduralMemory,
            SemanticFact,
            WorkingMemory,
        )

        agent_id = unique_agent_id
        scope = _make_scope(agent_id)

        store.working.create(
            WorkingMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="disc-working",
            ),
            scope,
        )
        store.episodic.create(
            EpisodicMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="disc-episodic",
            ),
            scope,
        )
        store.facts.create(
            SemanticFact(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="disc-fact",
            ),
            scope,
        )
        store.profiles.create(
            EntityProfile(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="disc-profile",
            ),
            scope,
        )
        store.procedures.create(
            ProceduralMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="disc-procedure",
            ),
            scope,
        )

        records = list(store.export_scope(scope))
        assert len(records) >= 5, (
            f"Expected at least 5 exported records, got {len(records)}"
        )

        for rec in records:
            assert "_type" in rec, (
                f"Exported record is missing '_type' key: {rec}"
            )
            assert rec["_type"] in _KNOWN_TYPES, (
                f"Exported record has unknown _type={rec['_type']!r}; "
                f"expected one of {_KNOWN_TYPES}"
            )


# ---------------------------------------------------------------------------
# Test 3 — Scope mismatch rejection
# ---------------------------------------------------------------------------


class TestScopeMismatchRejection:
    """import_scope() must raise ScopeMismatchError when a record's agent_id
    does not match the target scope."""

    def test_wrong_agent_id_raises_scope_mismatch(self, store, unique_agent_id):
        """Modify one record's agent_id to 'wrong-agent' and assert rejection."""
        from agent_memory_sdk import ScopeMismatchError
        from agent_memory_sdk.models import WorkingMemory

        agent_id = unique_agent_id
        source_scope = _make_scope(agent_id)
        target_scope = _make_scope(f"{agent_id}-target")

        # Seed a single row in source_scope
        store.working.create(
            WorkingMemory(
                agent_id=agent_id,
                user_id=source_scope.user_id,
                content="mismatch-test-content",
            ),
            source_scope,
        )

        # Export, then corrupt one record's agent_id
        records = list(store.export_scope(source_scope))
        assert len(records) >= 1

        modified_records = []
        for i, rec in enumerate(records):
            r = dict(rec)
            if i == 0:
                # Corrupt the first record's agent_id so it no longer matches
                # the target_scope the caller intends to import into.
                r["agent_id"] = "wrong-agent"
            modified_records.append(r)

        with pytest.raises(ScopeMismatchError):
            store.import_scope(modified_records, target_scope)


# ---------------------------------------------------------------------------
# Test 4 — Vector round-trip tolerance
# ---------------------------------------------------------------------------


class TestVectorRoundTrip:
    """Embedding values must survive the export → import cycle within 1e-4."""

    def test_embedding_within_float32_tolerance(self, store, unique_agent_id):
        """Seed a SemanticFact with make_unit_vec(1536, 10), export + import,
        read back via SDK, and check every element is within 1e-4."""
        from agent_memory_sdk.models import SemanticFact

        original_vec = make_unit_vec(_DIM, 10)

        agent_id = unique_agent_id
        source_scope = _make_scope(agent_id)
        target_agent = f"{agent_id}-vec-target"
        target_scope = _make_scope(target_agent)

        store.facts.create(
            SemanticFact(
                agent_id=agent_id,
                user_id=source_scope.user_id,
                content="vec-round-trip-fact",
                embedding=original_vec,
                confidence=0.99,
            ),
            source_scope,
        )

        records = list(store.export_scope(source_scope))
        fact_records = [r for r in records if r["_type"] == "semantic_facts"]
        assert len(fact_records) == 1, (
            f"Expected exactly 1 semantic_facts record, got {len(fact_records)}"
        )

        # Confirm the exported embedding field is a list (not None / base64)
        exported_emb = fact_records[0].get("embedding")
        assert isinstance(exported_emb, list), (
            f"Exported 'embedding' must be a list of floats; got {type(exported_emb)}"
        )

        store.import_scope(records, target_scope)

        imported_rows = store.facts.list_all(target_scope)
        assert len(imported_rows) == 1

        imported_emb = imported_rows[0].embedding
        _assert_vec_close(imported_emb, original_vec, tol=1e-4)


# ---------------------------------------------------------------------------
# Test 5 — Streaming Iterator works with live cursor
# ---------------------------------------------------------------------------


class TestStreamingIterator:
    """export_scope() must be consumable one record at a time without error."""

    def test_streaming_one_record_at_a_time(self, store, unique_agent_id):
        """Consume the export iterator in a manual loop and verify the total
        count matches the number of rows seeded — no premature cursor close."""
        from agent_memory_sdk.models import (
            EntityProfile,
            EpisodicMemory,
            ProceduralMemory,
            SemanticFact,
            WorkingMemory,
        )

        agent_id = unique_agent_id
        scope = _make_scope(agent_id)

        # Seed exactly 5 rows (one per memory type) so we have a known total
        store.working.create(
            WorkingMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="stream-working",
            ),
            scope,
        )
        store.episodic.create(
            EpisodicMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="stream-episodic",
            ),
            scope,
        )
        store.facts.create(
            SemanticFact(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="stream-fact",
            ),
            scope,
        )
        store.profiles.create(
            EntityProfile(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="stream-profile",
            ),
            scope,
        )
        store.procedures.create(
            ProceduralMemory(
                agent_id=agent_id,
                user_id=scope.user_id,
                content="stream-procedure",
            ),
            scope,
        )

        expected_count = 5

        # Obtain the iterator WITHOUT materializing it immediately
        iterator = store.export_scope(scope)

        consumed: list[dict] = []
        for record in iterator:
            assert isinstance(record, dict), (
                f"Each yielded record must be a dict; got {type(record)}"
            )
            assert "_type" in record, (
                f"Streamed record missing '_type' key: {record}"
            )
            consumed.append(record)

        assert len(consumed) == expected_count, (
            f"Expected {expected_count} records from streaming iterator, "
            f"got {len(consumed)}"
        )


# ---------------------------------------------------------------------------
# Internal assertion helper
# ---------------------------------------------------------------------------


def _assert_vec_close(
    actual: list[float],
    expected: list[float],
    tol: float = 1e-4,
) -> None:
    """Assert that every element of *actual* is within *tol* of *expected*.

    Fails with a descriptive message showing the first mismatched index.
    """
    assert len(actual) == len(expected), (
        f"Vector length mismatch: got {len(actual)}, expected {len(expected)}"
    )
    for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
        assert abs(a - e) <= tol, (
            f"Embedding element [{i}] out of tolerance: "
            f"got {a}, expected {e}, diff={abs(a - e):.2e} > tol={tol}"
        )
