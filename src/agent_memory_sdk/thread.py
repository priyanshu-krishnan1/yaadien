"""
thread.py
~~~~~~~~~
Thread — a bound-scope convenience wrapper over MemoryStore.

``Thread`` pre-binds a :class:`~agent_memory_sdk.models.MemoryScope` to a
:class:`~agent_memory_sdk.store.MemoryStore` instance, so all message and
memory operations can be called without repeating the scope on every call.

Usage::

    thread = store.create_thread(thread_id="t-1", agent_id="agent-1", user_id="user-42")
    thread.add_messages([{"content": "Hello!", "metadata": {"role": "user"}}])
    messages = thread.get_messages()
    summary  = thread.get_summary(token_budget=500)
    card     = thread.get_context_card()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_memory_sdk.models import MemoryScope, WorkingMemory
    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.types import ContextCard, SearchResult, Summary


class Thread:
    """A bound-scope convenience wrapper over :class:`~agent_memory_sdk.store.MemoryStore`.

    Created by :meth:`~agent_memory_sdk.store.MemoryStore.create_thread` or
    :meth:`~agent_memory_sdk.store.MemoryStore.get_thread` — do not instantiate
    directly.

    All methods are thin pass-throughs to the underlying ``MemoryStore`` with
    the bound ``scope`` pre-applied.

    Args:
        store: The backing :class:`~agent_memory_sdk.store.MemoryStore`.
        scope: The pre-bound :class:`~agent_memory_sdk.models.MemoryScope`.
               ``scope.thread_id`` identifies this thread.
    """

    def __init__(self, store: MemoryStore, scope: MemoryScope) -> None:
        self._store = store
        self._scope = scope

    @property
    def scope(self) -> MemoryScope:
        """The bound scope for this thread."""
        return self._scope

    # -- message operations (THRD-1) --

    def add_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        extract_memories: bool = True,
    ) -> list[str]:
        """Add messages to this thread.  See :meth:`~agent_memory_sdk.store.MemoryStore.add_messages`."""
        return self._store.add_messages(messages, self._scope, extract_memories=extract_memories)

    def get_messages(self, start: int = 0, end: int | None = None) -> list[WorkingMemory]:
        """Return messages in chronological order.  See :meth:`~agent_memory_sdk.store.MemoryStore.get_messages`."""
        return self._store.get_messages(self._scope, start=start, end=end)

    def delete_message(self, message_id: str) -> int:
        """Soft-delete a message.  See :meth:`~agent_memory_sdk.store.MemoryStore.delete_message`."""
        return self._store.delete_message(message_id, self._scope)

    # -- durable memory operations (THRD-2) --

    def add_memory(
        self,
        content: str,
        *,
        memory_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add a durable memory fact.  See :meth:`~agent_memory_sdk.store.MemoryStore.add_memory`."""
        return self._store.add_memory(content, self._scope, memory_id=memory_id, metadata=metadata)

    def delete_memory(self, memory_id: str) -> int:
        """Delete a durable memory by id.  See :meth:`~agent_memory_sdk.store.MemoryStore.delete_memory`."""
        return self._store.delete_memory(memory_id, self._scope)

    # -- search (THRD-3) --

    def search(
        self,
        query: str,
        record_types: list[str] | None = None,
        max_results: int = 10,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Fan-out search.  See :meth:`~agent_memory_sdk.store.MemoryStore.search`."""
        return self._store.search(
            query,
            self._scope,
            record_types=record_types,
            max_results=max_results,
            metadata_filter=metadata_filter,
        )

    # -- summary (THRD-4) --

    def get_summary(
        self,
        except_last: int = 0,
        token_budget: int | None = None,
    ) -> Summary:
        """Return a deterministic thread transcript.  See :meth:`~agent_memory_sdk.store.MemoryStore.get_summary`."""
        return self._store.get_summary(self._scope, except_last=except_last, token_budget=token_budget)

    # -- context card (ORC-1 / PIPE-4) --

    def get_context_card(
        self,
        max_turns: int = 20,
        query: str | None = None,
        include_long_term: bool = False,
        min_results_by_type: dict[str, int] | None = None,
        long_term_top_k: int = 5,
    ) -> ContextCard:
        """Return a structured context card.  See :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card`."""
        return self._store.get_context_card(
            self._scope,
            max_turns=max_turns,
            query=query,
            include_long_term=include_long_term,
            min_results_by_type=min_results_by_type,
            long_term_top_k=long_term_top_k,
        )

    def __repr__(self) -> str:
        return (
            f"Thread(agent_id={self._scope.agent_id!r}, "
            f"user_id={self._scope.user_id!r}, "
            f"thread_id={self._scope.thread_id!r})"
        )
