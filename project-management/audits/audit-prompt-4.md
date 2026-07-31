# Audit remediation prompt for Bob (round 4 — post Step 6)

This is a one-off fix pass, separate from the Step N build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Item 1 is a critical,
verified functional defect (confirmed directly against OpenAI's own docs,
not a guess). Items 2 and 3 are minor. Fix all three, then commit (e.g.
"fix: OpenAI Agents SDK Session protocol conformance + adapter polish").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md — this entry should explicitly supersede the
"OpenAI Agents SDK — Db2Session" decision in the Step 6 entry, which
recorded the wrong protocol method names), then commit.

1. CRITICAL — Db2Session (adapters/openai_agents.py) does not implement
   the real OpenAI Agents SDK Session protocol. Verified directly against
   https://openai.github.io/openai-agents-python/ref/memory/session/ —
   the actual protocol is:

     async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]
     async def add_items(self, items: list[TResponseInputItem]) -> None
     async def pop_item(self) -> TResponseInputItem | None
     async def clear_session(self) -> None

   All four are `async def`. The current implementation has the wrong
   names, wrong signatures, and is missing one method entirely:
   `add_message(message: dict)` (should be `add_items(items: list[dict])`),
   `get_messages()` (should be `get_items(limit=None)`), `clear()` (should
   be `clear_session()`), and there is no `pop_item()` at all. This means
   the adapter's own usage-example docstring
   (`Runner.run(agent, input=..., session=Db2Session(...))`) would fail
   immediately in real use.

   Fix:
   - Rename/rewrite as `async def add_items(self, items: list[dict[str, Any]]) -> None`
     — iterate the list, persist each item the same way `add_message` does
     today (extract the per-item logic into a private helper if that reads
     better).
   - Rename/rewrite as `async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]`
     — keep the existing reverse-to-chronological-order logic; when
     `limit` is provided, return only the most recent `limit` items (the
     tail of the chronological list), matching the convention used by
     other Session backends (e.g. SQLiteSession) for truncating history.
   - Rename/rewrite as `async def clear_session(self) -> None` — same
     soft-delete-all-rows logic as today's `clear()`.
   - Add `async def pop_item(self) -> dict[str, Any] | None` — fetch the
     single most recent non-deleted row for the scope (order by
     created_at DESC, limit 1); if found, soft-delete it via
     `store.working.forget()` and return its deserialized content; if none
     found, return `None`.
   - The underlying store/repository calls stay synchronous (this
     matches the sync-first design already used elsewhere, e.g. the MCP
     adapter's `async def` tool handlers also wrap synchronous store
     calls directly, no threading) — just make the four Session methods
     themselves `async def` so the signatures match the protocol.
   - Update the module's usage-example docstring to show `await` on all
     four calls.
   - Update `tests/test_adapters.py::TestDb2Session` to call
     `add_items`/`get_items`/`pop_item`/`clear_session` instead of the old
     names, and drive them with `asyncio.run(...)` inside the existing
     sync test functions (simplest option — avoids adding a new
     `pytest-asyncio` dev dependency for a handful of tests; only add
     `pytest-asyncio` if you find `asyncio.run()` awkward here, your call).
     Add a test for `pop_item()` (currently untested since the method
     doesn't exist) covering: pops the most recent item, tombstones it
     (a second `pop_item()` call or `get_items()` afterward doesn't see
     it), and returns `None` when nothing is left to pop.

2. Minor — adapters/langchain.py's docstring is stale. It claims "The
   class dynamically inherits from BaseChatMessageHistory at instantiation
   time," but DECISIONS.md's Step 6 entry ("BaseChatMessageHistory is NOT
   dynamically subclassed") records that this was considered and
   deliberately rejected in favor of duck-typing. Delete/rewrite that
   paragraph in the class docstring so it matches the actual (correct,
   already-justified) design — no behavior change needed, doc-only.

3. Minor — Db2ChatMessageHistory.add_messages() doesn't actually batch;
   it loops calling self.add_message() per item, so it costs the same
   number of round-trips LangChain's docs say the batch method exists to
   avoid. If a low-effort improvement is feasible (e.g. one connection
   checkout for the whole batch instead of one per message via
   store.remember()), make it; if not worth the scope right now, at least
   update the method's docstring to honestly say it is not yet optimized
   for batching, rather than implying it is.

After all three: run `pytest`, `ruff check .`, and `mypy src` and confirm
all three are clean before committing.
```
