"""
tests/integration/test_agent_framework_integration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for the Microsoft Agent Framework adapter against a real
Db2 instance.

Covers:
- MemoryStoreContextProvider: before_run / after_run lifecycle round-trips
- MemoryStoreHistoryProvider: save_messages / get_messages round-trips

Both classes require the ``agent-framework`` extra.  The entire file is
skipped (rather than erroring on collection) when ``agent_framework`` cannot
be imported — see the ``pytest.importorskip`` gate at module scope below.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Gate — skip the entire file if agent_framework is not importable.
#
# Because the real package may not be installed, we inject a minimal fake
# module into sys.modules *before* importing the adapter so the adapter's own
# try/except import guard resolves against our stub and sets
# _AGENT_FRAMEWORK_AVAILABLE = True, allowing instantiation to succeed.
# This mirrors the ``af`` fixture strategy in tests/test_adapters.py
# (TestAgentFrameworkAdapter.af) but applied at module scope here.
# ---------------------------------------------------------------------------


def _install_fake_agent_framework() -> types.ModuleType:
    """Inject a minimal fake ``agent_framework`` module and return it.

    Called once at module import time.  If the real package is already
    present we return it as-is (no stub needed).
    """
    real = sys.modules.get("agent_framework")
    if real is not None:
        return real  # real package installed — nothing to do

    class _FakeContextProvider:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _FakeHistoryProvider(_FakeContextProvider):
        pass

    fake_mod = types.ModuleType("agent_framework")
    fake_mod.ContextProvider = _FakeContextProvider  # type: ignore[attr-defined]
    fake_mod.HistoryProvider = _FakeHistoryProvider  # type: ignore[attr-defined]
    sys.modules["agent_framework"] = fake_mod
    return fake_mod


# Install the stub (or verify the real package) now so that the subsequent
# import of the adapter module succeeds.  If neither works, skip the file.
try:
    _install_fake_agent_framework()
    # Force a fresh import of the adapter against the (possibly just-installed)
    # fake module so _AGENT_FRAMEWORK_AVAILABLE is True.
    _af_adapter_name = "agent_memory_sdk.adapters.agent_framework"
    sys.modules.pop(_af_adapter_name, None)
    import agent_memory_sdk.adapters.agent_framework as _af_mod
    from agent_memory_sdk.adapters.agent_framework import (  # noqa: E402
        MemoryStoreContextProvider,
        MemoryStoreHistoryProvider,
    )

    if not _af_mod._AGENT_FRAMEWORK_AVAILABLE:
        pytest.skip(
            "agent_framework not available (_AGENT_FRAMEWORK_AVAILABLE=False)",
            allow_module_level=True,
        )
except Exception as _exc:  # pragma: no cover
    pytest.skip(
        f"agent_framework adapter could not be imported: {_exc}",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Minimal fake framework objects (agent / session / context / state)
# ---------------------------------------------------------------------------
# The adapter only reads:
#   - agent       — passed through; never inspected inside the adapter
#   - session     — .session_id attribute used as fallback for thread_id
#   - context     — .extend_instructions(source_id, text) called in before_run
#                   .messages optionally read in after_run fallback path
#   - state       — plain dict; keys: thread_id, messages, request, response,
#                   user_id, tenant_id, query_embedding


class _FakeContext:
    """Minimal SessionContext stub that records extend_instructions calls."""

    def __init__(self) -> None:
        self.instructions: list[tuple[str, str]] = []

    def extend_instructions(self, source_id: str, text: str) -> None:
        self.instructions.append((source_id, text))


class _FakeSession:
    """Minimal session stub exposing a .session_id attribute."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


# ---------------------------------------------------------------------------
# TestMemoryStoreContextProviderIntegration
# ---------------------------------------------------------------------------


class TestMemoryStoreContextProviderIntegration:
    """MemoryStoreContextProvider backed by a real Db2 working_memory table."""

    @pytest.fixture()
    def provider(self, store, unique_agent_id):
        """Return a MemoryStoreContextProvider wired to the live store."""
        return MemoryStoreContextProvider(store=store, agent_id=unique_agent_id)

    @pytest.fixture()
    def thread_id(self, unique_agent_id) -> str:
        """A deterministic thread id derived from the unique agent id."""
        return f"thread-{unique_agent_id}"

    def _state(self, thread_id: str, **extra: Any) -> dict[str, Any]:
        return {"thread_id": thread_id, **extra}

    # ------------------------------------------------------------------ #
    # 1. before_run on an empty store must not raise                       #
    # ------------------------------------------------------------------ #

    def test_before_run_on_empty_store_does_not_raise(self, provider, thread_id):
        context = _FakeContext()
        asyncio.run(
            provider.before_run(
                agent=None,
                session=None,
                context=context,
                state=self._state(thread_id),
            )
        )
        # No assertions on instructions — the store is empty so nothing is
        # injected; the call must simply complete without raising.

    # ------------------------------------------------------------------ #
    # 2. after_run persists messages to Db2                                #
    # ------------------------------------------------------------------ #

    def test_after_run_persists_messages(self, store, provider, unique_agent_id, thread_id):
        from agent_memory_sdk.models import MemoryScope

        state = self._state(
            thread_id,
            messages=[
                {"role": "user", "text": "Hello from integration!"},
                {"role": "assistant", "text": "Hi back from Db2!"},
            ],
        )
        asyncio.run(
            provider.after_run(
                agent=None,
                session=None,
                context=_FakeContext(),
                state=state,
            )
        )

        scope = MemoryScope(agent_id=unique_agent_id, thread_id=thread_id)
        rows = store.working.list_all(scope=scope)
        contents = [r.content for r in rows]
        assert any("Hello from integration!" in c for c in contents), (
            "after_run must persist the user message to working_memory"
        )
        assert any("Hi back from Db2!" in c for c in contents), (
            "after_run must persist the assistant message to working_memory"
        )

    # ------------------------------------------------------------------ #
    # 3. before_run after after_run injects the stored turn                #
    # ------------------------------------------------------------------ #

    def test_before_run_retrieves_stored_turn(self, store, provider, unique_agent_id, thread_id):
        """After after_run writes a turn, before_run must surface it via
        context.extend_instructions with source_id 'agent-memory-sdk:working'."""
        state = self._state(
            thread_id,
            messages=[{"role": "user", "text": "remember me?"}],
        )
        asyncio.run(
            provider.after_run(
                agent=None,
                session=None,
                context=_FakeContext(),
                state=state,
            )
        )

        context = _FakeContext()
        asyncio.run(
            provider.before_run(
                agent=None,
                session=None,
                context=context,
                state=self._state(thread_id),
            )
        )

        source_ids = [src for src, _ in context.instructions]
        assert "agent-memory-sdk:working" in source_ids, (
            "before_run must inject stored working turns via extend_instructions"
        )
        injected_text = " ".join(
            text for src, text in context.instructions if src == "agent-memory-sdk:working"
        )
        assert "remember me?" in injected_text, (
            "before_run must include the previously stored message text"
        )

    # ------------------------------------------------------------------ #
    # 4. scope isolation — two different thread ids stay separate          #
    # ------------------------------------------------------------------ #

    def test_scope_isolation_between_sessions(
        self, store, provider, unique_agent_id, thread_id
    ):
        """Messages written under thread_id must not appear when scoped to a
        different thread."""
        from agent_memory_sdk.models import MemoryScope

        other_thread_id = f"{thread_id}-other"

        asyncio.run(
            provider.after_run(
                agent=None,
                session=None,
                context=_FakeContext(),
                state=self._state(
                    thread_id,
                    messages=[{"role": "user", "text": "scoped message"}],
                ),
            )
        )

        other_scope = MemoryScope(agent_id=unique_agent_id, thread_id=other_thread_id)
        other_rows = store.working.list_all(scope=other_scope)
        contents = [r.content for r in other_rows]
        assert not any("scoped message" in c for c in contents), (
            "Messages written under one thread_id must not leak into another scope"
        )

    # ------------------------------------------------------------------ #
    # 5. session fallback — thread_id resolved from session.session_id    #
    # ------------------------------------------------------------------ #

    def test_before_run_resolves_thread_id_from_session_object(
        self, store, provider, unique_agent_id
    ):
        """When state has no thread_id, the adapter falls back to
        session.session_id — the correct scope is still used."""
        from agent_memory_sdk.models import MemoryScope

        session_thread_id = f"session-fallback-{unique_agent_id}"
        session = _FakeSession(session_id=session_thread_id)

        asyncio.run(
            provider.after_run(
                agent=None,
                session=session,
                context=_FakeContext(),
                state={
                    "messages": [{"role": "user", "text": "via session object"}]
                },
            )
        )

        scope = MemoryScope(agent_id=unique_agent_id, thread_id=session_thread_id)
        rows = store.working.list_all(scope=scope)
        contents = [r.content for r in rows]
        assert any("via session object" in c for c in contents), (
            "adapter must fall back to session.session_id for scope resolution"
        )


# ---------------------------------------------------------------------------
# TestMemoryStoreHistoryProviderIntegration
# ---------------------------------------------------------------------------


class TestMemoryStoreHistoryProviderIntegration:
    """MemoryStoreHistoryProvider backed by a real Db2 working_memory table."""

    @pytest.fixture()
    def provider(self, store, unique_agent_id):
        """Return a MemoryStoreHistoryProvider wired to the live store."""
        return MemoryStoreHistoryProvider(store=store, agent_id=unique_agent_id)

    @pytest.fixture()
    def session_id(self, unique_agent_id) -> str:
        """A deterministic session id derived from the unique agent id."""
        return f"history-session-{unique_agent_id}"

    # ------------------------------------------------------------------ #
    # 6. save_messages writes rows to Db2                                  #
    # ------------------------------------------------------------------ #

    def test_save_messages_writes_to_db(
        self, store, provider, unique_agent_id, session_id
    ):
        from agent_memory_sdk.models import MemoryScope

        messages = [
            {"role": "user", "text": "msg one"},
            {"role": "assistant", "text": "msg two"},
            {"role": "user", "text": "msg three"},
        ]
        asyncio.run(provider.save_messages(session_id, messages, state={}))

        scope = MemoryScope(agent_id=unique_agent_id, thread_id=session_id)
        rows = store.working.list_all(scope=scope)
        assert len(rows) >= 3, (
            f"Expected at least 3 rows after save_messages, found {len(rows)}"
        )
        raw_contents = [r.content for r in rows]
        assert any("msg one" in c for c in raw_contents)
        assert any("msg two" in c for c in raw_contents)
        assert any("msg three" in c for c in raw_contents)

    # ------------------------------------------------------------------ #
    # 7. get_messages returns saved messages in chronological order        #
    # ------------------------------------------------------------------ #

    def test_get_messages_returns_saved_messages(self, provider, session_id):
        messages = [
            {"role": "user", "text": "get-test first"},
            {"role": "assistant", "text": "get-test second"},
            {"role": "user", "text": "get-test third"},
        ]
        asyncio.run(provider.save_messages(session_id, messages, state={}))

        result = asyncio.run(provider.get_messages(session_id, state={}))

        texts = [m["text"] for m in result]
        assert "get-test first" in texts, "get_messages must return the first saved message"
        assert "get-test second" in texts, "get_messages must return the second saved message"
        assert "get-test third" in texts, "get_messages must return the third saved message"

    def test_get_messages_chronological_order(self, provider, session_id):
        """get_messages must restore chronological order (oldest first)."""
        messages = [
            {"role": "user", "text": "order-check alpha"},
            {"role": "assistant", "text": "order-check beta"},
        ]
        asyncio.run(provider.save_messages(session_id, messages, state={}))

        result = asyncio.run(provider.get_messages(session_id, state={}))

        idx_alpha = next((i for i, m in enumerate(result) if m["text"] == "order-check alpha"), -1)
        idx_beta = next((i for i, m in enumerate(result) if m["text"] == "order-check beta"), -1)
        assert idx_alpha != -1, "order-check alpha not found in get_messages result"
        assert idx_beta != -1, "order-check beta not found in get_messages result"
        assert idx_alpha < idx_beta, (
            f"Expected alpha before beta (chronological order), "
            f"got alpha={idx_alpha}, beta={idx_beta}"
        )

    # ------------------------------------------------------------------ #
    # 8. get_messages on an empty session returns []                       #
    # ------------------------------------------------------------------ #

    def test_get_messages_returns_empty_on_unknown_session(
        self, provider, unique_agent_id
    ):
        result = asyncio.run(
            provider.get_messages(
                f"no-such-session-{unique_agent_id}", state={}
            )
        )
        assert result == [], (
            "get_messages on an unknown session_id must return an empty list"
        )

    # ------------------------------------------------------------------ #
    # 9. Role information is preserved through the round-trip              #
    # ------------------------------------------------------------------ #

    def test_message_role_preserved(self, provider, session_id):
        messages = [
            {"role": "user", "text": "role-check"},
            {"role": "assistant", "text": "role-check reply"},
        ]
        asyncio.run(provider.save_messages(session_id, messages, state={}))

        result = asyncio.run(provider.get_messages(session_id, state={}))

        roles = [m["role"] for m in result]
        assert "user" in roles, "user role must survive the DB round-trip"
        assert "assistant" in roles, "assistant role must survive the DB round-trip"

    # ------------------------------------------------------------------ #
    # 10. scope isolation via user_id in state                             #
    # ------------------------------------------------------------------ #

    def test_scope_isolation_via_user_id_in_state(
        self, store, provider, unique_agent_id, session_id
    ):
        """Messages scoped to user-A must not appear when querying user-B."""

        asyncio.run(
            provider.save_messages(
                session_id,
                [{"role": "user", "text": "user-A only"}],
                state={"user_id": "user-A"},
            )
        )

        # Query with a different user_id — row must not appear
        result_b = asyncio.run(
            provider.get_messages(
                session_id,
                state={"user_id": "user-B"},
            )
        )
        texts_b = [m["text"] for m in result_b]
        assert "user-A only" not in texts_b, (
            "Messages written for user-A must not be visible when querying for user-B"
        )
