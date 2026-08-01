"""LangChain adapter example — Db2ChatMessageHistory.

Requires a running Db2 instance (see README.md) and:
    pip install "agent-memory-sdk[langchain]"

Run:
    python examples/langchain_example.py
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent_memory_sdk import MemoryScope, MemoryStore
from agent_memory_sdk.adapters.langchain import Db2ChatMessageHistory
from agent_memory_sdk.db.connection import ConnectionPool

from _embedding import fake_embedding

pool = ConnectionPool()  # reads DB2_* env vars
store = MemoryStore(pool, embedding_provider=fake_embedding)
scope = MemoryScope(agent_id="lc-demo-agent", thread_id="session-1")
history = Db2ChatMessageHistory(store=store, scope=scope)

# remember() — add_message() calls store.remember() under the hood,
# persisting one WorkingMemory row per LangChain message.
history.add_message(HumanMessage(content="What's a good Python web framework?"))
history.add_message(AIMessage(content="FastAPI is a great choice for APIs."))

# recall() — semantic search over the same working-memory scope.
results = store.working.search(
    query_embedding=fake_embedding("Python web framework recommendation"),
    scope=scope,
    top_k=2,
)
for r in results:
    print(f"recalled: {r.content!r}")

print(f"full history ({len(history.messages)} turns)")
