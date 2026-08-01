"""
agent_memory_sdk.adapters
~~~~~~~~~~~~~~~~~~~~~~~~~
Optional framework adapters.  Each submodule requires its own extra
dependency group to be installed; the core SDK is importable without any of
them.

Available adapters
------------------
``agent_memory_sdk.adapters.langchain``
    :class:`~agent_memory_sdk.adapters.langchain.Db2ChatMessageHistory`
    — a LangChain ``BaseChatMessageHistory`` backed by
    ``store.working``, and
    :class:`~agent_memory_sdk.adapters.langchain.Db2MemoryStore`
    — a LangChain ``BaseStore`` backed by ``store.facts`` and
    ``store.profiles``.

    Install: ``pip install agent-memory-sdk[langchain]``

``agent_memory_sdk.adapters.openai_agents``
    :class:`~agent_memory_sdk.adapters.openai_agents.Db2Session`
    — implements the OpenAI Agents SDK ``Session`` protocol backed by
    ``store.working`` (current session) + ``store.episodic`` (history
    retrieval across sessions).

    Install: ``pip install agent-memory-sdk[openai-agents]``

``agent_memory_sdk.adapters.mcp_server``
    :func:`~agent_memory_sdk.adapters.mcp_server.create_server`
    — factory that returns an MCP ``Server`` with four tools:
    ``remember``, ``recall``, ``forget``, and ``list_memories``.

    Install: ``pip install agent-memory-sdk[mcp]``

``agent_memory_sdk.adapters.agent_framework``
    :class:`~agent_memory_sdk.adapters.agent_framework.MemoryStoreContextProvider`
    — Microsoft Agent Framework ``ContextProvider`` (``before_run``/
    ``after_run`` lifecycle hooks) backed by ``store.get_context_card()``
    + ``store.facts`` (retrieval) and ``store.remember()`` (persistence),
    and
    :class:`~agent_memory_sdk.adapters.agent_framework.MemoryStoreHistoryProvider`
    — the specialized ``HistoryProvider`` subclass (``get_messages()``/
    ``save_messages()``) backed by ``store.working``.

    Install: ``pip install agent-memory-sdk[agent-framework]``
"""
