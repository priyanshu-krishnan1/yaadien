"""
agent_memory_sdk.adapters.langchain
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LangChain adapter — requires ``pip install agent-memory-sdk[langchain]``.

Provides two classes:

:class:`Db2ChatMessageHistory`
    Implements LangChain's ``BaseChatMessageHistory`` protocol backed by
    ``store.working``.  Wires directly into
    ``RunnableWithMessageHistory`` (and any other LangChain component
    that accepts a ``BaseChatMessageHistory``).

:class:`Db2MemoryStore`
    Implements LangChain's ``BaseStore[str, Any]`` backed by
    ``store.facts`` and ``store.profiles``.  Useful for storing and
    retrieving extracted facts or entity profiles keyed by an arbitrary
    string key.

Import guard
------------
Both classes perform a deferred import of ``langchain_core`` so the
*rest* of the SDK remains importable even when LangChain is not
installed.  Attempting to instantiate either class without
``langchain_core`` installed raises ``ImportError`` with an actionable
message.

Usage example::

    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk import MemoryStore, MemoryScope
    from agent_memory_sdk.adapters.langchain import Db2ChatMessageHistory

    pool = ConnectionPool()
    store = MemoryStore(pool)
    scope = MemoryScope(agent_id="my-agent", user_id="user-1")

    history = Db2ChatMessageHistory(store=store, scope=scope)

    # Use with RunnableWithMessageHistory:
    from langchain_core.runnables.history import RunnableWithMessageHistory

    chain_with_history = RunnableWithMessageHistory(
        runnable=your_llm_chain,
        get_session_history=lambda session_id: Db2ChatMessageHistory(
            store=store,
            scope=MemoryScope(agent_id="my-agent", thread_id=session_id),
        ),
    )
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from agent_memory_sdk.models import (
    EntityProfile,
    MemoryScope,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-import helpers — keep core importable without langchain installed
# ---------------------------------------------------------------------------

def _require_langchain() -> Any:
    """Import langchain_core, raising a helpful error if absent."""
    try:
        import langchain_core  # noqa: F401
        return langchain_core
    except ImportError as exc:
        raise ImportError(
            "The LangChain adapter requires langchain-core>=0.2. "
            "Install it with: pip install 'agent-memory-sdk[langchain]'"
        ) from exc


def _lc_messages_module() -> Any:
    _require_langchain()
    from langchain_core import messages as msg_mod
    return msg_mod


def _lc_base_history() -> Any:
    _require_langchain()
    from langchain_core.chat_history import BaseChatMessageHistory
    return BaseChatMessageHistory


def _lc_base_store() -> Any:
    _require_langchain()
    from langchain_core.stores import BaseStore
    return BaseStore


# ---------------------------------------------------------------------------
# Message serialization helpers
# ---------------------------------------------------------------------------

_ROLE_TO_CLASS: dict[str, str] = {
    "human": "HumanMessage",
    "user": "HumanMessage",
    "ai": "AIMessage",
    "assistant": "AIMessage",
    "system": "SystemMessage",
    "tool": "ToolMessage",
    "function": "FunctionMessage",
    "chat": "ChatMessage",
}

_CLASS_TO_ROLE: dict[str, str] = {
    "HumanMessage": "human",
    "AIMessage": "ai",
    "SystemMessage": "system",
    "ToolMessage": "tool",
    "FunctionMessage": "function",
    "ChatMessage": "chat",
}


def _message_to_metadata(message: Any) -> dict[str, Any]:
    """Serialize a LangChain BaseMessage to a metadata dict for storage."""
    msg_type = type(message).__name__
    meta: dict[str, Any] = {
        "lc_type": msg_type,
        "role": _CLASS_TO_ROLE.get(msg_type, "unknown"),
        "additional_kwargs": message.additional_kwargs or {},
        "response_metadata": getattr(message, "response_metadata", {}),
    }
    # Preserve tool_call_id for ToolMessages
    if hasattr(message, "tool_call_id"):
        meta["tool_call_id"] = message.tool_call_id
    # Preserve tool_calls for AIMessages
    if hasattr(message, "tool_calls") and message.tool_calls:
        meta["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
            }
            for tc in message.tool_calls
        ]
    # LangChain message id
    if message.id:
        meta["lc_id"] = message.id
    return meta


def _content_to_str(content: Any) -> str:
    """Normalize BaseMessage.content to a plain string for storage."""
    if isinstance(content, str):
        return content
    # Structured content (list of dicts — vision, tool results, etc.)
    return json.dumps(content)


def _metadata_to_message(content: str, metadata: dict[str, Any]) -> Any:
    """Reconstruct a LangChain BaseMessage from stored content + metadata."""
    msgs = _lc_messages_module()
    lc_type = metadata.get("lc_type", "")

    # Attempt to parse structured content back
    raw_content: Any = content
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            raw_content = parsed
    except (json.JSONDecodeError, ValueError):
        pass

    additional_kwargs: dict[str, Any] = metadata.get("additional_kwargs", {})
    response_metadata: dict[str, Any] = metadata.get("response_metadata", {})
    lc_id: str | None = metadata.get("lc_id")

    cls_map: dict[str, Any] = {
        "HumanMessage": msgs.HumanMessage,
        "AIMessage": msgs.AIMessage,
        "SystemMessage": msgs.SystemMessage,
        "ToolMessage": msgs.ToolMessage,
        "FunctionMessage": msgs.FunctionMessage,
    }

    cls = cls_map.get(lc_type)

    if cls is None:
        # Fallback: use role if available
        role = metadata.get("role", "unknown")
        role_cls_map: dict[str, Any] = {
            "human": msgs.HumanMessage,
            "user": msgs.HumanMessage,
            "ai": msgs.AIMessage,
            "assistant": msgs.AIMessage,
            "system": msgs.SystemMessage,
            "tool": msgs.ToolMessage,
        }
        cls = role_cls_map.get(role, msgs.HumanMessage)

    try:
        if lc_type == "ToolMessage":
            tool_call_id: str = metadata.get("tool_call_id", "")
            msg = cls(
                content=raw_content,
                tool_call_id=tool_call_id,
                additional_kwargs=additional_kwargs,
                response_metadata=response_metadata,
            )
        elif lc_type == "AIMessage" and metadata.get("tool_calls"):
            msg = cls(
                content=raw_content,
                tool_calls=metadata["tool_calls"],
                additional_kwargs=additional_kwargs,
                response_metadata=response_metadata,
            )
        else:
            msg = cls(
                content=raw_content,
                additional_kwargs=additional_kwargs,
                response_metadata=response_metadata,
            )
        if lc_id:
            # BaseMessage.id is an optional field; set if present
            import contextlib
            with contextlib.suppress(Exception):
                object.__setattr__(msg, "id", lc_id)
    except Exception as exc:
        logger.warning("Failed to reconstruct %s: %s; returning HumanMessage stub", lc_type, exc)
        msg = msgs.HumanMessage(content=content)

    return msg


# ---------------------------------------------------------------------------
# Db2ChatMessageHistory
# ---------------------------------------------------------------------------

class Db2ChatMessageHistory:
    """LangChain ``BaseChatMessageHistory`` backed by ``store.working``.

    Each LangChain message is stored as one :class:`~agent_memory_sdk.models.WorkingMemory`
    row.  Message type (HumanMessage, AIMessage, …) and additional fields
    are round-tripped through the ``metadata`` JSON column.

    This class does **not** inherit from ``BaseChatMessageHistory``; it
    satisfies the interface via duck-typing (``messages`` property,
    ``add_message()``, ``add_messages()``, ``clear()``).  ``langchain_core``
    is only imported at construction time via the ``_require_langchain()``
    guard, so the SDK core remains importable without LangChain installed.

    Args:
        store:   A configured :class:`~agent_memory_sdk.store.MemoryStore`.
        scope:   The :class:`~agent_memory_sdk.models.MemoryScope` that
                 scopes all reads and writes.  At minimum ``agent_id``
                 must be set; ``thread_id`` should be set to the
                 LangChain ``session_id`` so different conversations don't
                 mix.

    Example::

        history = Db2ChatMessageHistory(
            store=store,
            scope=MemoryScope(agent_id="bot-1", thread_id="session-abc"),
        )
        history.add_message(HumanMessage(content="Hello!"))
        print(history.messages)   # [HumanMessage(content='Hello!')]
        history.clear()
    """

    def __init__(self, store: MemoryStore, scope: MemoryScope) -> None:
        # Trigger the import-check eagerly so callers get a clear error
        # at construction time rather than during the first method call.
        _require_langchain()
        self._store = store
        self._scope = scope

    # ------------------------------------------------------------------ #
    # BaseChatMessageHistory interface                                     #
    # ------------------------------------------------------------------ #

    @property
    def messages(self) -> list[Any]:
        """Return all non-deleted messages in chronological order."""
        rows = self._store.working.list_all(
            scope=self._scope,
            limit=1000,
            offset=0,
        )
        # list_all returns newest-first (ORDER BY created_at DESC); reverse
        # to restore chronological order for conversation context.
        rows = list(reversed(rows))
        result = []
        for row in rows:
            try:
                msg = _metadata_to_message(row.content, row.metadata)
                result.append(msg)
            except Exception as exc:
                logger.warning("Could not deserialise message id=%s: %s", row.id, exc)
        return result

    def add_message(self, message: Any) -> None:
        """Append a LangChain ``BaseMessage`` to the history."""
        content = _content_to_str(message.content)
        metadata = _message_to_metadata(message)
        record = WorkingMemory(
            agent_id=self._scope.agent_id,
            content=content,
            metadata=metadata,
        )
        self._store.remember(record, self._scope)

    def add_messages(self, messages: Sequence[Any]) -> None:
        """Append multiple messages (batch helper for LangChain >= 0.2).

        .. note::
            This method is not yet optimised for batching — it calls
            :meth:`add_message` (and therefore checks out a separate DB
            connection) for each message.  It fulfils the LangChain interface
            contract but does not reduce round-trips compared with calling
            :meth:`add_message` in a loop.  A future improvement could
            checkpoint a single connection for the whole batch.
        """
        for message in messages:
            self.add_message(message)

    def clear(self) -> None:
        """Soft-delete all messages in this scope (tombstone them)."""
        rows = self._store.working.list_all(scope=self._scope, limit=1000)
        for row in rows:
            self._store.working.forget(row.id, self._scope)

    # ------------------------------------------------------------------ #
    # Representation                                                       #
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Db2ChatMessageHistory("
            f"agent_id={self._scope.agent_id!r}, "
            f"thread_id={self._scope.thread_id!r})"
        )


# ---------------------------------------------------------------------------
# Db2MemoryStore  (LangChain BaseStore[str, str])
# ---------------------------------------------------------------------------

class Db2MemoryStore:
    """LangChain ``BaseStore[str, str]`` backed by ``store.facts`` and
    ``store.profiles``.

    Key design choices
    ------------------
    - Keys are stored in ``metadata["store_key"]``.
    - Values are stored as ``content`` (plain strings).
    - ``namespace`` is mapped to
      :class:`~agent_memory_sdk.models.MemoryScope` fields:
      ``"facts"`` → ``store.facts``; any other namespace → ``store.profiles``.
    - Vector embeddings are not populated here (zero-vector sentinel);
      this store is intended for exact key lookups, not semantic search.
      For semantic retrieval, use the repositories' ``search()`` method
      directly.

    Args:
        store:     A configured :class:`~agent_memory_sdk.store.MemoryStore`.
        scope:     The :class:`~agent_memory_sdk.models.MemoryScope` for
                   all reads/writes.
        namespace: ``"facts"`` (default) or any other string for profiles.

    Example::

        mem_store = Db2MemoryStore(store=store, scope=scope, namespace="facts")
        mem_store.mset([("user-pref-theme", "dark"), ("user-pref-lang", "Python")])
        values = mem_store.mget(["user-pref-theme"])  # ["dark"]
        mem_store.mdelete(["user-pref-theme"])
    """

    def __init__(
        self,
        store: MemoryStore,
        scope: MemoryScope,
        namespace: str = "facts",
    ) -> None:
        _require_langchain()
        self._store = store
        self._scope = scope
        self._namespace = namespace
        self._repo = store.facts if namespace == "facts" else store.profiles

    def _find_by_key(self, key: str) -> Any | None:
        """Linear scan to find a row with matching store_key in metadata.

        In production this should be indexed; for now it's acceptable given
        that the number of stored keys per scope is expected to be small
        (typically < 1000 per agent).
        """
        rows = self._repo.list_all(scope=self._scope, limit=1000)
        for row in rows:
            if row.metadata.get("store_key") == key:
                return row
        return None

    def mget(self, keys: Sequence[str]) -> list[str | None]:
        """Retrieve values for the given keys.

        Returns a list of the same length as *keys*; positions where the
        key was not found contain ``None``.
        """
        results: list[str | None] = []
        for key in keys:
            row = self._find_by_key(key)
            results.append(row.content if row is not None else None)
        return results

    def mset(self, key_value_pairs: Sequence[tuple[str, str]]) -> None:
        """Set (upsert) key-value pairs.

        If a record with the same key already exists it is updated in-place
        (optimistic concurrency); otherwise a new record is created.
        """
        for key, value in key_value_pairs:
            existing = self._find_by_key(key)
            if existing is not None:
                existing.content = value
                existing.metadata = {**existing.metadata, "store_key": key}
                try:
                    self._repo.update(existing, self._scope)
                except Exception as exc:
                    logger.warning("mset update failed for key=%r: %s; creating new row", key, exc)
                    self._create_row(key, value)
            else:
                self._create_row(key, value)

    def mdelete(self, keys: Sequence[str]) -> None:
        """Soft-delete records matching the given keys."""
        for key in keys:
            row = self._find_by_key(key)
            if row is not None:
                self._repo.forget(row.id, self._scope)

    def yield_keys(self, prefix: str | None = None) -> list[str]:
        """Return all stored keys, optionally filtered by *prefix*."""
        rows = self._repo.list_all(scope=self._scope, limit=1000)
        keys = [r.metadata.get("store_key", "") for r in rows if r.metadata.get("store_key")]
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys

    # ------------------------------------------------------------------

    def _create_row(self, key: str, value: str) -> None:
        """Insert a new row for the given key/value pair."""
        if self._namespace == "facts":
            record: Any = SemanticFact(
                agent_id=self._scope.agent_id,
                content=value,
                metadata={"store_key": key, "namespace": self._namespace},
            )
        else:
            record = EntityProfile(
                agent_id=self._scope.agent_id,
                content=value,
                metadata={"store_key": key, "namespace": self._namespace},
            )
        self._repo.create(record, self._scope)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Db2MemoryStore("
            f"namespace={self._namespace!r}, "
            f"agent_id={self._scope.agent_id!r})"
        )
