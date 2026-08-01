"""
agent_memory_sdk.adapters.agent_framework
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Microsoft Agent Framework adapter — requires
``pip install agent-memory-sdk[agent-framework]``.

Microsoft Agent Framework (GA April 3, 2026, unifying AutoGen + Semantic
Kernel) uses a fundamentally different adapter shape than the three
frameworks this SDK already integrates with (LangChain's
``BaseChatMessageHistory``/``BaseStore``, the OpenAI Agents SDK's
``Session`` protocol, and MCP's tool-call model — see
``adapters/langchain.py``, ``adapters/openai_agents.py``, and
``adapters/mcp_server.py``): a **lifecycle-hook** pattern rather than a
store/session interface.

Provides two classes:

:class:`MemoryStoreContextProvider`
    Subclasses ``agent_framework.ContextProvider``.  ``before_run()`` is
    called before the model is invoked — it retrieves recent working-memory
    turns (via ``store.get_context_card()``) and, when a query embedding is
    available, relevant semantic facts (via ``store.facts.search()``), then
    injects both into the run via ``context.extend_instructions()``.
    ``after_run()`` is called after the model responds — it persists the
    turn's request/response messages via ``store.remember()``.

:class:`MemoryStoreHistoryProvider`
    Subclasses ``agent_framework.HistoryProvider`` (a specialised
    ``ContextProvider``).  ``get_messages()`` maps onto
    ``store.working.list_all()`` and ``save_messages()`` maps onto
    ``store.remember()`` — analogous to ``Db2ChatMessageHistory`` in the
    LangChain adapter, but shaped to the Agent Framework's own
    ``HistoryProvider`` contract instead of LangChain's
    ``BaseChatMessageHistory``.

Session-scoping contract
-------------------------
Microsoft's own documentation calls out explicitly that a single
``ContextProvider``/``HistoryProvider`` instance is shared across every
session an agent runs. Both classes here are constructed once per **agent**
(with an ``agent_id`` fixed at construction time) and read all
session-specific identifiers — a thread/session id, and optionally a
user/tenant id or a pre-computed query embedding — out of the ``state``
dict (or the ``session`` object) passed into each call, **never** from
instance attributes. See ``MemoryStoreContextProvider._scope_for`` /
``MemoryStoreHistoryProvider._scope_for``, which are the single place this
mapping happens.

Assumptions (package not resolvable in this environment)
-----------------------------------------------------------
``agent_framework`` was not installed/importable at the time this adapter
was written (2026-08-02) and its exact class/method signatures could not be
directly inspected from source. This module is implemented strictly against
the documented interface (learn.microsoft.com/agent-framework/agents/
conversations/context-providers, doc revision dated 2026-07-10, as recorded
in ``project-management/DECISIONS.md``'s 2026-08-01 entry and the PIPE-3
board card):

- ``ContextProvider.before_run(*, agent, session, context, state)`` and
  ``after_run(*, agent, session, context, state)`` are ``async`` and
  keyword-only.
- ``context: SessionContext`` exposes ``extend_instructions(source_id: str,
  text: str)``.
- ``HistoryProvider.get_messages(session_id, *, state, **kwargs)`` returns
  ``list[Message]`` and ``save_messages(session_id, messages, *, state,
  **kwargs)`` persists them; both are ``async``.

Beyond that documented surface, this adapter makes the following explicit,
narrower assumptions and degrades gracefully if they don't hold:

1. **Request/response extraction in ``after_run``.** The documented
   signature does not specify exactly where the turn's request/response
   text lives. This adapter looks, in order: ``state["messages"]`` (a list
   of message-like objects/dicts), then ``state["request"]``/
   ``state["response"]``, then ``getattr(context, "messages", None)``. If
   none are present, ``after_run()`` persists nothing rather than raising —
   a framework integration should never crash the agent's response path.
2. **Message shape.** ``agent_framework.Message`` was not resolvable, so
   messages are treated as either plain ``dict`` (``{"role": ..., "text":
   ...}`` or ``{"role": ..., "content": ...}``) or objects exposing
   ``.role`` and ``.text``/``.content`` attributes — the same duck-typing
   strategy already used for LangChain ``BaseMessage`` objects in
   ``adapters/langchain.py``. Whatever shape is found is serialised to JSON
   for storage and reconstructed as a plain ``dict`` on read (round-trip
   fidelity is not guaranteed for arbitrary framework-specific subclasses,
   matching the disclaimer already made for the OpenAI Agents SDK adapter).
3. **Query embedding for fact search.** ``before_run`` only searches
   ``store.facts`` when ``state`` supplies a ``"query_embedding"`` (a
   pre-computed ``list[float]``) — this SDK does not ship an embedding
   model (see ``EmbeddingProvider`` in ``types.py``), so, exactly as the MCP
   adapter's ``recall`` tool does, callers who haven't embedded the current
   turn simply get the recent-turns context card without the relevant-facts
   section, rather than an error.

Import guard
------------
``MemoryStoreContextProvider``/``MemoryStoreHistoryProvider`` must actually
*subclass* ``agent_framework.ContextProvider``/``HistoryProvider`` (per the
PIPE-3 spec), which ordinarily forces the real package to exist at class
*definition* time — unlike the other three adapters, which duck-type their
target interface instead of inheriting from it. To keep this module
importable without ``agent-framework`` installed (matching
``adapters/langchain.py``, ``adapters/openai_agents.py``, and
``adapters/mcp_server.py``, and the
``TestCoreImportableWithoutAdapters.test_adapter_module_importable_*``
tests in ``tests/test_adapters.py``), the base classes are imported at
module scope inside a ``try``/``except ImportError`` and fall back to
``object`` when the package is absent; ``_require_agent_framework()`` is
then called eagerly in ``__init__`` (mirroring every other adapter here),
so instantiation — not import — is where the actionable ``ImportError`` is
raised.

Usage example::

    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk import MemoryStore
    from agent_memory_sdk.adapters.agent_framework import (
        MemoryStoreContextProvider,
        MemoryStoreHistoryProvider,
    )

    pool = ConnectionPool()
    store = MemoryStore(pool)

    context_provider = MemoryStoreContextProvider(store=store, agent_id="my-agent")
    history_provider = MemoryStoreHistoryProvider(store=store, agent_id="my-agent")

    # Wire into an agent_framework Agent — session-specific state (thread id,
    # user id, a pre-computed query embedding) is supplied per-call via the
    # `state` dict, NOT at construction time, because one provider instance
    # is shared across every session:
    from agent_framework import Agent

    agent = Agent(
        name="my-agent",
        context_providers=[context_provider],
        history_provider=history_provider,
    )
    # await agent.run(..., state={"thread_id": "session-abc", "user_id": "user-42"})
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_memory_sdk.models import MemoryScope, WorkingMemory
from agent_memory_sdk.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------
#
# The real base classes are imported at module scope (required so
# MemoryStoreContextProvider/MemoryStoreHistoryProvider can subclass them),
# but wrapped in a try/except so this module — and therefore the rest of
# the SDK, which never imports adapters/ eagerly — stays importable with
# zero agent-framework dependency, matching the other three adapters.

try:
    from agent_framework import ContextProvider as _ContextProviderBase
    from agent_framework import HistoryProvider as _HistoryProviderBase

    _AGENT_FRAMEWORK_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via patch.dict(sys.modules, ...)
    _ContextProviderBase = object  # noqa: F841
    _HistoryProviderBase = object  # noqa: F841
    _AGENT_FRAMEWORK_AVAILABLE = False


def _require_agent_framework() -> None:
    """Raise ImportError with an actionable message if agent-framework is absent."""
    if not _AGENT_FRAMEWORK_AVAILABLE:
        raise ImportError(
            "The Microsoft Agent Framework adapter requires agent-framework. "
            "Install it with: pip install 'agent-memory-sdk[agent-framework]'"
        )


# ---------------------------------------------------------------------------
# Message serialization helpers (duck-typed — see module docstring, item 2)
# ---------------------------------------------------------------------------


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "unknown"))
    return str(getattr(message, "role", "unknown"))


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        text = message.get("text", message.get("content", ""))
        return text if isinstance(text, str) else json.dumps(text)
    text = getattr(message, "text", None)
    if text is None:
        text = getattr(message, "content", "")
    return text if isinstance(text, str) else json.dumps(text)


def _message_to_content(message: Any) -> str:
    """Serialize a framework message (dict or object) to a JSON string."""
    return json.dumps({"role": _message_role(message), "text": _message_text(message)})


def _content_to_message_dict(content: str) -> dict[str, Any]:
    """Deserialize stored JSON back into a plain ``{"role", "text"}`` dict.

    A plain ``dict`` (rather than a real ``agent_framework.Message``) is
    returned deliberately — the real ``Message`` class could not be
    resolved (see module docstring); callers that need the framework's
    concrete type should reconstruct it from this dict's ``role``/``text``
    keys.
    """
    try:
        parsed: dict[str, Any] = json.loads(content)
        parsed.setdefault("role", "unknown")
        parsed.setdefault("text", "")
        return parsed
    except (json.JSONDecodeError, ValueError):
        return {"role": "unknown", "text": content}


def _extract_turn_messages(context: Any, state: dict[str, Any]) -> list[Any]:
    """Best-effort extraction of the turn's messages for ``after_run``.

    Checked in order (see module docstring, assumption 1):
      1. ``state["messages"]`` — a list of message-like objects/dicts.
      2. ``state["request"]`` / ``state["response"]`` — two individual
         message-like values.
      3. ``context.messages`` — an attribute on the ``SessionContext``.

    Returns an empty list (never raises) when nothing is found, so a
    framework integration mismatch degrades to "nothing persisted" rather
    than crashing the agent's response path.
    """
    messages = state.get("messages")
    if messages:
        return list(messages)

    request = state.get("request")
    response = state.get("response")
    if request is not None or response is not None:
        return [m for m in (request, response) if m is not None]

    ctx_messages = getattr(context, "messages", None)
    if ctx_messages:
        return list(ctx_messages)

    return []


# ---------------------------------------------------------------------------
# MemoryStoreContextProvider
# ---------------------------------------------------------------------------


class MemoryStoreContextProvider(_ContextProviderBase):  # type: ignore[misc]
    """``agent_framework.ContextProvider`` backed by :class:`MemoryStore`.

    One instance is constructed per **agent** (fixed ``agent_id``) and
    shared across every session that agent runs — Microsoft's docs call
    this sharing out explicitly. Session-specific identifiers (thread /
    user / tenant id, an optional pre-computed query embedding) are read
    from the ``state`` dict passed into :meth:`before_run` /
    :meth:`after_run`, never stored on ``self``.

    Args:
        store:      A configured :class:`~agent_memory_sdk.store.MemoryStore`.
        agent_id:   The agent identifier (required by ``MemoryScope``);
                    fixed for the lifetime of this provider instance.
        max_turns:  Passed straight through to
                    :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card`
                    (default 20).
        top_k:      Number of semantic facts to retrieve in
                    :meth:`before_run` when ``state["query_embedding"]``
                    is supplied (default 5).

    ``state`` keys read per call:
        ``thread_id``       — maps to ``MemoryScope.thread_id``; falls
                              back to ``session.session_id`` /
                              ``getattr(session, "id", None)`` when absent.
        ``user_id``         — optional, maps to ``MemoryScope.user_id``.
        ``tenant_id``       — optional, maps to ``MemoryScope.tenant_id``.
        ``query_embedding`` — optional ``list[float]``; when present,
                              :meth:`before_run` also injects relevant
                              semantic facts.
        ``messages`` / ``request`` / ``response`` — read by
                              :meth:`after_run`; see
                              :func:`_extract_turn_messages`.
    """

    def __init__(
        self,
        store: MemoryStore,
        agent_id: str,
        max_turns: int = 20,
        top_k: int = 5,
    ) -> None:
        _require_agent_framework()
        super().__init__()
        self._store = store
        self._agent_id = agent_id
        self._max_turns = max_turns
        self._top_k = top_k

    # ------------------------------------------------------------------ #
    # Scope resolution — session state, never instance state              #
    # ------------------------------------------------------------------ #

    def _scope_for(self, session: Any, state: dict[str, Any]) -> MemoryScope:
        thread_id = state.get("thread_id")
        if thread_id is None and session is not None:
            thread_id = getattr(session, "session_id", None) or getattr(session, "id", None)
        return MemoryScope(
            agent_id=self._agent_id,
            thread_id=thread_id,
            user_id=state.get("user_id"),
            tenant_id=state.get("tenant_id"),
        )

    # ------------------------------------------------------------------ #
    # ContextProvider lifecycle hooks                                      #
    # ------------------------------------------------------------------ #

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]
    ) -> None:
        """Inject retrieved memory before the model is invoked.

        Always injects the recent-turns context card (``store
        .get_context_card()``); additionally injects relevant semantic
        facts (``store.facts.search()``) when ``state`` supplies a
        ``"query_embedding"``.
        """
        scope = self._scope_for(session, state)

        card = self._store.get_context_card(scope, max_turns=self._max_turns)
        if card.turns:
            turns_text = "\n".join(
                f"{t.metadata.get('role', 'user')}: {t.content}" for t in card.turns
            )
            context.extend_instructions("agent-memory-sdk:working", turns_text)
        if card.summary:
            context.extend_instructions("agent-memory-sdk:summary", card.summary)

        query_embedding = state.get("query_embedding")
        if query_embedding:
            facts = self._store.facts.search(
                query_embedding=query_embedding,
                scope=scope,
                top_k=self._top_k,
            )
            if facts:
                facts_text = "\n".join(f"- {f.content}" for f in facts)
                context.extend_instructions("agent-memory-sdk:facts", facts_text)

    async def after_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]
    ) -> None:
        """Persist the turn's request/response messages after the run.

        Each extracted message (see :func:`_extract_turn_messages`) is
        written as its own :class:`~agent_memory_sdk.models.WorkingMemory`
        row, mirroring how the OpenAI Agents SDK adapter's
        ``Db2Session.add_items()`` persists one row per message.
        """
        scope = self._scope_for(session, state)
        for message in _extract_turn_messages(context, state):
            record = WorkingMemory(
                agent_id=scope.agent_id,
                content=_message_to_content(message),
                metadata={"role": _message_role(message)},
            )
            self._store.remember(record, scope)

    def __repr__(self) -> str:  # pragma: no cover
        return f"MemoryStoreContextProvider(agent_id={self._agent_id!r})"


# ---------------------------------------------------------------------------
# MemoryStoreHistoryProvider
# ---------------------------------------------------------------------------


class MemoryStoreHistoryProvider(_HistoryProviderBase):  # type: ignore[misc]
    """``agent_framework.HistoryProvider`` backed by :class:`MemoryStore`.

    ``HistoryProvider`` is documented as a specialised ``ContextProvider``
    that manages conversation history directly rather than through
    ``before_run``/``after_run`` instruction injection. Like
    :class:`MemoryStoreContextProvider`, one instance is constructed per
    agent and shared across sessions; ``session_id`` and any other
    session-specific identifiers are passed as call arguments / via
    ``state``, never stored on ``self``.

    Args:
        store:    A configured :class:`~agent_memory_sdk.store.MemoryStore`.
        agent_id: The agent identifier (required by ``MemoryScope``);
                  fixed for the lifetime of this provider instance.
    """

    def __init__(self, store: MemoryStore, agent_id: str) -> None:
        _require_agent_framework()
        super().__init__()
        self._store = store
        self._agent_id = agent_id

    def _scope_for(self, session_id: str | None, state: dict[str, Any]) -> MemoryScope:
        return MemoryScope(
            agent_id=self._agent_id,
            thread_id=session_id,
            user_id=state.get("user_id"),
            tenant_id=state.get("tenant_id"),
        )

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any], **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Return this session's messages in chronological order.

        Maps directly onto
        :meth:`~agent_memory_sdk.repositories.working.WorkingMemoryRepository.list_all`.
        ``list_all`` returns newest-first; the result is reversed here to
        restore chronological order, matching the same convention used by
        ``Db2ChatMessageHistory.messages`` and ``Db2Session.get_items()``.

        Returns a list of plain ``{"role", "text"}`` dicts rather than a
        real ``agent_framework.Message`` — see the module docstring's
        assumption 2 (the concrete ``Message`` class could not be resolved
        in this environment).
        """
        scope = self._scope_for(session_id, state)
        rows = self._store.working.list_all(scope=scope, limit=1000)
        rows = list(reversed(rows))
        messages: list[dict[str, Any]] = []
        for row in rows:
            try:
                messages.append(_content_to_message_dict(row.content))
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Could not deserialise message id=%s: %s", row.id, exc)
        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages: list[Any],
        *,
        state: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Persist *messages* for this session.

        Maps directly onto
        :meth:`~agent_memory_sdk.store.MemoryStore.remember` — one
        :class:`~agent_memory_sdk.models.WorkingMemory` row per message.
        """
        scope = self._scope_for(session_id, state)
        for message in messages:
            record = WorkingMemory(
                agent_id=scope.agent_id,
                content=_message_to_content(message),
                metadata={"role": _message_role(message)},
            )
            self._store.remember(record, scope)

    def __repr__(self) -> str:  # pragma: no cover
        return f"MemoryStoreHistoryProvider(agent_id={self._agent_id!r})"
