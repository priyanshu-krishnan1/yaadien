"""
tests/integration/test_thread_summary.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-13: token-budget thread summary (get_summary()).

Covers (in class order):
  TestGetSummaryLiveDb2 — THRD-4: get_summary() full transcript, budget
                          truncation, large budget, except_last, empty scope.

All tests are skipped automatically when DB2_DATABASE is not set.
Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestGetSummaryLiveDb2:
    """THRD-4: get_summary() — deterministic token-budget transcript."""

    # ------------------------------------------------------------------
    # Test 1 — basic transcript with no budget
    # ------------------------------------------------------------------

    def test_no_budget_includes_all_messages(self, store, scope) -> None:
        """get_summary(scope) with no token_budget returns all 5 messages."""
        messages = [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi, how can I help you today?"},
            {"role": "user", "content": "I need help with Python"},
            {"role": "assistant", "content": "Sure, what specifically about Python?"},
            {"role": "user", "content": "Tell me about list comprehensions"},
        ]
        store.add_messages(messages, scope)

        summary = store.get_summary(scope)

        assert summary.message_count == 5
        assert summary.truncated is False
        # Each message must appear formatted with its role label.
        for msg in messages:
            expected_line = f"{msg['role']} (-): {msg['content']}"
            assert expected_line in summary.content

    # ------------------------------------------------------------------
    # Test 2 — budget truncation with real data
    # ------------------------------------------------------------------

    def test_budget_truncation_stops_before_exceeding_limit(
        self, store, scope
    ) -> None:
        """get_summary(scope, token_budget=50) truncates when total tokens exceed budget."""
        # Seed 10 messages with content long enough that all 10 together
        # exceed a budget of 50 tokens.
        flat_messages: list[dict] = []
        for i in range(5):
            flat_messages.append({
                "role": "user",
                "content": f"This is user message number {i} in the conversation with extra padding words",
            })
            flat_messages.append({
                "role": "assistant",
                "content": f"Acknowledged message {i} here is my response with more padding words here",
            })
        # 10 messages total.
        total_seeded = len(flat_messages)
        store.add_messages(flat_messages, scope)

        summary = store.get_summary(scope, token_budget=50)

        assert summary.truncated is True
        # Token count must be at or below the budget (the implementation
        # stops *before* adding a line that would push over the limit).
        assert len(summary.content.split()) <= 50
        assert summary.message_count < total_seeded

    def test_budget_truncation_oldest_messages_are_included(
        self, store, scope
    ) -> None:
        """When truncating, the OLDEST messages appear in the summary."""
        flat_messages = []
        for i in range(10):
            flat_messages.append({
                "role": "user",
                "content": f"This is user message number {i} with extra filler words for token length",
            })
        store.add_messages(flat_messages, scope)

        # Budget small enough to exclude the newest messages.
        summary = store.get_summary(scope, token_budget=30)

        assert summary.truncated is True
        # The very first (oldest) message must appear in the transcript.
        first_line = "user (-): This is user message number 0 with extra filler words for token length"
        assert first_line in summary.content
        # The last (newest) message must NOT appear.
        last_line = "user (-): This is user message number 9 with extra filler words for token length"
        assert last_line not in summary.content

    # ------------------------------------------------------------------
    # Test 3 — budget larger than all content returns everything untruncated
    # ------------------------------------------------------------------

    def test_large_budget_returns_all_messages_untruncated(
        self, store, scope
    ) -> None:
        """get_summary(scope, token_budget=10000) returns all 3 messages untruncated."""
        messages = [
            {"role": "user", "content": "Short message one"},
            {"role": "assistant", "content": "Short reply two"},
            {"role": "user", "content": "Short message three"},
        ]
        store.add_messages(messages, scope)

        summary = store.get_summary(scope, token_budget=10000)

        assert summary.truncated is False
        assert summary.message_count == 3

    # ------------------------------------------------------------------
    # Test 4 — except_last parameter drops most-recent messages
    # ------------------------------------------------------------------

    def test_except_last_excludes_most_recent_messages(
        self, store, scope
    ) -> None:
        """get_summary(scope, except_last=2) includes only the first 3 of 5 messages."""
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Second message"},
            {"role": "user", "content": "Third message"},
            {"role": "assistant", "content": "Fourth message"},
            {"role": "user", "content": "Fifth message"},
        ]
        store.add_messages(messages, scope)

        summary = store.get_summary(scope, except_last=2)

        assert summary.message_count == 3
        # The first 3 messages must be present.
        assert "user (-): First message" in summary.content
        assert "assistant (-): Second message" in summary.content
        assert "user (-): Third message" in summary.content
        # The last 2 (dropped) messages must NOT be present.
        assert "assistant (-): Fourth message" not in summary.content
        assert "user (-): Fifth message" not in summary.content

    # ------------------------------------------------------------------
    # Test 5 — empty scope returns empty summary
    # ------------------------------------------------------------------

    def test_empty_scope_returns_empty_summary(self, store, scope) -> None:
        """get_summary on a scope with no messages returns an empty Summary."""
        # No messages seeded — scope is fresh and unique per this test.
        summary = store.get_summary(scope)

        assert summary.message_count == 0
        assert summary.content == ""
        assert summary.truncated is False
