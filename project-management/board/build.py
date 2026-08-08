#!/usr/bin/env python3
"""Regenerate project-management/BOARD.html from the sharded board data.

Source of truth lives in project-management/board/epics/*.json (one file
per epic) and project-management/board/stories/*.json (one file per
story) — small, independently-editable files so parallel agents/subagents
never collide on a single monolithic board file. This script validates
those files, assembles them into the same {"epics": [...], "stories":
[...]} shape the board's front-end JS has always expected, and injects
that JSON into project-management/board/template.html (the static
CSS/JS shell) to produce project-management/BOARD.html.

Usage:
    python project-management/board/build.py            # write BOARD.html
    python project-management/board/build.py --check     # exit 1 if stale

Also runnable as `make board` / `make board-check` from the repo root.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BOARD_DIR = Path(__file__).resolve().parent
PROJECT_MGMT_DIR = BOARD_DIR.parent
EPICS_DIR = BOARD_DIR / "epics"
STORIES_DIR = BOARD_DIR / "stories"
TEMPLATE = BOARD_DIR / "template.html"
OUTPUT = PROJECT_MGMT_DIR / "BOARD.html"

PLACEHOLDER = "__BOARD_DATA_JSON__"
VALID_STATUSES = {"To Do", "In Progress", "Done"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NATURAL_ID_RE = re.compile(r"^([A-Za-z]+)-(\d+)([A-Za-z]*)$")


class BoardValidationError(Exception):
    """Raised when a shard file fails schema validation."""


def natural_sort_key(story_id: str) -> tuple[str, int, str]:
    """Sort 'PREFIX-<n><suffix>' ids the way a human reads them.

    E.g. BENCH-2, BENCH-3a, BENCH-3b, BENCH-3c, BENCH-4 — not the
    lexicographic order that would put BENCH-10 before BENCH-2.
    """
    m = NATURAL_ID_RE.match(story_id)
    if not m:
        return (story_id, 0, "")
    prefix, number, suffix = m.groups()
    return (prefix, int(number), suffix)


def _git_tracked_files(directory: Path) -> set[Path]:
    """Return the set of paths under *directory* that git currently tracks.

    Untracked and ignored files are excluded so that a developer's
    work-in-progress JSON files on disk don't sneak into BOARD.html and
    cause CI's --check to fail (CI only has the committed files).
    """
    try:
        out = subprocess.check_output(
            ["git", "ls-files", str(directory)],
            cwd=directory,
            text=True,
        )
        return {directory / Path(p).name for p in out.splitlines() if p}
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not inside a git repo (e.g. a bare export) — fall back to all files.
        return set(directory.glob("*.json"))


def _load_json_files(directory: Path) -> list[dict[str, Any]]:
    tracked = _git_tracked_files(directory)
    items = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue  # e.g. _NEXT_ID.json — not a data record
        if path not in tracked:
            continue  # untracked/WIP file — exclude from build
        try:
            obj = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise BoardValidationError(f"{path}: invalid JSON — {e}") from e
        if obj.get("id") != path.stem:
            raise BoardValidationError(
                f"{path}: \"id\" field ({obj.get('id')!r}) must match "
                f"filename stem ({path.stem!r})"
            )
        items.append(obj)
    return items


def _validate_comments(owner_id: str, comments: Any) -> None:
    """Validate comments, tolerating the same legacy shapes the board's
    front-end normalizeComment() already handles: a bare string (a
    handful of pre-2026-08 VER-* comments), or {"author","date","body"}
    instead of {"date","text"} (PIPE-5). New comments should use the
    canonical {"date","text"} shape — that's enforced strictly here.
    """
    if not isinstance(comments, list):
        raise BoardValidationError(f"{owner_id}: \"comments\" must be a list")
    for c in comments:
        if isinstance(c, str):
            continue  # legacy bare-string comment — tolerated, not encouraged
        if not isinstance(c, dict):
            raise BoardValidationError(f"{owner_id}: bad comment entry {c!r}")
        if "text" not in c and "body" not in c:
            raise BoardValidationError(
                f"{owner_id}: comment needs \"text\" (or legacy \"body\") — got {c!r}"
            )
        if "date" not in c or not DATE_RE.match(c["date"]):
            raise BoardValidationError(
                f"{owner_id}: comment date {c.get('date')!r} must be YYYY-MM-DD"
            )


def load_and_validate() -> dict[str, list[dict[str, Any]]]:
    epics = _load_json_files(EPICS_DIR)
    stories = _load_json_files(STORIES_DIR)

    epic_ids = [e["id"] for e in epics]
    dupe_epics = {i for i in epic_ids if epic_ids.count(i) > 1}
    if dupe_epics:
        raise BoardValidationError(f"duplicate epic ids: {sorted(dupe_epics)}")

    story_ids = [s["id"] for s in stories]
    dupe_stories = {i for i in story_ids if story_ids.count(i) > 1}
    if dupe_stories:
        raise BoardValidationError(f"duplicate story ids: {sorted(dupe_stories)}")

    epic_id_set = set(epic_ids)
    for e in epics:
        for field in ("id", "title", "description", "comments"):
            if field not in e:
                raise BoardValidationError(f"{e.get('id', '?')}: missing \"{field}\"")
        _validate_comments(e["id"], e["comments"])

    for s in stories:
        for field in ("id", "epic_id", "title", "summary", "status", "comments"):
            if field not in s:
                raise BoardValidationError(f"{s.get('id', '?')}: missing \"{field}\"")
        if s["epic_id"] not in epic_id_set:
            raise BoardValidationError(
                f"{s['id']}: epic_id {s['epic_id']!r} does not match any epic file"
            )
        if s["status"] not in VALID_STATUSES:
            raise BoardValidationError(
                f"{s['id']}: status {s['status']!r} not in {sorted(VALID_STATUSES)}"
            )
        _validate_comments(s["id"], s["comments"])

    # Reconstruct the original grouped-by-epic, naturally-sorted display
    # order: epics in their own natural id order, stories grouped under
    # their epic (in that same epic order) and naturally sorted within it.
    epics.sort(key=lambda e: natural_sort_key(e["id"]))
    epic_order = {e["id"]: i for i, e in enumerate(epics)}
    stories.sort(key=lambda s: (epic_order[s["epic_id"]], natural_sort_key(s["id"])))

    return {"epics": epics, "stories": stories}


def render(data: dict[str, list[dict[str, Any]]]) -> str:
    template = TEMPLATE.read_text()
    if PLACEHOLDER not in template:
        raise BoardValidationError(f"{TEMPLATE}: missing {PLACEHOLDER} marker")
    # Pretty-printed (not minified): a single 200KB+ line broke normal
    # tooling (line-ranged reads, `git diff`, grep -n context) worse than
    # the confusion it was meant to prevent, and the banner comment +
    # PROMPTS.md's Step 0 redirect + the CI drift check already establish
    # "generated, don't hand-edit" without needing an unreadable blob too.
    blob = json.dumps(data, indent=2, ensure_ascii=False)
    return template.replace(PLACEHOLDER, blob)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="don't write; exit 1 if BOARD.html would change (CI drift check)",
    )
    args = parser.parse_args()

    try:
        data = load_and_validate()
        rendered = render(data)
    except BoardValidationError as e:
        print(f"board validation failed: {e}", file=sys.stderr)
        return 1

    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print(
                f"{OUTPUT} is stale relative to project-management/board/ "
                f"sources — run `python project-management/board/build.py` "
                f"and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} is up to date ({len(data['epics'])} epics, "
              f"{len(data['stories'])} stories).")
        return 0

    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT} ({len(data['epics'])} epics, "
          f"{len(data['stories'])} stories).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
