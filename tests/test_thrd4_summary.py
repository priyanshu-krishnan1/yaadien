"""
tests/test_thrd4_summary.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-4: token-budget-aware thread summary via get_summary()

Coverage:
  1. Basic transcript — 3 messages, correct "{role} (-): {content}" format
  2. except_last=1 — last message is dropped from the transcript
  3. except_last equal to total count — returns empty Summary
  4. token_budget truncates to fit within the budget
  5. token_budget larger than content — no truncation, truncated=False
  6. token_budget=0 — empty summary, truncated=True when messages exist
  7. message_count matches number of lines included
  8. truncated=False when no truncation occurred
  9. Negative except_last raises ValueError
 10. Negative token_budget raises ValueError
 11. Unknown role uses "unknown" fallback
 12. Summary is exported from agent_memory_sdk root

No live Db2 instance required — uses the same fake-pool pattern as
test_thrd1_messages.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

import agent_memory_sdk
from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import Summary

# ---------------------------------------------------------------------------
# Fake DB infrastructure (same pattern as test_thrd1_messages.py)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(agent_id="agent-thrd4", tenant_id="t1")


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _working_row(
    id_: str,
    content: str = "msg",
    created_at: datetime = _NOW,
    deleted_at: Any = None,
    metadata: dict | None = None,
) -> tuple[Any, ...]:
    """Build a fake 16-column DB row for working_memory."""
    return (
        id_, "t1", "agent-thrd4", None, None,
        content, json.dumps(metadata or {}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        created_at, created_at, None, 1, deleted_at,
        None,  # consolidated_at (ENH-4)
    )


class _FakeCursor:
    """Minimal fake cursor: records SQL/params and returns preset rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows: list[tuple[Any, ...]] = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 1
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []
        self.all_sqls.append(sql)
        self.all_params.append(self.last_params)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class _FakePool:
    """Fake pool that always returns the same cursor (configurable rows)."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


def _make_store(rows: list[tuple[Any, ...]] | None = None) -> tuple[MemoryStore, _FakePool]:
    """Return a (store, pool) pair backed by a fake pool."""
    pool = _FakePool(rows)
    store = MemoryStore(pool)
    return store, pool


# ---------------------------------------------------------------------------
# Shared test data — three messages, newest-first (as list_all returns them)
# ---------------------------------------------------------------------------

_T1 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)  # oldest
_T2 = datetime(2026, 9, 1, 10, 1, 0, tzinfo=timezone.utc)
_T3 = datetime(2026, 9, 1, 10, 2, 0, tzinfo=timezone.utc)  # newest

_ROW1 = _working_row("id-1", content="Hello there", created_at=_T1, metadata={"role": "user"})
_ROW2 = _working_row("id-2", content="Hi back",     created_at=_T2, metadata={"role": "assistant"})
_ROW3 = _working_row("id-3", content="How are you", created_at=_T3, metadata={"role": "user"})

# list_all() returns newest-first → [ROW3, ROW2, ROW1]
_THREE_ROWS = [_ROW3, _ROW2, _ROW1]


# ---------------------------------------------------------------------------
# 1. Basic transcript format
# ---------------------------------------------------------------------------

class TestGetSummaryBasicFormat:
    def test_returns_summary_instance(self):
        """get_summary returns a Summary dataclass."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE)
        assert isinstance(result, Summary)

    def test_content_is_string(self):
        """Summary.content is a str."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE)
        assert isinstance(result.content, str)

    def test_three_messages_correct_format(self):
        """Each line is formatted as '{role} (-): {content}' in chronological order."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE)
        lines = result.content.splitlines()
        assert lines[0] == "user (-): Hello there"
        assert lines[1] == "assistant (-): Hi back"
        assert lines[2] == "user (-): How are you"

    def test_message_count_equals_three(self):
        """message_count reflects all 3 messages when no truncation."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE)
        assert result.message_count == 3

    def test_truncated_false_no_budget(self):
        """truncated=False when no token_budget is supplied."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE)
        assert result.truncated is False


# ---------------------------------------------------------------------------
# 2. except_last
# ---------------------------------------------------------------------------

class TestGetSummaryExceptLast:
    def test_except_last_1_drops_newest(self):
        """except_last=1 removes the most-recent message from the transcript."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, except_last=1)
        lines = result.content.splitlines()
        assert len(lines) == 2
        assert lines[0] == "user (-): Hello there"
        assert lines[1] == "assistant (-): Hi back"

    def test_except_last_1_message_count(self):
        """message_count is 2 when except_last=1 with 3 total messages."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, except_last=1)
        assert result.message_count == 2

    def test_except_last_equals_total_returns_empty(self):
        """except_last equal to total message count returns empty Summary."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, except_last=3)
        assert result.content == ""
        assert result.message_count == 0
        assert result.truncated is False

    def test_except_last_greater_than_total_returns_empty(self):
        """except_last > total count also returns empty Summary gracefully."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, except_last=100)
        assert result.content == ""
        assert result.message_count == 0
        assert result.truncated is False

    def test_except_last_zero_includes_all(self):
        """except_last=0 (default) includes all messages."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, except_last=0)
        assert result.message_count == 3


# ---------------------------------------------------------------------------
# 3. token_budget truncation
# ---------------------------------------------------------------------------

class TestGetSummaryTokenBudget:
    def test_token_budget_truncates(self):
        """A tight token_budget stops including messages once budget is exceeded."""
        store, _ = _make_store(rows=_THREE_ROWS)
        # "user (-): Hello there" → 4 tokens
        # "assistant (-): Hi back" → 4 tokens
        # budget=5 → only the first line fits
        result = store.get_summary(_SCOPE, token_budget=5)
        lines = result.content.splitlines()
        assert len(lines) == 1
        assert lines[0] == "user (-): Hello there"
        assert result.truncated is True

    def test_token_budget_larger_than_content_no_truncation(self):
        """A token_budget that fits all content: truncated=False."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, token_budget=10000)
        assert result.message_count == 3
        assert result.truncated is False

    def test_token_budget_zero_empty_if_messages_exist(self):
        """token_budget=0 with existing messages → empty content, truncated=True."""
        store, _ = _make_store(rows=_THREE_ROWS)
        result = store.get_summary(_SCOPE, token_budget=0)
        assert result.content == ""
        assert result.message_count == 0
        assert result.truncated is True

    def test_token_budget_zero_no_messages_not_truncated(self):
        """token_budget=0 with no messages at all → truncated=False."""
        store, _ = _make_store(rows=[])
        result = store.get_summary(_SCOPE, token_budget=0)
        assert result.content == ""
        assert result.message_count == 0
        assert result.truncated is False

    def test_message_count_matches_included_lines(self):
        """message_count always equals the number of newline-separated lines included."""
        store, _ = _make_store(rows=_THREE_ROWS)
        # budget=8 fits lines 1+2 (4+4=8 tokens) exactly, not line 3 (4 tokens → 12 total)
        result = store.get_summary(_SCOPE, token_budget=8)
        assert result.message_count == len(result.content.splitlines())

    def test_truncated_false_exact_budget_fit(self):
        """When all messages fit exactly within the budget, truncated=False."""
        store, _ = _make_store(rows=_THREE_ROWS)
        # Count exact tokens for all 3 lines and pass that as the budget.
        lines_expected = [
            "user (-): Hello there",       # 4 tokens
            "assistant (-): Hi back",       # 4 tokens
            "user (-): How are you",        # 5 tokens
        ]
        exact_budget = sum(len(line.split()) for line in lines_expected)
        result = store.get_summary(_SCOPE, token_budget=exact_budget)
        assert result.message_count == 3
        assert result.truncated is False


# ---------------------------------------------------------------------------
# 4. Validation errors
# ---------------------------------------------------------------------------

class TestGetSummaryValidation:
    def test_negative_except_last_raises(self):
        """except_last < 0 raises ValueError."""
        store, _ = _make_store()
        with pytest.raises(ValueError, match="except_last"):
            store.get_summary(_SCOPE, except_last=-1)

    def test_negative_token_budget_raises(self):
        """token_budget < 0 raises ValueError."""
        store, _ = _make_store()
        with pytest.raises(ValueError, match="token_budget"):
            store.get_summary(_SCOPE, token_budget=-1)


# ---------------------------------------------------------------------------
# 5. Unknown role fallback
# ---------------------------------------------------------------------------

class TestGetSummaryUnknownRole:
    def test_missing_role_uses_unknown_fallback(self):
        """Messages without a 'role' key in metadata use 'unknown' as the role label."""
        row_no_role = _working_row("id-norole", content="no role here", created_at=_T1)
        store, _ = _make_store(rows=[row_no_role])
        result = store.get_summary(_SCOPE)
        assert result.content == "unknown (-): no role here"

    def test_empty_store_returns_empty_summary(self):
        """With no messages get_summary returns empty content, count 0, truncated False."""
        store, _ = _make_store(rows=[])
        result = store.get_summary(_SCOPE)
        assert result.content == ""
        assert result.message_count == 0
        assert result.truncated is False


# ---------------------------------------------------------------------------
# 6. Export check — Summary is importable from the package root
# ---------------------------------------------------------------------------

class TestSummaryExport:
    def test_summary_exported_from_root(self):
        """Summary is accessible directly from agent_memory_sdk."""
        assert hasattr(agent_memory_sdk, "Summary")
        assert agent_memory_sdk.Summary is Summary

    def test_summary_in_all(self):
        """'Summary' appears in agent_memory_sdk.__all__."""
        assert "Summary" in agent_memory_sdk.__all__
