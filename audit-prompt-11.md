# Audit remediation prompt for Bob (round 11 — post ORC-2)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Item 1 is a real
correctness/product-quality gap (chunked content becomes unreachable
through every existing search path) — treat it as top priority. Item 2 is
a real but lower-stakes gate regression (mypy strict isn't actually
passing). Fix both, then commit (e.g. "fix: default search to chunk-aware
when chunking is active, resolve mypy strict errors").

Note: there's a large amount of unrelated in-progress work (a repo
reorganization plus edits to base.py/store.py/migrations/integration
tests) sitting uncommitted in this tree. Leave it alone — don't stage,
commit, or fold it into this fix. This prompt is scoped only to the two
items below.

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template — check
where it currently lives after the audit-prompt-10 relocation and insert
correctly relative to it), then commit.

1. CRITICAL — chunked content is silently unreachable through every
   existing search() call site. Chunking is enabled by default at write
   time (enable_chunking=True) whenever an embedding_provider is
   configured — a normal setup, since chunking requires one anyway. Once
   a record is chunked, its parent-row embedding becomes a zero-vector
   sentinel by design (this part is correct and already documented), so
   it can only be found via search(search_chunks=True). But
   search_chunks defaults to False, and nothing that calls search() —
   not the LangChain adapter, not the OpenAI Agents SDK adapter, not the
   MCP tool, not any other existing caller — was updated to pass it. Net
   effect: anyone who configures an embedding_provider and stores content
   over ~2000 characters gets that content silently unreachable through
   every pre-existing search path.

   Fix: change search()'s default behavior so it auto-detects whether
   chunk-aware search should apply, instead of requiring every caller to
   remember an explicit opt-in. Change the parameter from
   `search_chunks: bool = False` to `search_chunks: bool | None = None`.
   When `None` (the new default): use chunk-aware search automatically if
   `self._chunk_repo is not None` (i.e. chunking is actually active for
   this store), otherwise fall back to the standard path exactly as
   today. Explicit `True`/`False` from a caller always wins and skips the
   auto-detection, so anyone who wants to force one path or the other
   still can (e.g. to avoid the extra round-trip cost of chunk search
   when they know their content is always short).

   This closes the gap for every current and future caller with one
   change, rather than requiring each of the three adapters to be
   individually updated and every future caller to remember to do the
   same. Still: skim the three adapters (langchain.py, openai_agents.py,
   mcp_server.py) and their docstrings for any place that documents or
   implies "search only checks the parent embedding" and update the
   wording to reflect the new auto behavior.

   Add tests covering all three states: chunk_repo is None (unaffected,
   same as before), chunk_repo is set and search_chunks left at the
   default None (now finds chunked content automatically), and
   search_chunks explicitly passed as True/False overriding the
   auto-detection in both directions.

2. mypy strict is not actually clean — 12 real errors, despite the ORC-2
   completion comment listing "ruff clean" and saying nothing about
   mypy. Two causes:

   a. repositories/chunks.py imports DistanceMetric and SearchMode from
      agent_memory_sdk.repositories.base instead of their actual home,
      agent_memory_sdk.types — mypy flags this as an unexported
      re-import. Fix: import both directly from agent_memory_sdk.types.

   b. store.py builds a plain `chunk_kwargs = dict(chunk_repo=...,
      chunk_threshold=..., chunk_size=..., chunk_overlap=...)` and
      forwards it via `**chunk_kwargs` to all five repository
      constructors. The dict's inferred value type is a union
      (`int | ChunkRepository | None`), which mypy strict can't verify
      against each repository's individually-typed __init__ parameters.
      Fix: pass the four arguments as explicit keywords to each of the
      five repository constructors instead of building and splatting a
      dict — more repetitive, but it's what every other constructor call
      in this codebase already does, and it's what mypy can actually
      check.

   After both fixes, run `mypy src` and confirm it reports zero errors —
   don't just reduce the count.

After both items: run `pytest`, `ruff check .`, and `mypy src` and
confirm all three are clean before committing. Going forward, always
state the mypy result explicitly in the BOARD.html completion comment,
even when it's clean — silence on it reads as "wasn't checked," which is
exactly what happened here.
```
