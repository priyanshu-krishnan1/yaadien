.PHONY: benchmark

# Runs the on-demand benchmarking harness (benchmarks/) against a live Db2
# instance — NOT part of PH-1/PH-2 CI. See benchmarks/README.md for setup
# (env vars, free-tier embedding/judge options) and project-management/BENCHMARKS.md
# for the last checked-in report.
#
# Pass extra flags via ARGS, e.g.:
#   make benchmark ARGS="--suite retrieval --embedding-provider sentence-transformers --judge gemini"
benchmark:
	python scripts/run_benchmarks.py $(ARGS)
