# Audit remediation prompt for Bob (round 5 — post Step 7)

This is a one-off fix pass, separate from the Step N build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Item 1 is a real,
verified SQL-injection vulnerability — small fix, but treat it as
top priority. Items 2 and 3 are documentation-only. Fix all three, then
commit (e.g. "fix: close SQL injection in vector search + doc updates").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md — this entry should explicitly supersede/amend the
"Db2 12.1.5 fp0 compatibility fixes" entry's claim that vec_str carries
"no SQL-injection risk", since that claim was only true for two of the
three call sites), then commit.

1. CRITICAL — SQL injection via search()'s query_embedding parameter.
   The Db2 12.1.5 fp0 fix switched vector values from parameterized
   binding (`TO_VECTOR(?, FLOAT32)`) to inline SQL string literals
   (`CAST('{vec_str}' AS VECTOR(dim,FLOAT32))`) in repositories/base.py,
   justified by "_vec_to_str() only ever receives Python floats, so
   there's no injection risk." That's true for create()/update(), where
   record.embedding: list[float] is a Pydantic-validated field — Pydantic
   coerces or rejects non-numeric values before the record object even
   exists. It is NOT true for search()'s query_embedding parameter: that's
   a plain, unenforced type hint (Python does not validate type hints at
   runtime), and it's externally reachable via the MCP `recall` tool —
   `_tool_recall` in adapters/mcp_server.py does
   `args.get("query_embedding")` straight from client-supplied JSON with
   zero coercion, then passes it to repo.search(). A crafted string
   element (e.g. `"1) UNION SELECT ... --"`) in that array would be joined
   by _vec_to_str() and interpolated directly into the SQL string that
   gets executed — a real, exploitable SQL injection reachable from any
   MCP client that can call the `recall` tool.

   Fix: in `_vec_to_str(embedding: list[float]) -> str`
   (repositories/base.py), coerce every element with `float(x)` instead of
   the current `str(f)`:

       return "[" + ",".join(str(float(f)) for f in embedding) + "]"

   This one change closes the hole at all three call sites (create,
   update, search) simultaneously, since all three route through
   _vec_to_str(). Any non-numeric element now raises `ValueError` /
   `TypeError` before it ever reaches SQL, instead of being silently
   embedded as a literal. Add a test that calls `search()` (or the MCP
   `recall` tool) with a query_embedding containing a non-numeric string
   element and asserts it raises rather than executing.

2. ARCHITECTURE.md was not updated at all during Step 7 (no "Last
   updated: Step 7" line appears anywhere) despite Step 7 changing the
   actual query shape and schema. Two sections need updating:
   - Section 5 (`recall()` / semantic search flow, the Mermaid sequence
     diagram) still shows a single-step `SELECT ... ORDER BY
     VECTOR_DISTANCE(...)`. Update it to reflect the real two-step
     search() implementation: step 1 selects `id` ordered by distance
     (no VECTOR_SERIALIZE in the SELECT list — Db2 12.1.5 fp0 rejects
     combining it with VECTOR_DISTANCE in ORDER BY), step 2 fetches full
     rows by those IDs and reorders to restore distance rank in Python.
   - Section 3 (schema / column type legend and the index description)
     should no longer describe the `expires_at` indexes as filtered/
     partial (`WHERE expires_at IS NOT NULL`) — that predicate was
     removed from all five `ix_*_expires` indexes in migration 0002
     because Db2 12.1.5 fp0 doesn't support partial indexes (SQL0104N).
     Update the description to say these are now plain (unfiltered)
     indexes on `expires_at`.

3. INTEGRATION_TESTING.md documents a discontinued Docker image.
   `docker.io/ibmcom/db2` was migrated off Docker Hub in February 2023 in
   favor of `icr.io/db2_community/db2` — the old repo is expected to be
   gone or unmaintained by now, so the guide's Quick-start section would
   likely fail at `docker pull` for anyone following it today. Update:
   - The `docker run` command's image reference from `ibmcom/db2:latest`
     to `icr.io/db2_community/db2:latest` (same env vars and flags —
     LICENSE=accept, DBNAME, DB2INST1_PASSWORD, --privileged, -p
     50000:50000 — nothing else changes).
   - The Troubleshooting table row that says "The ibmcom/db2 image ships
     12.1" — update to reference the new image name.
   - On Apple Silicon Macs, `--platform=linux/amd64` is commonly needed
     for this image; consider adding a one-line note for that, since it's
     a common local-dev environment.

After all three: run `pytest`, `ruff check .`, and `mypy src` and confirm
all three are clean before committing. If you have a live Db2 instance
available, also re-run the integration suite to confirm the injection fix
doesn't change any legitimate search() behavior.
```
