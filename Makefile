.PHONY: benchmark benchmark-pr benchmark-nightly board board-check install-hooks

# EPIC-13 (BM-2): scripts/run_benchmarks.py was retired; benchmarks are now
# driven by pytest-benchmark. See benchmarks/README.md for the new four-tier
# architecture and project-management/BENCHMARKS.md for the last checked-in
# report.
#
# Run Tier 1 (PR tier) — requires DB2_* env vars:
#   make benchmark
# Run a specific tier:
#   make benchmark-nightly
# Pass extra pytest flags via ARGS, e.g.:
#   make benchmark ARGS="-k test_remember --benchmark-json=results.json"
benchmark:
	pytest benchmarks/ -m benchmark_pr $(ARGS)

benchmark-nightly:
	pytest benchmarks/ -m benchmark_nightly $(ARGS)

# Regenerate project-management/BOARD.html from project-management/board/
# (epics/*.json, stories/*.json). See project-management/board/README.md.
board:
	python project-management/board/build.py

# Fails if BOARD.html is stale relative to its sources — same check CI runs.
board-check:
	python project-management/board/build.py --check

# Installs a .git/hooks/pre-commit shim that runs
# project-management/board/pre_commit_hook.py (auto-rebuilds BOARD.html and
# stages it before every commit — see project-management/board/README.md).
# .git/hooks/ isn't tracked by git, so this must be re-run once per clone.
# Deliberately installs at the standard .git/hooks/pre-commit path, NOT via
# `git config core.hooksPath` — this repo's core.hooksPath is already
# claimed by an IBM-managed Vault Radar secret-scanning hook, which chains
# to .git/hooks/pre-commit itself as a "custom" hook; redirecting
# core.hooksPath instead would silently disable that hook.
install-hooks:
	@hook=".git/hooks/pre-commit"; \
	if [ -f "$$hook" ] && ! grep -q "agent-memory-sdk board pre-commit shim" "$$hook"; then \
		echo "install-hooks: $$hook already exists and wasn't installed by this target — not overwriting. Remove it manually first if you want this shim." >&2; \
		exit 1; \
	fi; \
	printf '#!/bin/sh\n# agent-memory-sdk board pre-commit shim — installed by `make install-hooks`.\n# Logic lives in project-management/board/pre_commit_hook.py (versioned);\n# this file is just a pointer and rarely needs to change.\nexec python3 "$$(git rev-parse --show-toplevel)/project-management/board/pre_commit_hook.py"\n' > "$$hook"; \
	chmod +x "$$hook"; \
	echo "install-hooks: installed $$hook"
