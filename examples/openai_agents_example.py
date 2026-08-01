"""OpenAI Agents SDK adapter example — Db2Session.

Requires a running Db2 instance (see README.md) and:
    pip install "agent-memory-sdk[openai-agents]"

Run:
    python examples/openai_agents_example.py
"""

from __future__ import annotations

import asyncio

from agent_memory_sdk import MemoryScope, MemoryStore
from agent_memory_sdk.adapters.openai_agents import Db2Session
from agent_memory_sdk.db.connection import ConnectionPool

from _embedding import fake_embedding

pool = ConnectionPool()  # reads DB2_* env vars
store = MemoryStore(pool, embedding_provider=fake_embedding)
scope = MemoryScope(agent_id="oa-demo-agent", thread_id="run-1")
session = Db2Session(store=store, agent_id=scope.agent_id, session_id=scope.thread_id)


async def main() -> None:
    # remember() — add_items() persists each message via store.remember().
    await session.add_items(
        [{"role": "user", "content": "Remember that I prefer concise answers."}]
    )

    # recall() — semantic search directly over the session's working memory.
    results = store.working.search(
        query_embedding=fake_embedding("What does the user prefer?"),
        scope=scope,
        top_k=3,
    )
    for r in results:
        print(f"recalled: {r.content!r}")


asyncio.run(main())
