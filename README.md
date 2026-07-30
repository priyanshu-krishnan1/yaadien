# agent-memory-sdk

[![CI](https://github.com/oss-ai-connector/agent-memory-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/oss-ai-connector/agent-memory-sdk/actions/workflows/ci.yml)

Governed multi-type memory system for AI agents backed by IBM Db2 LUW.

> Full documentation added in Step 8.

Build prompts, the decision log, architecture doc, and local tracking board
live under [`project-management/`](project-management/) — see
[`project-management/README.md`](project-management/README.md) for an
index. None of it ships with the package.

## Development setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install the package and dev dependencies (editable)
pip install -e ".[dev]"

# 3. Run tests
pytest

# 4. Lint
ruff check .

# 5. Type-check
mypy src
```

> **Note:** `ibm_db` requires the Db2 CLI driver. For a live Db2 connection,
> set the env vars documented in `.env.example` before running any
> integration tests or the `scripts/check_connection.py` script.
> The unit-test suite mocks ibm_db and runs without a live Db2 instance.
