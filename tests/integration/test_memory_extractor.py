"""
tests/integration/test_memory_extractor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-14: MemoryExtractor automatic extraction on message ingest.

Covers:
- Extracted records land in the correct live table (semantic_facts) when
  ``add_messages(extract_memories=True)`` is called with a real extractor.
- Extraction is skipped when ``add_messages(extract_memories=False)`` is used —
  working-memory rows are still written but the facts table stays empty.
- An extractor that raises does NOT propagate the exception; ``add_messages()``
  succeeds and the working-memory rows are still written.

All tests use a deterministic ``KeywordSemanticExtractor`` (no LLM) that
triggers on messages whose content contains ``"EXTRACT:"`` and emits a
``SemanticFact`` for every matched line.

All tests are gated behind the ``integration`` pytest marker and skipped
automatically when ``DB2_DATABASE`` is not set.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Deterministic test extractor — no LLM
# ---------------------------------------------------------------------------


class KeywordSemanticExtractor:
    """Scan each WorkingMemory message for the trigger word ``EXTRACT:``.

    For any message whose content contains the substring ``"EXTRACT:"``, the
    text that follows the trigger is returned as a :class:`SemanticFact`.
    All other messages produce nothing.  This is deterministic and has no
    external dependencies, making it ideal for integration-testing the
    ``add_messages()`` extraction pipeline.
    """

    def __call__(self, messages: list, scope) -> list:
        from agent_memory_sdk.models import SemanticFact

        derived = []
        for msg in messages:
            marker = "EXTRACT:"
            if marker in msg.content:
                fact_text = msg.content.split(marker, 1)[1].strip()
                derived.append(
                    SemanticFact(
                        agent_id=scope.agent_id,
                        content=fact_text,
                        metadata={"source": "keyword_extractor"},
                    )
                )
        return derived


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMemoryExtractorWithExtraction:
    """Tests for the MemoryExtractor pipeline wired through add_messages()."""

    def test_extracted_records_land_in_facts_table(self, migrated_pool, unique_agent_id):
        """Extracted SemanticFact records must appear in store.facts after add_messages()."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        store = MemoryStore(migrated_pool, memory_extractor=KeywordSemanticExtractor())
        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-1")

        messages = [
            {"role": "user", "content": "EXTRACT: The user likes Python"},
            {"role": "assistant", "content": "I understand"},
        ]

        store.add_messages(messages, scope, extract_memories=True)

        # Both messages must be written to working memory.
        working_rows = store.working.list_all(scope)
        assert len(working_rows) == 2, (
            f"Expected 2 working-memory rows, got {len(working_rows)}"
        )

        # At least one SemanticFact must have been extracted.
        facts = store.facts.list_all(scope)
        assert len(facts) >= 1, (
            f"Expected at least 1 extracted fact, got {len(facts)}"
        )

        # The extracted fact must contain the text after "EXTRACT:".
        contents = [f.content for f in facts]
        assert any("The user likes Python" in c for c in contents), (
            f"Expected 'The user likes Python' in extracted facts, got: {contents}"
        )

        # Every extracted fact must belong to the correct scope (agent isolation).
        for fact in facts:
            assert fact.agent_id == scope.agent_id, (
                f"Extracted fact has wrong agent_id: {fact.agent_id!r} "
                f"(expected {scope.agent_id!r})"
            )

    def test_extract_memories_false_skips_extraction(self, migrated_pool, unique_agent_id):
        """With extract_memories=False the facts table must remain empty."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        store = MemoryStore(migrated_pool, memory_extractor=KeywordSemanticExtractor())
        # Use a distinct scope so this test is fully isolated from test 1.
        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-2")

        messages = [
            {"role": "user", "content": "EXTRACT: The user prefers dark mode"},
            {"role": "assistant", "content": "Got it"},
        ]

        store.add_messages(messages, scope, extract_memories=False)

        # Working-memory rows must still be written even without extraction.
        working_rows = store.working.list_all(scope)
        assert len(working_rows) == 2, (
            f"Expected 2 working-memory rows even when extract_memories=False, "
            f"got {len(working_rows)}"
        )

        # No facts must have been extracted.
        facts = store.facts.list_all(scope)
        assert len(facts) == 0, (
            f"Expected 0 extracted facts when extract_memories=False, got {len(facts)}"
        )

    def test_extractor_error_is_caught_and_does_not_fail_add_messages(
        self, migrated_pool, unique_agent_id
    ):
        """An extractor that raises must not propagate; working memory is still written."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        class ErrorExtractor:
            """Always raises to simulate a broken LLM extractor."""

            def __call__(self, messages: list, scope) -> list:
                raise RuntimeError("extraction failed")

        store = MemoryStore(migrated_pool, memory_extractor=ErrorExtractor())
        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-3")

        messages = [
            {"role": "user", "content": "EXTRACT: This should not raise"},
        ]

        # Must not raise, even though the extractor raises internally.
        store.add_messages(messages, scope)

        # Working-memory rows must still be persisted despite the extractor failure.
        working_rows = store.working.list_all(scope)
        assert len(working_rows) == 1, (
            f"Expected 1 working-memory row to survive extractor failure, "
            f"got {len(working_rows)}"
        )
