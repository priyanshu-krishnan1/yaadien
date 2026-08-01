# Examples

Each script is self-contained, under 50 lines, and shows the same three
steps: store setup, a `remember()`-style write, and a `recall()`-style
semantic search. None require an LLM or an API key — they use the tiny
deterministic pseudo-embedding in [`_embedding.py`](_embedding.py) so they
run against nothing but a Db2 instance. Swap in a real
`EmbeddingProvider` (OpenAI, sentence-transformers, etc.) for actual use.

All examples need a running Db2 with migrations applied — see the
"Quickstart: Db2 in Docker" section of the root [`README.md`](../README.md)
— and must be run from this directory (or with it on `PYTHONPATH`) so the
`_embedding` helper import resolves:

```bash
cd examples
python core_sdk_example.py
```

| Script | Demonstrates |
|---|---|
| [`core_sdk_example.py`](core_sdk_example.py) | Plain `MemoryStore` — no framework adapter. `store.remember()` a `SemanticFact`, `store.facts.search()` to recall it. |
| [`langchain_example.py`](langchain_example.py) | `Db2ChatMessageHistory` (requires `agent-memory-sdk[langchain]`) — `add_message()` to remember chat turns, `store.working.search()` to recall one semantically. |
| [`openai_agents_example.py`](openai_agents_example.py) | `Db2Session` (requires `agent-memory-sdk[openai-agents]`) — `await session.add_items(...)` to remember, `store.working.search()` to recall. |
| [`mcp_example.py`](mcp_example.py) | `create_server(store)` (requires `agent-memory-sdk[mcp]`) — sets up the MCP server, then shows the same `remember`/`recall` calls the server's tools make internally. |
