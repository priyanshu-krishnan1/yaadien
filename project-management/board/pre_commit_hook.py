#!/usr/bin/env python3
"""Git pre-commit hook: regenerate project-management/BOARD.html from its
sharded sources (board/epics/*.json, board/stories/*.json) and stage the
result automatically, so a forgotten `make board` can never land a stale
board in a commit.

This file is the versioned source of truth for the hook's logic. It is
NOT itself a git hook — .git/hooks/ isn't tracked by git, so nothing here
runs automatically until installed. Run `make install-hooks` once per
clone to install a thin shim at .git/hooks/pre-commit that calls this
script; future edits to this file take effect immediately on the next
commit with no reinstall needed.

Chaining note: this repo's core.hooksPath is already claimed by an
IBM-managed Vault Radar secret-scanning hook (MDM-deployed — see
/opt/vault-radar/hooks/pre-commit, "Do not modify"). That hook's own
probe_chain() step specifically looks for and runs .git/hooks/pre-commit
as a "custom" chained hook before its own scan, and treats a nonzero exit
from it as a legitimate commit block (not a secret finding). So
installing this at the standard .git/hooks/pre-commit location — not by
touching core.hooksPath — is what lets both hooks run correctly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )
    build_script = repo_root / "project-management" / "board" / "build.py"
    board_html = repo_root / "project-management" / "BOARD.html"

    if not build_script.exists():
        return 0  # board/ structure not present (e.g. hook run in a stale checkout)

    before = board_html.read_text() if board_html.exists() else None

    result = subprocess.run([sys.executable, str(build_script)], cwd=repo_root)
    if result.returncode != 0:
        print(
            "\npre-commit: board validation failed (see errors above) — "
            "fix project-management/board/epics/*.json or stories/*.json "
            "and retry.",
            file=sys.stderr,
        )
        return 1

    after = board_html.read_text() if board_html.exists() else None
    if before != after:
        subprocess.run(
            ["git", "add", str(board_html)], cwd=repo_root, check=True
        )
        print(
            f"pre-commit: regenerated and staged "
            f"{board_html.relative_to(repo_root)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
