"""Plain core-SDK example — no framework adapter, just MemoryStore.

Requires a running Db2 instance (see README.md "Quickstart: Db2 in Docker")
with DB2_* env vars set, and migrations already applied via
`Migrator(pool).run()`.

Run:
    python examples/core_sdk_example.py
"""

from __future__ import annotations

from _embedding import fake_embedding

from agent_memory_sdk import MemoryScope, MemoryStore, SemanticFact
from agent_memory_sdk.db.connection import ConnectionPool

pool = ConnectionPool()  # reads DB2_* env vars
# embedding_provider auto-embeds record.content on remember() when the
# caller hasn't already set record.embedding.
store = MemoryStore(pool, embedding_provider=fake_embedding)
scope = MemoryScope(agent_id="demo-agent", user_id="user-42")

# remember() — write a semantic fact (embedding computed automatically).
fact = SemanticFact(
    agent_id=scope.agent_id,
    user_id=scope.user_id,
    content="The user prefers dark mode and codes in Python.",
    confidence=0.95,
)
stored = store.remember(fact, scope)
print(f"Stored fact id={stored.id}")

# recall() — semantic search over the same scope.
results = store.facts.search(
    query_embedding=fake_embedding("What language does the user code in?"),
    scope=scope,
    top_k=3,
)
for r in results:
    print(f"recalled: {r.content!r} (confidence={r.confidence})")
