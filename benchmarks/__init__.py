"""
benchmarks/
~~~~~~~~~~~
On-demand measurement harness for agent-memory-sdk.

This package is deliberately **not** part of the distributed wheel (see the
comment above ``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml`` —
same treatment as ``project-management/``: the wheel target only lists
``src/agent_memory_sdk``, so anything outside ``src/`` is excluded by
omission). It is also **not** run by the PH-1/PH-2 CI jobs — it requires a
live Db2 instance and, for the retrieval-quality suite, an
``EmbeddingProvider``/LLM judge. Run it on demand via ``make benchmark`` or
``python scripts/run_benchmarks.py``.

See ``benchmarks/README.md`` for setup and free-tier options.
"""
