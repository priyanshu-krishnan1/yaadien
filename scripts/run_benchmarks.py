#!/usr/bin/env python3
"""
scripts/run_benchmarks.py
~~~~~~~~~~~~~~~~~~~~~~~~~
**Retired entry point** — this script was superseded in EPIC-13 (BM-2).
Benchmarks are now driven by pytest-benchmark via ``make benchmark``
(see ``benchmarks/README.md`` for the four-tier architecture and
``project-management/BENCHMARKS.md`` for the last checked-in report).

This file is retained as a documentation stub so that the reproduce
commands recorded in BENCHMARKS.md resolve to a real path in the repo
and so that the run-configuration comment blocks below are co-located
with the entry point they describe.

Do not add new logic here.  New benchmark runs should be added as
pytest-benchmark tests under ``benchmarks/`` and recorded in
``project-management/BENCHMARKS.md``.

--------------------------------------------------------------------

Recorded run configurations
============================

Run D — With SDK + Consolidator + Reconciler + facts search
------------------------------------------------------------
Reproduce with::

    make benchmark ARGS="--suite retrieval --embedding-provider ollama \\
        --judge ollama:llama3.1:8b --dataset-size 10 --seed 42 \\
        --baseline --consolidator benchmark --reconcile --search-facts"

(See BENCHMARKS.md § Run D for full results and analysis.)

--------------------------------------------------------------------

# Run E — embedding-swap reproducibility check
# Re-run: python scripts/run_benchmarks.py --suite retrieval --seed 42 \\
#         --top-k 5 --embedding-model mxbai-embed-large \\
#         --with-consolidator --with-reconciler
# Purpose: check whether Run D's SDK-beats-baseline result holds when
# the embedding model is swapped from nomic-embed-text to mxbai-embed-large.
# If the win holds within ±5%, it is architecture-driven not embedding-driven.

Run E — embedding-swap reproducibility check
--------------------------------------------
Same configuration as Run D (``--suite retrieval``, ``--seed 42``,
``--top-k 5``, ``--with-consolidator``, ``--with-reconciler``) but with
``mxbai-embed-large`` (768-dim → 1536 padded) instead of
``nomic-embed-text``.

Reproduce with::

    make benchmark ARGS="--suite retrieval --embedding-provider ollama \\
        --embedding-model mxbai-embed-large \\
        --judge ollama:llama3.1:8b --dataset-size 10 --seed 42 \\
        --baseline --consolidator benchmark --reconcile --search-facts"

Equivalently (direct invocation once this script is wired up again)::

    python scripts/run_benchmarks.py --suite retrieval --seed 42 \\
        --top-k 5 --embedding-model mxbai-embed-large \\
        --with-consolidator --with-reconciler

Interpretation:
- If Run E overall accuracy is within ±5% of Run D (98.0%), the
  SDK's advantage is **architecture-driven** (consolidator +
  reconciler + facts-search), not attributable to the choice of
  embedding model.
- If Run E accuracy falls outside that band, BENCHMARKS.md §
  "Summary across runs" should be updated to note the gain is
  **embedding-sensitive** and further investigation is warranted.

Motivating critique: LightMem reproduction (arXiv 2607.29104) and
MemPalace audit (arXiv 2604.21284) showed that vendor memory system
wins were often attributable to the embedding model, not the
architecture.  Run E applies the same standard to this SDK's own
Run D claimed win before it is cited externally.

(See BENCHMARKS.md § Run E for the methodology table and comparison
section.)
"""

# This stub intentionally contains no executable code.
