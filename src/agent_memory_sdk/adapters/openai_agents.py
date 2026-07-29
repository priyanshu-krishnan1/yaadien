"""
agent_memory_sdk.adapters.openai_agents
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
OpenAI Agents SDK adapter — requires
``pip install agent-memory-sdk[openai-agents]``.

Provides :class:`Db2Session` which implements the OpenAI Agents SDK
``Session`` protocol (see
https://openai.github.io/openai-agents-python/ref/memory/session/).

A ``Session`` is responsible for storing and retrieving the conversational
message list for a single agent run.  ``Db2Session`` maps this to:

- **Current turn storage** → ``store.working`` (one row per message).
- **Cross-session history retrieval** → ``store.episodic`` can be searched
  for past summaries from the same scope; this adapter exposes a separate
  :meth:`~Db2Session.recall_episodes` method for that use-case rather than
  mixing episodic records into the live message list (which would confuse
  the LLM).

Session / scope mapping
-----------------------
OpenAI Agents SDK sessions are keyed by a ``session_id`` string that maps
to ``MemoryScope.thread_id``.  The required ``agent_id`` must be supplied
by the caller at construction time and does NOT come from the framework.

Message format
--------------
The OpenAI Agents SDK passes messages as plain ``dict`` objects::

    {"role": "user", "content": "Hello"}
    {"role": "assistant", "content": "Hi there!", "metadata": {...}}

These are stored verbatim as JSON in the ``content`` column so they
round-trip faithfully without any transformation loss.

Import guard
------------
``openai_agents`` is only imported at class instantiation time; the rest
of the SDK remains importable without the extra installed.

Usage example::

    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk import MemoryStore, MemoryScope
    from agent_memory_sdk.adapters.openai_agents import Db2Session

    pool = ConnectionPool()
    store = MemoryStore(pool)

    session = Db2Session(
        store=store,
        agent_id="my-agent",
        session_id="session-xyz",
        user_id="user-42",         # optional
    )

    # Wire into the OpenAI Agents SDK — all Session methods are async:
    from agents import Agent, Runner
    result = await Runner.run(
        Agent(name="MyAgent"),
        input="Hello!",
        session=session,
    )

    # Direct async usage:
    await session.add_items([{"role": "user", "content": "Hello!"}])
    messages = await session.get_items()
    last = await session.pop_item()
    await session.clear_session()
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_memory_sdk.models import (
    EpisodicMemory,
    MemoryScope,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

def _require_openai_agents() -> None:
    """Raise ImportError with an actionable message if openai-agents is absent."""
    try:
        import agents  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The OpenAI Agents SDK adapter requires openai-agents>=0.0.10. "
            "Install it with: pip install 'agent-memory-sdk[openai-agents]'"
        ) from exc


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------

def _msg_to_content(message: dict[str, Any]) -> str:
    """Serialize an OpenAI message dict to a JSON string for storage."""
    return json.dumps(message)


def _content_to_msg(content: str) -> dict[str, Any]:
    """Deserialize a stored JSON string back to an OpenAI message dict."""
    try:
        result: dict[str, Any] = json.loads(content)
        return result
    except (json.JSONDecodeError, ValueError):
        # Fallback: treat the raw string as a user message
        return {"role": "user", "content": content}


# ---------------------------------------------------------------------------
# Db2Session
# ---------------------------------------------------------------------------

class Db2Session:
    """OpenAI Agents SDK ``Session`` protocol backed by IBM Db2 LUW.

    Implements the ``Session`` interface expected by the OpenAI Agents SDK
    (``openai-agents>=0.0.10``).  The instance can be passed directly as
    the ``session=`` argument to ``Runner.run()`` or stored on an ``Agent``.

    Protocol methods (all ``async``):

    - :meth:`add_items` — persist a list of message dicts.
    - :meth:`get_items` — retrieve all (or the most recent *limit*) messages
      in chronological order.
    - :meth:`pop_item` — fetch and tombstone the single most-recent message.
    - :meth:`clear_session` — soft-delete all messages for this scope.

    For cross-session retrieval, :meth:`recall_episodes` searches
    ``store.episodic`` by vector similarity — useful for injecting relevant
    past episode summaries before a new agent run.

    Args:
        store:       A configured :class:`~agent_memory_sdk.store.MemoryStore`.
        agent_id:    The agent identifier (required by ``MemoryScope``).
        session_id:  Maps to ``MemoryScope.thread_id``; isolates each
                     conversation's messages.
        user_id:     Optional user identifier.
        tenant_id:   Optional tenant identifier.
    """

    def __init__(
        self,
        store: MemoryStore,
        agent_id: str,
        session_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        _require_openai_agents()
        self._store = store
        self._scope = MemoryScope(
            agent_id=agent_id,
            thread_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    # ------------------------------------------------------------------ #
    # Internal helper                                                      #
    # ------------------------------------------------------------------ #

    def _persist_message(self, message: dict[str, Any]) -> None:
        """Write a single message dict to ``store.working``.

        Extracted from :meth:`add_items` so the per-item logic lives in
        one place.

        Args:
            message: A dict with at least ``"role"`` and ``"content"``
                     keys (OpenAI Agents SDK convention).
        """
        content = _msg_to_content(message)
        metadata: dict[str, Any] = {
            "role": message.get("role", "unknown"),
        }
        record = WorkingMemory(
            agent_id=self._scope.agent_id,
            content=content,
            metadata=metadata,
        )
        self._store.remember(record, self._scope)

    # ------------------------------------------------------------------ #
    # Session protocol                                                     #
    # ------------------------------------------------------------------ #

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        """Persist a list of message dicts.

        Each item is written as a separate ``WorkingMemory`` row.  The
        underlying store call is synchronous (matching the sync-first
        design used throughout this SDK); the method is ``async`` so the
        signature matches the OpenAI Agents SDK ``Session`` protocol.

        Args:
            items: A list of dicts, each with at least ``"role"`` and
                   ``"content"`` keys (OpenAI Agents SDK convention).
        """
        for item in items:
            self._persist_message(item)

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return non-deleted messages in chronological order.

        ``list_all`` returns newest-first (``ORDER BY created_at DESC``);
        this method reverses the list to restore conversation order.
        When *limit* is provided only the most recent *limit* messages are
        returned (the tail of the chronological list), matching the
        convention used by other ``Session`` backends (e.g.
        ``SQLiteSession``).

        Args:
            limit: Maximum number of messages to return (most-recent).
                   ``None`` returns all messages.

        Returns:
            A list of message dicts (same format as passed to
            :meth:`add_items`).
        """
        rows = self._store.working.list_all(
            scope=self._scope,
            limit=1000,
        )
        # Reverse to chronological order (oldest first)
        rows = list(reversed(rows))
        if limit is not None:
            rows = rows[-limit:]
        messages: list[dict[str, Any]] = []
        for row in rows:
            try:
                messages.append(_content_to_msg(row.content))
            except Exception as exc:
                logger.warning(
                    "Could not deserialise message id=%s: %s", row.id, exc
                )
        return messages

    async def pop_item(self) -> dict[str, Any] | None:
        """Fetch and tombstone the single most-recent message.

        Retrieves the newest non-deleted row for this scope, soft-deletes
        it via :meth:`~agent_memory_sdk.repositories.base.BaseRepository.forget`,
        and returns its deserialized content.  Returns ``None`` when there
        are no messages left in the session.

        Returns:
            The most-recent message dict, or ``None`` if the session is
            empty.
        """
        rows = self._store.working.list_all(scope=self._scope, limit=1)
        if not rows:
            return None
        row = rows[0]
        self._store.working.forget(row.id, self._scope)
        try:
            return _content_to_msg(row.content)
        except Exception as exc:
            logger.warning("Could not deserialise popped message id=%s: %s", row.id, exc)
            return {"role": "user", "content": row.content}

    async def clear_session(self) -> None:
        """Soft-delete all messages for the current session scope.

        Uses :meth:`~agent_memory_sdk.repositories.base.BaseRepository.forget`
        on each row so the data is tombstoned (invisible to reads) but not
        immediately hard-deleted.  Call
        :meth:`~agent_memory_sdk.store.MemoryStore.purge_expired` from a
        maintenance script to reclaim disk space.
        """
        rows = self._store.working.list_all(scope=self._scope, limit=1000)
        for row in rows:
            self._store.working.forget(row.id, self._scope)

    # ------------------------------------------------------------------ #
    # Extended API: cross-session episodic recall                          #
    # ------------------------------------------------------------------ #

    def recall_episodes(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[EpisodicMemory]:
        """Search past episode summaries by vector similarity.

        This is *not* part of the OpenAI Agents SDK ``Session`` protocol —
        it is an extension specific to this adapter.  Call it before
        starting a new agent run to inject relevant past-episode context:

        .. code-block:: python

            episodes = session.recall_episodes(
                query_embedding=embed("What did the user last ask about?"),
                top_k=3,
            )
            context = "\\n".join(ep.content for ep in episodes)
            # Prepend context to the initial agent prompt.

        Args:
            query_embedding: The embedding vector for the search query.
            top_k:           Maximum number of episodes to return.

        Returns:
            A list of :class:`~agent_memory_sdk.models.EpisodicMemory`
            instances ordered by descending similarity.
        """
        # Use agent-level scope only (not thread-scoped) so we search
        # across all sessions for this agent + user combination.
        recall_scope = MemoryScope(
            agent_id=self._scope.agent_id,
            tenant_id=self._scope.tenant_id,
            user_id=self._scope.user_id,
        )
        return self._store.episodic.search(
            query_embedding=query_embedding,
            scope=recall_scope,
            top_k=top_k,
        )

    # ------------------------------------------------------------------ #

    @property
    def scope(self) -> MemoryScope:
        """The ``MemoryScope`` used by this session (read-only)."""
        return self._scope

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Db2Session("
            f"agent_id={self._scope.agent_id!r}, "
            f"session_id={self._scope.thread_id!r})"
        )
