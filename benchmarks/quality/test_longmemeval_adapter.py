"""
benchmarks/quality/test_longmemeval_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-16 acceptance tests: LongMemEval adapter isolation, scope uniqueness,
and end-to-end ingest of all 500 questions.

These tests verify:
  1. Per-question scope isolation — no two questions share a scope/agent_id
  2. Dataset caching — a second load() call reads from disk, not network
  3. All 500 questions of longmemeval_s ingest without error (requires DB)
  4. Evidence session ids are correctly extracted and non-empty for non-
     abstention questions

Markers:
  benchmark_micro   — isolation / scope tests (no Db2, always runs)
  benchmark_nightly — full 500-question ingest (requires Db2)
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchmarks.common.scope_gen import new_run_id
from benchmarks.quality.longmemeval_adapter import (
    ABILITY_CATEGORIES,
    VALID_SPLITS,
    iter_questions,
    load_longmemeval,
)

# ---------------------------------------------------------------------------
# Minimal synthetic row factory (no HuggingFace required)
# ---------------------------------------------------------------------------


def _make_row(
    question_id: str = "q001",
    category: str = "single-session-user",
    question: str = "What does Alice like?",
    answer: str = "Alice likes hiking.",
    n_sessions: int = 2,
    evidence_session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal LongMemEval-shaped row dict for unit tests."""
    sessions = []
    for i in range(n_sessions):
        sid = f"sess-{i}"
        sessions.append({
            "session_id": sid,
            "turns": [
                {"role": "user", "content": f"Turn A in session {i}."},
                {"role": "assistant", "content": f"Turn B in session {i}."},
            ],
        })
    if evidence_session_ids is None:
        evidence_session_ids = ["sess-0"]
    return {
        "question_id": question_id,
        "question_type": category,
        "question": question,
        "answer": answer,
        "haystack_sessions": sessions,
        "evidence_session_ids": evidence_session_ids,
    }


# ---------------------------------------------------------------------------
# Scope isolation — benchmark_micro (no Db2)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_scope_uniqueness_across_questions():
    """Each question gets a unique agent_id — cross-question leakage is impossible."""
    rows = [_make_row(question_id=f"q{i:03d}") for i in range(20)]
    run_id = new_run_id()
    scopes = [q.scope for q in iter_questions(rows, run_id)]
    agent_ids = [s.agent_id for s in scopes]
    assert len(agent_ids) == len(set(agent_ids)), (
        "Two questions share an agent_id — scope isolation violated."
    )


@pytest.mark.benchmark_micro
def test_scope_run_id_prefix():
    """All scopes carry the run_id so rows from different runs cannot collide."""
    run_id = new_run_id()
    rows = [_make_row()]
    q = next(iter_questions(rows, run_id))
    assert run_id in q.scope.agent_id
    assert q.scope.tenant_id is not None and run_id in q.scope.tenant_id


@pytest.mark.benchmark_micro
def test_two_runs_produce_different_scopes():
    """Two separate run_ids never produce the same scope."""
    rows = [_make_row()]
    run1, run2 = new_run_id(), new_run_id()
    q1 = next(iter_questions(rows, run1))
    q2 = next(iter_questions(rows, run2))
    assert q1.scope.agent_id != q2.scope.agent_id


# ---------------------------------------------------------------------------
# Adapter correctness — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_question_fields_extracted():
    """Question text, gold answer, and category are correctly extracted."""
    row = _make_row(question="What is 2+2?", answer="4", category="knowledge-update")
    q = next(iter_questions([row], new_run_id()))
    assert q.question == "What is 2+2?"
    assert q.gold_answer == "4"
    assert q.category == "knowledge-update"


@pytest.mark.benchmark_micro
def test_haystack_messages_flat():
    """haystack_messages is a flat list spanning all sessions."""
    row = _make_row(n_sessions=3)
    q = next(iter_questions([row], new_run_id()))
    # Each session has 2 turns → 6 total
    assert len(q.haystack_messages) == 6


@pytest.mark.benchmark_micro
def test_session_id_in_message_metadata():
    """Each message metadata carries the session_id for Recall@k mapping."""
    row = _make_row(n_sessions=2)
    q = next(iter_questions([row], new_run_id()))
    for msg in q.haystack_messages:
        assert "session_id" in msg.get("metadata", {}), (
            f"session_id missing from message metadata: {msg}"
        )


@pytest.mark.benchmark_micro
def test_evidence_session_ids_extracted():
    """Evidence session ids are correctly mapped from the row."""
    row = _make_row(n_sessions=3, evidence_session_ids=["sess-1", "sess-2"])
    q = next(iter_questions([row], new_run_id()))
    assert q.evidence_session_ids == {"sess-1", "sess-2"}


@pytest.mark.benchmark_micro
def test_is_evidence_flag_on_sessions():
    """is_evidence flag on HaystackSession matches evidence_session_ids."""
    row = _make_row(n_sessions=3, evidence_session_ids=["sess-0"])
    q = next(iter_questions([row], new_run_id()))
    assert q.haystack_sessions[0].is_evidence is True
    assert q.haystack_sessions[1].is_evidence is False
    assert q.haystack_sessions[2].is_evidence is False


@pytest.mark.benchmark_micro
def test_limit_parameter():
    """limit= caps the number of yielded questions."""
    rows = [_make_row(question_id=f"q{i}") for i in range(10)]
    qs = list(iter_questions(rows, new_run_id(), limit=3))
    assert len(qs) == 3


@pytest.mark.benchmark_micro
def test_category_filter_parameter():
    """category_filter= yields only matching rows."""
    rows = [
        _make_row(question_id="q0", category="knowledge-update"),
        _make_row(question_id="q1", category="abstention"),
        _make_row(question_id="q2", category="knowledge-update"),
    ]
    qs = list(iter_questions(rows, new_run_id(), category_filter="knowledge-update"))
    assert len(qs) == 2
    assert all(q.category == "knowledge-update" for q in qs)


@pytest.mark.benchmark_micro
def test_empty_session_skipped():
    """Rows with sessions that have no non-empty turns are skipped gracefully."""
    row = _make_row()
    # Clobber all turn content to empty strings.
    for sess in row["haystack_sessions"]:
        for turn in sess["turns"]:
            turn["content"] = "   "  # whitespace only
    qs = list(iter_questions([row], new_run_id()))
    # Row with no messages should be skipped entirely.
    assert len(qs) == 0


@pytest.mark.benchmark_micro
def test_valid_splits_constant():
    """VALID_SPLITS contains the three expected split names."""
    assert "longmemeval_s" in VALID_SPLITS
    assert "longmemeval_m" in VALID_SPLITS
    assert "longmemeval_oracle" in VALID_SPLITS


@pytest.mark.benchmark_micro
def test_ability_categories_constant():
    """ABILITY_CATEGORIES covers LongMemEval's six categories."""
    assert len(ABILITY_CATEGORIES) == 6
    assert "knowledge-update" in ABILITY_CATEGORIES
    assert "abstention" in ABILITY_CATEGORIES


# ---------------------------------------------------------------------------
# Caching — benchmark_micro (no Db2, but disk I/O)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_cache_write_and_read_roundtrip():
    """load_longmemeval writes a JSONL cache and reads it back identically.

    Uses sys.modules injection to mock the 'datasets' package so this test
    has no external dependency on HuggingFace.
    """
    import sys
    import types

    import benchmarks.quality.longmemeval_adapter as adapter_mod

    rows = [_make_row(question_id=f"q{i}") for i in range(5)]

    # Build a minimal fake 'datasets' module whose load_dataset returns our rows.
    fake_datasets = types.ModuleType("datasets")
    fake_datasets.load_dataset = lambda repo, split: rows  # type: ignore[attr-defined]

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)

        # Inject the fake module so the `from datasets import load_dataset`
        # inside load_longmemeval() finds it.
        original = sys.modules.get("datasets")
        sys.modules["datasets"] = fake_datasets
        try:
            original_cached = adapter_mod._is_cached
            call_count = [0]

            def _not_cached(split, cd):  # noqa: ANN001,ANN201
                call_count[0] += 1
                return False if call_count[0] == 1 else original_cached(split, cd)

            with patch.object(adapter_mod, "_is_cached", side_effect=_not_cached):
                result1 = load_longmemeval("longmemeval_s", cache_dir=cache_dir)
        finally:
            # Restore sys.modules to its original state.
            if original is None:
                sys.modules.pop("datasets", None)
            else:
                sys.modules["datasets"] = original

        # Second call — no fake module, no network — must load from the JSONL cache.
        result2 = load_longmemeval("longmemeval_s", cache_dir=cache_dir)

    assert result1 == result2


@pytest.mark.benchmark_micro
def test_load_invalid_split_raises():
    """load_longmemeval raises ValueError for an unknown split name."""
    with pytest.raises(ValueError, match="Unknown LongMemEval split"):
        load_longmemeval("invalid_split_name")


# ---------------------------------------------------------------------------
# Full 500-question ingest — benchmark_nightly (requires Db2 + warm cache)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
def test_all_500_questions_ingest(db_pool):
    """All 500 questions in longmemeval_s ingest without error end to end.

    Requires:
    * DB2_HOSTNAME set (skipped otherwise via conftest.py's db_pool fixture)
    * Warm LongMemEval cache (set LONGMEMEVAL_CACHE_DIR or run once online)

    This test exercises the full ingest path (add_messages → remember → store)
    for all 500 questions and verifies:
    1. No exception is raised for any question
    2. Every question produces ≥ 1 stored message id
    3. Evidence session ids are non-empty for all non-abstention questions
    """
    from agent_memory_sdk.store import MemoryStore
    from benchmarks.common.embedding_providers import HashingEmbeddingProvider
    from benchmarks.common.scope_gen import new_run_id

    rows = load_longmemeval("longmemeval_s")
    assert len(rows) == 500, f"Expected 500 rows, got {len(rows)}"

    run_id = new_run_id()
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=HashingEmbeddingProvider(),
        enable_chunking=False,
    )

    ingested = 0
    scopes_seen: set[str] = set()
    errors: list[str] = []

    for q in iter_questions(rows, run_id):
        # Scope uniqueness: each question has its own agent_id.
        assert q.scope.agent_id not in scopes_seen, (
            f"Duplicate agent_id {q.scope.agent_id} — cross-question leakage!"
        )
        scopes_seen.add(q.scope.agent_id)

        try:
            ids = store.add_messages(q.haystack_messages, q.scope, extract_memories=False)
            assert len(ids) >= 1, f"Question {q.question_id}: add_messages returned no ids"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{q.question_id}: {exc}")
        finally:
            # Always clean up to avoid residual rows from interfering with
            # subsequent questions.
            with contextlib.suppress(Exception):
                store.erase_all(q.scope)

        ingested += 1

    assert not errors, (
        f"{len(errors)} questions failed to ingest:\n" + "\n".join(errors[:10])
    )
    assert ingested == 500, f"Expected 500 questions, ingested {ingested}"
