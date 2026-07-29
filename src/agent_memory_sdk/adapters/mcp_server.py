"""
agent_memory_sdk.adapters.mcp_server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
MCP (Model Context Protocol) adapter — requires
``pip install agent-memory-sdk[mcp]``.

Exposes four MCP tools so that any MCP-compatible agent can use the SDK
without a Python import:

``remember``
    Store a memory record of any type.

``recall``
    Semantic (vector) search across a memory type.  The caller supplies
    the ``query_embedding`` as a JSON-encoded list of floats; they are
    responsible for embedding the query text before calling this tool.

``forget``
    Soft-delete a memory record by id.

``list_memories``
    List recent memory records for a scope without semantic search.

Embedding
---------
MCP tools are called with plain JSON parameters.  The ``recall`` tool
accepts a ``query_embedding`` parameter (list of floats as a JSON array)
rather than raw query text because:

1. The MCP server has no built-in embedding model — callers vary widely in
   which embedding provider they use.
2. Passing a 1536-float array inline is verbose but straightforward for
   agents that have already embedded the query; callers that haven't can
   omit the parameter and receive only a ``list_memories`` (recency-based)
   response instead.

Usage — standalone server process
----------------------------------
Run as a stdio MCP server::

    python -m agent_memory_sdk.adapters.mcp_server

Or call :func:`create_server` to get the ``Server`` object and run it
inside your own event loop.

Usage — factory
----------------
::

    from agent_memory_sdk.adapters.mcp_server import create_server
    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk import MemoryStore

    pool = ConnectionPool()
    store = MemoryStore(pool)
    server = create_server(store)

Import guard
------------
``mcp`` is only imported inside :func:`create_server`; the rest of the
SDK remains importable without the ``mcp`` extra installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
    _MemoryBase,
)
from agent_memory_sdk.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

def _require_mcp() -> Any:
    """Import the ``mcp`` package, raising a helpful error if absent."""
    try:
        import mcp
        return mcp
    except ImportError as exc:
        raise ImportError(
            "The MCP adapter requires mcp>=1.0. "
            "Install it with: pip install 'agent-memory-sdk[mcp]'"
        ) from exc


def _TextContent(**kwargs: Any) -> Any:  # pragma: no cover
    """Thin wrapper around ``mcp.types.TextContent`` so tests can patch it.

    Importing ``mcp.types.TextContent`` at the call-site inside each
    ``_tool_*`` function made the symbol unpatchable in unit tests when
    ``mcp`` is not installed.  By routing through this module-level
    function, tests can patch
    ``agent_memory_sdk.adapters.mcp_server._TextContent`` without
    requiring the real ``mcp`` package.
    """
    from mcp.types import TextContent
    return TextContent(**kwargs)


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------

_TYPE_TO_MODEL: dict[str, type[_MemoryBase]] = {
    "working": WorkingMemory,
    "episodic": EpisodicMemory,
    "facts": SemanticFact,
    "semantic_facts": SemanticFact,
    "profiles": EntityProfile,
    "entity_profiles": EntityProfile,
    "procedures": ProceduralMemory,
    "procedural": ProceduralMemory,
}

_VALID_TYPES = sorted(
    {"working", "episodic", "facts", "profiles", "procedures"}
)


def _build_scope(
    agent_id: str,
    user_id: str | None = None,
    thread_id: str | None = None,
    tenant_id: str | None = None,
) -> MemoryScope:
    return MemoryScope(
        agent_id=agent_id,
        user_id=user_id or None,
        thread_id=thread_id or None,
        tenant_id=tenant_id or None,
    )


def _record_to_dict(record: _MemoryBase) -> dict[str, Any]:
    """Serialise a memory model to a plain dict for JSON output."""
    return {
        "id": record.id,
        "memory_type": type(record).__name__,
        "content": record.content,
        "metadata": record.metadata,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "version": record.version,
    }


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server(store: MemoryStore) -> Any:
    """Create and return a configured MCP ``Server`` with four memory tools.

    The server is named ``"agent-memory-sdk"`` and exposes:

    - ``remember``    — store a memory record
    - ``recall``      — semantic vector search
    - ``forget``      — soft-delete by id
    - ``list_memories`` — recency-based list

    Args:
        store: A configured :class:`~agent_memory_sdk.store.MemoryStore`.

    Returns:
        An ``mcp.server.Server`` instance.  Call ``server.run(...)``
        inside an async context to start handling requests.
    """
    _require_mcp()
    from mcp.server import Server
    from mcp.types import (
        TextContent,
        Tool,
    )

    server: Any = Server("agent-memory-sdk")

    # ------------------------------------------------------------------ #
    # Tool: remember                                                       #
    # ------------------------------------------------------------------ #

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="remember",
                description=(
                    "Store a memory record in agent memory. "
                    "Supported memory_type values: "
                    + ", ".join(_VALID_TYPES) + "."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent identifier (required).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Text content to store.",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": _VALID_TYPES,
                            "description": "Type of memory to store.",
                            "default": "working",
                        },
                        "user_id": {
                            "type": "string",
                            "description": "Optional user identifier.",
                        },
                        "thread_id": {
                            "type": "string",
                            "description": "Optional conversation/thread id.",
                        },
                        "tenant_id": {
                            "type": "string",
                            "description": "Optional tenant identifier.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional arbitrary metadata.",
                        },
                    },
                    "required": ["agent_id", "content"],
                },
            ),
            Tool(
                name="recall",
                description=(
                    "Semantic search over agent memory using a pre-computed "
                    "embedding vector.  If query_embedding is omitted the "
                    "most recent records are returned instead (list fallback)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent identifier (required).",
                        },
                        "query_embedding": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": (
                                "Pre-computed embedding vector for the query.  "
                                "Must match the stored vector dimension (default 1536).  "
                                "If omitted, falls back to recency-based listing."
                            ),
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": _VALID_TYPES,
                            "description": "Memory type to search.",
                            "default": "working",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return.",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "user_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                    },
                    "required": ["agent_id"],
                },
            ),
            Tool(
                name="forget",
                description="Soft-delete a memory record by id (tombstone, not hard-delete).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent identifier (required).",
                        },
                        "record_id": {
                            "type": "string",
                            "description": "UUID of the record to forget.",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": _VALID_TYPES,
                            "description": "Memory type that owns the record.",
                            "default": "working",
                        },
                        "user_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                    },
                    "required": ["agent_id", "record_id"],
                },
            ),
            Tool(
                name="list_memories",
                description=(
                    "List recent memory records for a scope (no vector search). "
                    "Returns the most recent records ordered newest-first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent identifier (required).",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": _VALID_TYPES,
                            "description": "Memory type to list.",
                            "default": "working",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max records to return (1-100).",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "user_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                    },
                    "required": ["agent_id"],
                },
            ),
        ]

    # ------------------------------------------------------------------ #
    # Tool dispatcher                                                       #
    # ------------------------------------------------------------------ #

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "remember":
                return await _tool_remember(store, arguments)
            elif name == "recall":
                return await _tool_recall(store, arguments)
            elif name == "forget":
                return await _tool_forget(store, arguments)
            elif name == "list_memories":
                return await _tool_list(store, arguments)
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name!r}")]
        except Exception as exc:
            logger.exception("MCP tool %r raised: %s", name, exc)
            return [TextContent(type="text", text=f"Error: {exc}")]

    return server


# ---------------------------------------------------------------------------
# Individual tool implementations (async, but synchronous core calls)
# ---------------------------------------------------------------------------

async def _tool_remember(
    store: MemoryStore, args: dict[str, Any]
) -> list[Any]:
    agent_id: str = args["agent_id"]
    content: str = args["content"]
    memory_type: str = args.get("memory_type", "working")
    metadata: dict[str, Any] = args.get("metadata") or {}
    scope = _build_scope(
        agent_id=agent_id,
        user_id=args.get("user_id"),
        thread_id=args.get("thread_id"),
        tenant_id=args.get("tenant_id"),
    )

    model_cls = _TYPE_TO_MODEL.get(memory_type)
    if model_cls is None:
        return [_TextContent(
            type="text",
            text=f"Unknown memory_type {memory_type!r}. Valid: {_VALID_TYPES}",
        )]

    record = model_cls(
        agent_id=agent_id,
        content=content,
        metadata=metadata,
    )
    stored = store.remember(record, scope)
    result = {"id": stored.id, "memory_type": memory_type, "content": content}
    return [_TextContent(type="text", text=json.dumps(result))]


async def _tool_recall(
    store: MemoryStore, args: dict[str, Any]
) -> list[Any]:
    agent_id: str = args["agent_id"]
    memory_type: str = args.get("memory_type", "working")
    top_k: int = int(args.get("top_k", 5))
    query_embedding: list[float] | None = args.get("query_embedding")
    scope = _build_scope(
        agent_id=agent_id,
        user_id=args.get("user_id"),
        thread_id=args.get("thread_id"),
        tenant_id=args.get("tenant_id"),
    )

    repo_attr_map: dict[str, str] = {
        "working": "working",
        "episodic": "episodic",
        "facts": "facts",
        "semantic_facts": "facts",
        "profiles": "profiles",
        "entity_profiles": "profiles",
        "procedures": "procedures",
        "procedural": "procedures",
    }
    repo_attr = repo_attr_map.get(memory_type)
    if repo_attr is None:
        return [_TextContent(
            type="text",
            text=f"Unknown memory_type {memory_type!r}. Valid: {_VALID_TYPES}",
        )]
    repo = getattr(store, repo_attr)

    if query_embedding:
        rows = repo.search(
            query_embedding=query_embedding,
            scope=scope,
            top_k=top_k,
        )
    else:
        # Fallback: recency list when no embedding provided
        rows = repo.list_all(scope=scope, limit=top_k)
        rows = list(reversed(rows))  # chronological order

    records = [_record_to_dict(r) for r in rows]
    return [_TextContent(type="text", text=json.dumps(records))]


async def _tool_forget(
    store: MemoryStore, args: dict[str, Any]
) -> list[Any]:
    agent_id: str = args["agent_id"]
    record_id: str = args["record_id"]
    memory_type: str = args.get("memory_type", "working")
    scope = _build_scope(
        agent_id=agent_id,
        user_id=args.get("user_id"),
        thread_id=args.get("thread_id"),
        tenant_id=args.get("tenant_id"),
    )
    try:
        result = store.forget(record_id, memory_type, scope)
        status = "forgotten" if result else "not_found"
    except ValueError as exc:
        return [_TextContent(type="text", text=f"Error: {exc}")]

    return [_TextContent(
        type="text",
        text=json.dumps({"id": record_id, "status": status}),
    )]


async def _tool_list(
    store: MemoryStore, args: dict[str, Any]
) -> list[Any]:
    agent_id: str = args["agent_id"]
    memory_type: str = args.get("memory_type", "working")
    limit: int = min(int(args.get("limit", 20)), 100)
    scope = _build_scope(
        agent_id=agent_id,
        user_id=args.get("user_id"),
        thread_id=args.get("thread_id"),
        tenant_id=args.get("tenant_id"),
    )

    repo_attr_map: dict[str, str] = {
        "working": "working",
        "episodic": "episodic",
        "facts": "facts",
        "semantic_facts": "facts",
        "profiles": "profiles",
        "entity_profiles": "profiles",
        "procedures": "procedures",
        "procedural": "procedures",
    }
    repo_attr = repo_attr_map.get(memory_type)
    if repo_attr is None:
        return [_TextContent(
            type="text",
            text=f"Unknown memory_type {memory_type!r}. Valid: {_VALID_TYPES}",
        )]
    repo = getattr(store, repo_attr)
    rows = repo.list_all(scope=scope, limit=limit)
    records = [_record_to_dict(r) for r in rows]
    return [_TextContent(type="text", text=json.dumps(records))]


# ---------------------------------------------------------------------------
# CLI entry point: run as a standalone stdio MCP server
# ---------------------------------------------------------------------------

def _main() -> None:  # pragma: no cover
    """Entry point for running the MCP server as a stdio process."""
    import asyncio

    _require_mcp()
    import mcp.server.stdio as stdio_server_mod

    from agent_memory_sdk.db.connection import ConnectionPool

    pool = ConnectionPool()
    store = MemoryStore(pool)
    server = create_server(store)

    async def _run() -> None:
        options = server.create_initialization_options()
        async with stdio_server_mod.stdio_server() as (r, w):
            await server.run(r, w, options)

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    _main()
