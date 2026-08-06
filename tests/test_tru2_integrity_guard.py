"""
tests/test_tru2_integrity_guard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for TRU-2: IntegrityGuard protocol for ProceduralMemory writes.

All tests use mocked ibm_db — no live Db2 instance required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_memory_sdk.exceptions import IntegrityRejectionError
from agent_memory_sdk.models import (
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.types import (
    IntegrityDecision,
    IntegrityVerdict,
    MemoryOrigin,
    NoOpIntegrityGuard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_pool() -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.rowcount = 1
    conn.cursor.return_value = cursor
    pool.get_connection.return_value.__enter__ = MagicMock(return_value=conn)
    pool.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# 1. IntegrityDecision enum
# ---------------------------------------------------------------------------

class TestIntegrityDecisionEnum:
    def test_values(self) -> None:
        assert IntegrityDecision.ACCEPT.value == "ACCEPT"
        assert IntegrityDecision.QUARANTINE.value == "QUARANTINE"
        assert IntegrityDecision.REJECT.value == "REJECT"


# ---------------------------------------------------------------------------
# 2. IntegrityVerdict dataclass
# ---------------------------------------------------------------------------

class TestIntegrityVerdict:
    def test_default_reason(self) -> None:
        v = IntegrityVerdict(decision=IntegrityDecision.ACCEPT)
        assert v.reason == ""

    def test_reason_set(self) -> None:
        v = IntegrityVerdict(decision=IntegrityDecision.REJECT, reason="suspicious")
        assert v.reason == "suspicious"


# ---------------------------------------------------------------------------
# 3. NoOpIntegrityGuard always ACCEPTs
# ---------------------------------------------------------------------------

class TestNoOpIntegrityGuard:
    def test_always_accept(self) -> None:
        guard = NoOpIntegrityGuard()
        candidate = ProceduralMemory(agent_id="a", content="c")
        verdict = guard(candidate, MemoryOrigin.DIRECT_WRITE, [])
        assert verdict.decision == IntegrityDecision.ACCEPT


# ---------------------------------------------------------------------------
# 4. MemoryStore with ACCEPT guard — normal write proceeds
# ---------------------------------------------------------------------------

class TestAcceptGuard:
    def test_accept_guard_write_proceeds(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        call_count = [0]

        def accepting_guard(candidate, origin, context):
            call_count[0] += 1
            return IntegrityVerdict(decision=IntegrityDecision.ACCEPT)

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=accepting_guard)
        scope = MemoryScope(agent_id="a")
        record = ProceduralMemory(agent_id="a", content="debug python")

        result = store.remember(record, scope)
        assert call_count[0] == 1
        assert result.quarantined is False


# ---------------------------------------------------------------------------
# 5. MemoryStore with QUARANTINE guard — write proceeds, quarantined=True
# ---------------------------------------------------------------------------

class TestQuarantineGuard:
    def test_quarantine_sets_flag_and_proceeds(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        def quarantine_guard(candidate, origin, context):
            return IntegrityVerdict(
                decision=IntegrityDecision.QUARANTINE,
                reason="suspicious overlap",
            )

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=quarantine_guard)
        scope = MemoryScope(agent_id="a")
        record = ProceduralMemory(agent_id="a", content="debug python")

        result = store.remember(record, scope)
        # Write should have proceeded (DB mock cursor was used)
        assert result.quarantined is True


# ---------------------------------------------------------------------------
# 6. MemoryStore with REJECT guard — IntegrityRejectionError raised
# ---------------------------------------------------------------------------

class TestRejectGuard:
    def test_reject_raises(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        def rejecting_guard(candidate, origin, context):
            return IntegrityVerdict(
                decision=IntegrityDecision.REJECT,
                reason="poisoning detected",
            )

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=rejecting_guard)
        scope = MemoryScope(agent_id="a")
        record = ProceduralMemory(agent_id="a", content="malicious skill")

        with pytest.raises(IntegrityRejectionError) as exc_info:
            store.remember(record, scope)

        assert "poisoning detected" in str(exc_info.value)

    def test_reject_does_not_write(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        def rejecting_guard(candidate, origin, context):
            return IntegrityVerdict(decision=IntegrityDecision.REJECT, reason="bad")

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=rejecting_guard)
        scope = MemoryScope(agent_id="a")

        write_calls = [0]
        original_create = store.procedures.create

        def counting_create(rec, sc):
            write_calls[0] += 1
            return original_create(rec, sc)

        store.procedures.create = counting_create

        with pytest.raises(IntegrityRejectionError):
            store.remember(ProceduralMemory(agent_id="a", content="c"), scope)

        assert write_calls[0] == 0, "Should not have written when REJECT was returned"


# ---------------------------------------------------------------------------
# 7. Guard only fires on ProceduralMemory, not other types
# ---------------------------------------------------------------------------

class TestGuardOnlyForesProceduralMemory:
    def test_guard_does_not_fire_on_working_memory(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        call_count = [0]

        def always_reject(candidate, origin, context):
            call_count[0] += 1
            return IntegrityVerdict(decision=IntegrityDecision.REJECT, reason="x")

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=always_reject)
        scope = MemoryScope(agent_id="a")

        # WorkingMemory write should succeed even with a REJECT guard
        store.remember(WorkingMemory(agent_id="a", content="hello"), scope)
        assert call_count[0] == 0, "Guard should not be invoked for WorkingMemory"

    def test_guard_does_not_fire_on_semantic_fact(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        call_count = [0]

        def always_reject(candidate, origin, context):
            call_count[0] += 1
            return IntegrityVerdict(decision=IntegrityDecision.REJECT, reason="x")

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=always_reject)
        scope = MemoryScope(agent_id="a")

        store.remember(SemanticFact(agent_id="a", content="fact"), scope)
        assert call_count[0] == 0


# ---------------------------------------------------------------------------
# 8. Guard crash = fail-open (write proceeds as ACCEPT)
# ---------------------------------------------------------------------------

class TestGuardCrashFailsOpen:
    def test_guard_exception_fails_open(self) -> None:
        from agent_memory_sdk.store import MemoryStore

        def crashing_guard(candidate, origin, context):
            raise RuntimeError("guard exploded")

        pool = _make_fake_pool()
        store = MemoryStore(pool, integrity_guard=crashing_guard)
        scope = MemoryScope(agent_id="a")
        record = ProceduralMemory(agent_id="a", content="debug python")

        # Should NOT raise — guard crash is fail-open
        result = store.remember(record, scope)
        assert result.quarantined is False


# ---------------------------------------------------------------------------
# 9. IntegrityRejectionError is exported from package
# ---------------------------------------------------------------------------

class TestPublicExport:
    def test_integrity_rejection_error_importable(self) -> None:
        from agent_memory_sdk import IntegrityRejectionError as _IRE
        assert _IRE is IntegrityRejectionError

    def test_integrity_decision_importable(self) -> None:
        from agent_memory_sdk import IntegrityDecision as _ID
        assert _ID is IntegrityDecision

    def test_integrity_verdict_importable(self) -> None:
        from agent_memory_sdk import IntegrityVerdict as _IV
        assert _IV is IntegrityVerdict

    def test_noop_integrity_guard_importable(self) -> None:
        from agent_memory_sdk import NoOpIntegrityGuard as _NIG
        assert _NIG is NoOpIntegrityGuard


# ---------------------------------------------------------------------------
# 10. DECISIONS.md reference example exists
# ---------------------------------------------------------------------------

class TestExampleFile:
    def test_integrity_guard_example_exists(self) -> None:
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "examples", "integrity_guard_example.py",
        )
        assert os.path.isfile(path), "examples/integrity_guard_example.py not found"
