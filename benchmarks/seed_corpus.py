"""
benchmarks/seed_corpus.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Deterministic, resumable, size-parametrized corpus seeding for the benchmark
suite.  Feeds every downstream perf/scale/Db2-depth story (EPIC-14, EPIC-15,
EPIC-18).

CLI
---
::

    python benchmarks/seed_corpus.py --size 1k --seed 42
    python benchmarks/seed_corpus.py --size 50k --seed 42 --provider hashing
    python benchmarks/seed_corpus.py --size 1k --seed 42 --purge

Sizes: 1k / 50k / 500k rows per run.

Resumability
------------
A JSON checkpoint file is written to ``--state-dir`` (default
``.bench_seed_state/``) every 500 rows.  Interrupting mid-seed and re-running
resumes from the last checkpoint rather than restarting.

Determinism
-----------
* ``run_id`` is derived as ``f"seed-{seed}-{size}"`` — not a random UUID —
  so the same (seed, size) pair always addresses the same corpus.
* Each row's RNG is seeded deterministically: ``random.Random(seed + row_index)``.
* ``HashingEmbeddingProvider`` is MD5-based (not Python's randomised ``hash()``),
  so the same text always produces the same vector.
* Identical (seed, size) → byte-identical corpus across two runs.

Purge
-----
``--purge`` removes all rows whose ``tenant_id`` matches the seeded run, then
deletes the checkpoint file.  Safe to re-run after a purge; the corpus will
be re-seeded from scratch.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agent_memory_sdk.db.connection import ConnectionPool  # noqa: E402
from agent_memory_sdk.db.migrate import Migrator  # noqa: E402
from agent_memory_sdk.models import MemoryScope, SemanticFact  # noqa: E402
from agent_memory_sdk.store import MemoryStore  # noqa: E402
from benchmarks.common.embedding_providers import build_embedding_provider  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIZES: dict[str, int] = {"1k": 1_000, "50k": 50_000, "500k": 500_000}

_CHECKPOINT_BATCH = 500  # flush checkpoint every N rows

_BENCH_VOCAB: list[str] = [
    "agent", "memory", "session", "recall", "context", "query", "result",
    "scope", "tenant", "thread", "user", "fact", "profile", "procedure",
    "embed", "vector", "search", "write", "read", "index", "chunk", "token",
    "model", "extract", "consolidate", "reconcile", "resolve", "ingest",
    "latency", "cost", "scale", "load", "isolation", "benchmark", "measure",
    "database", "table", "column", "row", "insert", "select", "filter",
    "metadata", "category", "topic", "priority", "language", "source",
    "content", "sentence", "paragraph", "document", "corpus", "dataset",
    "training", "inference", "retrieval", "precision", "recall", "accuracy",
    "threshold", "limit", "offset", "page", "batch", "stream", "cursor",
    "connection", "pool", "timeout", "retry", "error", "exception", "log",
    "debug", "trace", "metric", "monitor", "alert", "gate", "pass", "fail",
    "semantic", "episodic", "working", "procedural", "entity", "relation",
    "knowledge", "skill", "instruction", "howto", "strategy", "goal", "plan",
    "action", "decision", "output", "input", "feedback", "reward", "signal",
]

_DEFAULT_N_TENANTS = 5
_DEFAULT_AGENTS_PER_TENANT = 4
_DEFAULT_USERS_PER_TENANT = 10
_DEFAULT_THREADS_PER_AGENT = 8

# CIW-10: opt-in per-phase timing. Set CIW10_PROFILE=1 in the environment to
# enable; defaults to False so normal runs pay zero overhead.
_CIW10_PROFILE: bool = os.environ.get("CIW10_PROFILE", "0") not in ("0", "", "false", "False")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _Checkpoint:
    seed: int
    size: str
    run_id: str
    total_rows: int
    rows_committed: int
    batch_size: int
    schema_version: int = 1


def _build_run_id(seed: int, size: str) -> str:
    return f"seed-{seed}-{size}"


def _checkpoint_path(state_dir: Path, seed: int, size: str) -> Path:
    return state_dir / f"seed_{seed}_{size}.json"


def _load_checkpoint(path: Path) -> _Checkpoint | None:
    if not path.exists():
        return None
    with path.open() as f:
        data = json.load(f)
    return _Checkpoint(**{k: v for k, v in data.items()})  # include all fields


def _save_checkpoint(path: Path, cp: _Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(asdict(cp), f, indent=2)


def _make_row_rng(seed: int, row_index: int) -> random.Random:
    """Per-row RNG — deterministic and independent of row ordering."""
    return random.Random(seed * 1_000_000 + row_index)


def _generate_content(rng: random.Random, chunk_threshold: int = 2000) -> str:
    """Generate content that is either unchunked (<threshold) or chunked (>threshold).

    70 % of rows: short (50–1800 chars, safely under the default 2000 threshold).
    30 % of rows: long (2200–8000 chars, safely over threshold).
    """
    if rng.random() < 0.70:
        target_len = rng.randint(50, min(1800, chunk_threshold - 200))
    else:
        target_len = rng.randint(chunk_threshold + 200, chunk_threshold + 6000)

    words: list[str] = []
    current_len = 0
    while current_len < target_len:
        word = rng.choice(_BENCH_VOCAB)
        words.append(word)
        current_len += len(word) + 1  # +1 for space

    return " ".join(words)


def _generate_metadata(rng: random.Random, cardinality: dict[str, int]) -> dict[str, str]:
    """Generate metadata with controlled cardinality (filter-selectivity knob for BM-23)."""
    return {
        field: f"{field}_{rng.randint(0, n - 1)}"
        for field, n in cardinality.items()
    }


def _generate_scope(
    rng: random.Random,
    run_id: str,
    n_tenants: int,
    agents_per_tenant: int,
    users_per_tenant: int,
    threads_per_agent: int,
) -> MemoryScope:  # type: ignore[name-defined]  # noqa: F821
    from benchmarks.common.scope_gen import make_scope as _make_scope  # local import ok here
    return _make_scope(
        run_id,
        tenant_index=rng.randint(0, n_tenants - 1),
        agent_index=rng.randint(0, agents_per_tenant - 1),
        user_index=rng.randint(0, users_per_tenant - 1),
        thread_index=rng.randint(0, threads_per_agent - 1),
    )


# ---------------------------------------------------------------------------
# Core seeding loop
# ---------------------------------------------------------------------------


def seed_corpus(args: argparse.Namespace) -> None:
    """Main seeding loop — writes rows to Db2 with checkpoint-based resumption."""
    total_rows = _SIZES[args.size]
    run_id = _build_run_id(args.seed, args.size)
    state_dir = Path(args.state_dir)
    cp_path = _checkpoint_path(state_dir, args.seed, args.size)

    # Load or create checkpoint.
    cp = _load_checkpoint(cp_path)
    if cp is None:
        cp = _Checkpoint(
            seed=args.seed,
            size=args.size,
            run_id=run_id,
            total_rows=total_rows,
            rows_committed=0,
            batch_size=_CHECKPOINT_BATCH,
        )
        _save_checkpoint(cp_path, cp)
        print(f"[seed_corpus] Starting new corpus: run_id={run_id}, total={total_rows:,}")
    else:
        print(
            f"[seed_corpus] Resuming from checkpoint: {cp.rows_committed:,}/{total_rows:,} "
            f"rows already committed"
        )

    embedding_provider = build_embedding_provider(args.provider, dim=args.dim)
    pool = ConnectionPool()
    try:
        migrator = Migrator(pool)
        migrator.run()

        store = MemoryStore(
            pool=pool,
            embedding_provider=embedding_provider,
            embedding_dim=args.dim,
            enable_chunking=True,  # exercises both chunked and unchunked paths per BM-4 spec
        )

        cardinality: dict[str, int] = {
            "category": args.cardinality_category,
            "topic": args.cardinality_topic,
            "priority": 5,
            "lang": 10,
            "source": 50,
        }

        start_ts = time.perf_counter()
        start_index = cp.rows_committed

        # CIW-10 profiling accumulators (zero-cost when _CIW10_PROFILE=False)
        _t_gen_total = 0.0
        _t_embed_total = 0.0
        _t_db_total = 0.0

        batch_records: list[tuple[SemanticFact, MemoryScope]] = []

        for row_index in range(start_index, total_rows):
            if _CIW10_PROFILE:
                _t0 = time.perf_counter()
            rng = _make_row_rng(args.seed, row_index)
            scope = _generate_scope(
                rng, run_id,
                n_tenants=_DEFAULT_N_TENANTS,
                agents_per_tenant=_DEFAULT_AGENTS_PER_TENANT,
                users_per_tenant=_DEFAULT_USERS_PER_TENANT,
                threads_per_agent=_DEFAULT_THREADS_PER_AGENT,
            )
            content = _generate_content(rng)
            metadata = _generate_metadata(rng, cardinality)
            if _CIW10_PROFILE:
                _t_gen_total += time.perf_counter() - _t0
                _t1 = time.perf_counter()
            embedding = embedding_provider(content)
            if _CIW10_PROFILE:
                _t_embed_total += time.perf_counter() - _t1

            batch_records.append((
                SemanticFact(
                    tenant_id=scope.tenant_id,
                    agent_id=scope.agent_id,
                    user_id=scope.user_id,
                    thread_id=scope.thread_id,
                    content=content,
                    metadata=metadata,
                    embedding=embedding,
                ),
                scope,
            ))

            if len(batch_records) >= _CHECKPOINT_BATCH:
                if _CIW10_PROFILE:
                    _t2 = time.perf_counter()
                store.facts.create_many(batch_records, commit_every=_CHECKPOINT_BATCH)
                if _CIW10_PROFILE:
                    _t_db_total += time.perf_counter() - _t2
                batch_records = []
                cp.rows_committed = row_index + 1
                _save_checkpoint(cp_path, cp)
                elapsed = time.perf_counter() - start_ts
                print(
                    f"[seed_corpus] {cp.rows_committed:,}/{total_rows:,} rows "
                    f"({elapsed:.1f}s elapsed)"
                )

        # Flush any remaining rows (last partial batch).
        if batch_records:
            if _CIW10_PROFILE:
                _t2 = time.perf_counter()
            store.facts.create_many(batch_records, commit_every=_CHECKPOINT_BATCH)
            if _CIW10_PROFILE:
                _t_db_total += time.perf_counter() - _t2
            batch_records = []

        # Final checkpoint.
        cp.rows_committed = total_rows
        _save_checkpoint(cp_path, cp)
        elapsed = time.perf_counter() - start_ts
        rows_seeded = total_rows - start_index
        print(
            f"[seed_corpus] Done: {total_rows:,} rows in {elapsed:.1f}s "
            f"(run_id={run_id})"
        )

        # CIW-10 profiling report
        if _CIW10_PROFILE and rows_seeded > 0:
            _t_other = elapsed - _t_gen_total - _t_embed_total - _t_db_total
            print(
                f"\n[CIW10 PROFILE] Per-phase breakdown over {rows_seeded:,} rows:\n"
                f"  content+metadata gen : {_t_gen_total:7.2f}s  "
                f"({100*_t_gen_total/elapsed:.1f}%,  "
                f"{1000*_t_gen_total/rows_seeded:.3f} ms/row)\n"
                f"  embedding            : {_t_embed_total:7.2f}s  "
                f"({100*_t_embed_total/elapsed:.1f}%,  "
                f"{1000*_t_embed_total/rows_seeded:.3f} ms/row)\n"
                f"  db write             : {_t_db_total:7.2f}s  "
                f"({100*_t_db_total/elapsed:.1f}%,  "
                f"{1000*_t_db_total/rows_seeded:.3f} ms/row)\n"
                f"  other (checkpoint/IO): {_t_other:7.2f}s  "
                f"({100*_t_other/elapsed:.1f}%)\n"
                f"  TOTAL                : {elapsed:7.2f}s",
                file=sys.stderr,
            )
    finally:
        pool.close()


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def purge_corpus(args: argparse.Namespace) -> None:
    """Delete all rows belonging to the given (seed, size) corpus."""
    run_id = _build_run_id(args.seed, args.size)
    state_dir = Path(args.state_dir)
    cp_path = _checkpoint_path(state_dir, args.seed, args.size)

    print(f"[seed_corpus] Purging corpus run_id={run_id} ...")

    pool = ConnectionPool()
    try:
        # Delete from all five memory tables + chunks using a tenant LIKE predicate.
        # The run_id is embedded in tenant_id as "bench-{run_id}-tenant-*"
        # via make_scope(), so the prefix is unambiguous.
        tenant_prefix = f"bench-{run_id}-tenant-%"
        tables = [
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
            "memory_chunks",
        ]
        total_deleted = 0
        with pool.get_connection() as conn:
            for table in tables:
                cur = conn.cursor()
                cur.execute(
                    f"DELETE FROM {table} WHERE tenant_id LIKE ?",  # noqa: S608
                    (tenant_prefix,),
                )
                # ibm_db_dbi cursor.rowcount may be -1 on Db2; use a SELECT COUNT instead
                # if an exact count is needed — for purge, the delete itself is the goal.
                total_deleted += max(0, cur.rowcount if cur.rowcount != -1 else 0)
                conn.commit()
        print(f"[seed_corpus] Deleted rows from {len(tables)} tables.")
    finally:
        pool.close()

    if cp_path.exists():
        cp_path.unlink()
        print(f"[seed_corpus] Removed checkpoint: {cp_path}")

    print(f"[seed_corpus] Purge complete (run_id={run_id}).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed a deterministic benchmark corpus into Db2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--size",
        choices=list(_SIZES),
        default="1k",
        help="Corpus size: 1k / 50k / 500k rows. Default: 1k",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed. Identical seed → byte-identical corpus. Default: 42",
    )
    p.add_argument(
        "--purge",
        action="store_true",
        help="Delete the corpus for this (size, seed) pair then exit.",
    )
    p.add_argument(
        "--provider",
        choices=["hashing", "sentence-transformers", "ollama"],
        default="hashing",
        help="Embedding provider. Default: hashing (deterministic, offline)",
    )
    p.add_argument(
        "--dim",
        type=int,
        default=1536,
        help="Embedding dimension. Default: 1536",
    )
    p.add_argument(
        "--state-dir",
        default=".bench_seed_state",
        help="Directory for checkpoint files. Default: .bench_seed_state/",
    )
    p.add_argument(
        "--cardinality-category",
        type=int,
        default=20,
        dest="cardinality_category",
        help="Number of distinct 'category' metadata values. Default: 20",
    )
    p.add_argument(
        "--cardinality-topic",
        type=int,
        default=100,
        dest="cardinality_topic",
        help="Number of distinct 'topic' metadata values. Default: 100",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.purge:
        purge_corpus(args)
    else:
        seed_corpus(args)


if __name__ == "__main__":
    main()
