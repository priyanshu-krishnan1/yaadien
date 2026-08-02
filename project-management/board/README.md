# Board source data

`project-management/BOARD.html` is a **generated file**. Its source of
truth lives here, split into one small JSON file per record so that many
agents/subagents can edit the board at the same time without colliding on
a single monolithic file (the problem this structure replaces — see the
dated 2026-08-05 DECISIONS.md entry for the full rationale).

**Agents: never read or grep `BOARD.html` to determine board state** —
it's a single ~1700-line file carrying every epic and story at once,
exactly the "read the whole thing just to check one story's status"
problem this structure exists to avoid. To check or reason about board
state, read the specific file(s) you need directly:
`project-management/board/epics/<EPIC-ID>.json` or
`project-management/board/stories/<STORY-ID>.json`. `ls
project-management/board/stories/` (or `epics/`) to see what exists.
`BOARD.html` is a human-viewing artifact only — open it in a browser,
don't parse it.

```
board/
  template.html       static shell: CSS + render/move/comment JS, no data
  epics/
    EPIC-1.json ...    one file per epic: {id, title, description, comments}
    _NEXT_ID.txt       next free EPIC-N number (plain text, one integer)
  stories/
    STEP-1.json ...    one file per story (see shape below)
  build.py             regenerates ../BOARD.html from the files above
```

## Story file shape

```json
{
  "id": "PREFIX-N",
  "epic_id": "EPIC-N",
  "title": "Display title",
  "summary": "One-line description shown on the card",
  "status": "To Do" | "In Progress" | "Done",
  "description": "Full task description (optional on a few legacy stories)",
  "comments": [
    {"date": "YYYY-MM-DD", "text": "…"}
  ]
}
```

`id` must match the filename (`LIVE-3.json` → `"id": "LIVE-3"`). Use the
existing epic's story prefix for a new story in that epic (`STEP`, `ENH`,
`ORC`, `VER`, `PH`, `BENCH`, `PIPE`, `THRD`, `SDD`, `LIVE`, …), or pick a
new short all-caps prefix when starting a new epic. Number sequentially;
`build.py` sorts naturally (`BENCH-3a` < `BENCH-3b` < `BENCH-4`), not
lexicographically, so gaps are fine but reuse isn't — never reuse an id.

Two legacy comment shapes (a bare string, and `{"author","date","body"}`
instead of `{"date","text"}`) exist on a handful of pre-2026-08-05 stories
and are intentionally still accepted by both the validator and the
front-end's `normalizeComment()`. Don't introduce new comments in either
legacy shape — always use `{"date": "YYYY-MM-DD", "text": "…"}`.

## Workflow

**Update an existing story** (flip status, add a comment): edit exactly
that one `stories/PREFIX-N.json` file. Nothing else needs to change.

**Add a new story to an existing epic**: create one new
`stories/PREFIX-N.json` file. Independent of every other story file —
safe to do in parallel with any number of other stories being added or
updated at the same time, in the same epic or a different one.

**Add a new epic**: read `epics/_NEXT_ID.txt` for the next free number,
create `epics/EPIC-<n>.json`, then write `<n> + 1` back into
`_NEXT_ID.txt`. This file exists specifically so two agents proposing a
new epic at the same time collide on one tiny text file (a trivial git
conflict to resolve) instead of on the whole board.

**Regenerate the viewable board** — required after any change above:

```bash
python project-management/board/build.py
```

This validates every file (required fields, valid `status`, `epic_id`
resolves to a real epic, no duplicate ids, comment date format) before
writing `../BOARD.html`, and fails loudly instead of writing a broken
board. Run `python project-management/board/build.py --check` to verify
`BOARD.html` is up to date without writing it (this is what CI runs).

`make board` / `make board-check` from the repo root are shortcuts for
the two commands above.

## Forgetting to rebuild: a pre-commit hook covers it automatically

Run `make install-hooks` once per clone. It installs `.git/hooks/pre-commit`
(not tracked by git, hence one-time-per-clone) as a thin shim pointing at
`project-management/board/pre_commit_hook.py` (the versioned logic — edits
to that file take effect immediately, no reinstall needed). On every
commit the hook runs `build.py`: if a shard was edited but `BOARD.html`
wasn't regenerated, it rebuilds and `git add`s the result automatically so
the commit includes the correct board state without you having to
remember; if a shard is invalid, it aborts the commit with the validation
error instead of letting broken data land. `make board-check` in CI is
the second line of defense for anyone who skipped `install-hooks` (e.g. CI
itself, or a contributor who forgot).

This repo's `core.hooksPath` is already claimed by an IBM-managed Vault
Radar secret-scanning hook (MDM-deployed, `/opt/vault-radar/hooks/`, "do
not modify"). `install-hooks` deliberately writes to the standard
`.git/hooks/pre-commit` path instead of touching `core.hooksPath` —
Vault Radar's own hook already probes for and chains to exactly that
path as a "custom" pre-existing hook before running its own scan, so
both run correctly without needing to redirect (and thereby silently
disable) the security tool.
