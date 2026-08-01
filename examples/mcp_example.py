"""MCP adapter example — remember/recall/forget/list_memories as MCP tools.

Requires a running Db2 instance (see README.md) and:
    pip install "agent-memory-sdk[mcp]"

Run:
    python examples/mcp_example.py

This shows the store + server setup. To actually serve the four tools over
stdio to an MCP host, run `python -m agent_memory_sdk.adapters.mcp_server`
(or call `server.run(...)` inside your own async event loop).
"""

from __future__ import annotations

from agent_memory_sdk import MemoryScope, MemoryStore, WorkingMemory
from agent_memory_sdk.adapters.mcp_server import create_server
from agent_memory_sdk.db.connection import ConnectionPool

from _embedding import fake_embedding

pool = ConnectionPool()  # reads DB2_* env vars
store = MemoryStore(pool, embedding_provider=fake_embedding)
server = create_server(store)  # ready for server.run(...) inside an MCP host
scope = MemoryScope(agent_id="mcp-demo-agent")

# remember() — the same call the MCP "remember" tool makes under the hood.
stored = store.remember(
    WorkingMemory(agent_id=scope.agent_id, content="User asked for the Tokyo forecast."),
    scope,
)
print(f"Stored id={stored.id}")

# recall() — the same call the MCP "recall" tool makes when given a
# query_embedding (MCP callers pass the vector as a JSON array of floats).
results = store.working.search(
    query_embedding=fake_embedding("Tokyo weather"),
    scope=scope,
    top_k=3,
)
for r in results:
    print(f"recalled: {r.content!r}")
