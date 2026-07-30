# project-management/

Process and tracking docs for `agent-memory-sdk`, kept separate from the
shipped package. None of this ships — the wheel build only packages
`src/agent_memory_sdk` (see `[tool.hatch.build.targets.wheel]` in
`pyproject.toml` at the repo root). This folder exists so the repo root
only shows what a user pulling the library actually needs: `README.md`,
`pyproject.toml`, `src/`, `tests/`, `scripts/`.

## What's here

- **`PROMPTS.md`** — the build-prompt sequence (Step 0 context + Steps
  1–8, ENH-1..4, ORC-1..4). Paste **Step 0** first into any fresh agent
  session on this repo, then feed it steps in order.
- **`DECISIONS.md`** — single source of truth for every decision made on
  this project, dated and appended-to, never rewritten. Read before
  starting any work here, append before finishing.
- **`ARCHITECTURE.md`** — current-state design doc (component/schema/
  sequence diagrams in Mermaid). Updated in place, not appended to.
- **`BOARD.html`** — local, self-contained Kanban board (epics + stories
  as embedded JSON). Stands in for Jira, whose MCP connection isn't wired
  up for this project. Open directly in a browser.
- **`INTEGRATION_TESTING.md`** — Docker/live-Db2 setup for the
  integration test suite.
- **`Chats.md`** — misc session notes.
- **`ai-agent-platform-competitive-analysis.md`** — market study of
  competing agent-memory platforms; the yardstick used by
  `beta-readiness-audit-prompt.md` for feature-completeness checks.
- **`audit-prompt.md`, `audit-prompt-2.md` … `audit-prompt-10.md`** —
  one-off fix-pass prompts, each a record of a completed post-step audit.
  Historical; already executed.
- **`beta-readiness-audit-prompt.md`** — pre-worldwide-beta verification
  pass: re-audits every "Done" board story against current code and
  checks feature-completeness against the market study.

## A note on paths in older entries

This folder — and everything in it — moved here from the repo root on
2026-07-30 (see the "Process/tracking docs moved out of repo root" entry
at the end of `DECISIONS.md`). Entries in `DECISIONS.md` and the contents
of `audit-prompt.md` through `audit-prompt-10.md` predate that move and
may reference these files by bare name (e.g. "read DECISIONS.md",
"see BOARD.html") the way they were addressed when they lived at the repo
root. Their content was left as-written since they're historical records
of work already done — read any such bare filename as
`project-management/<file>` today. `PROMPTS.md`'s Step 0 and
`beta-readiness-audit-prompt.md` (both still actively used going forward)
have already been updated to say so explicitly.
