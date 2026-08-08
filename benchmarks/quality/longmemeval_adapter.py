"""
benchmarks/quality/longmemeval_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-16 (EPIC-16): Adapter that maps the real LongMemEval dataset
(xiaowu0162, ICLR 2025, Apache-2.0) to the agent-memory-sdk API.

Two public entry points:

``load_longmemeval(split, cache_dir)``
    Download (once) and cache the HuggingFace dataset to disk.  Subsequent
    calls load from the local cache so CI runs are fully offline after the
    first fetch.

``iter_questions(rows, run_id)``
    Yield :class:`LongMemEvalQuestion` objects, one per dataset row.  Each
    question object:
    * carries the full haystack sessions mapped to ``add_messages()`` calls
    * exposes the gold answer and labelled evidence session ids for Recall@k
    * bundles a unique, per-question ``MemoryScope`` so no cross-question
      leakage is possible at the adapter layer

Dataset facts (longmemeval_s)
------------------------------
* 500 questions across 6 ability categories (single-session QA, multi-session
  QA, temporal reasoning, knowledge update, abstention-single, abstention-multi)
* Each question has 1–10 haystack sessions; one or more sessions are "evidence"
  (the labelled answer is derivable from those sessions)
* The haystack is real conversational data — session turns are already natural
  language, not synthetic templates
* Apache-2.0 license; attribution recorded in benchmarks/README.md

Scope isolation guarantee
--------------------------
Each question gets a ``MemoryScope`` whose ``agent_id`` encodes the
``run_id`` (UUID hex) and the question index.  Two questions in the same run
never share an ``agent_id``; two runs never collide because ``run_id`` is
different.  This guarantee is enforced at the adapter level, not just
documented — callers must not reuse a scope across questions.

Usage
-----
::

    from benchmarks.quality.longmemeval_adapter import load_longmemeval, iter_questions
    from benchmarks.common.scope_gen import new_run_id

    rows = load_longmemeval("longmemeval_s")   # downloads once, then cached
    run_id = new_run_id()

    for q in iter_questions(rows, run_id):
        # Ingest haystack into the store
        store.add_messages(q.haystack_messages, q.scope, extract_memories=False)
        # … search, score, etc.
        # Erase after each question to keep the DB clean
        store.erase_all(q.scope)

Acceptance (BM-16)
------------------
* All 500 questions ingest without error end to end
* Per-question scope isolation verified (no cross-question leakage in the
  adapter itself — adapter never re-uses a scope across questions)
* Dataset caching keeps CI fully offline after the first fetch
* Licensing and attribution are recorded in benchmarks/README.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_memory_sdk.models import MemoryScope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LongMemEval HuggingFace dataset identifiers
# ---------------------------------------------------------------------------

_HF_REPO = "xiaowu0162/longmemeval"

# Valid split names exposed by the HuggingFace dataset.
# longmemeval_s  — 500 questions, single-day sessions (Tier 2 / nightly)
# longmemeval_m  — 500 questions, multi-day sessions (Tier 2 / nightly)
# longmemeval_oracle — oracle subset; sessions already filtered to evidence only
VALID_SPLITS = frozenset({"longmemeval_s", "longmemeval_m", "longmemeval_oracle"})

# ---------------------------------------------------------------------------
# Ability categories (LongMemEval's own taxonomy)
# ---------------------------------------------------------------------------

#: The six ability categories used by LongMemEval.  The string values match
#: the dataset's ``question_type`` field exactly so category filtering works
#: without an additional mapping step.
ABILITY_CATEGORIES: tuple[str, ...] = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention",
)

# Shorter display aliases used in reports (same order as ABILITY_CATEGORIES).
CATEGORY_DISPLAY: dict[str, str] = {
    "single-session-user": "single_session_user",
    "single-session-assistant": "single_session_assistant",
    "multi-session": "multi_session",
    "temporal-reasoning": "temporal_reasoning",
    "knowledge-update": "knowledge_update",
    "abstention": "abstention",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class HaystackSession:
    """One haystack session mapped for ingest.

    Attributes:
        session_id:    Original LongMemEval session identifier.
        messages:      List of message dicts ready for ``add_messages()``.
                       Each dict has ``"content"`` and ``"role"`` keys.
        is_evidence:   True if this session is labelled as evidence for the
                       associated question (used to compute Recall@k).
    """

    session_id: str
    messages: list[dict[str, Any]]
    is_evidence: bool


@dataclass
class LongMemEvalQuestion:
    """One LongMemEval question fully mapped to the SDK API.

    Attributes:
        question_id:       Original dataset question id.
        category:          Ability category (one of ``ABILITY_CATEGORIES``).
        question:          The question text to submit to search().
        gold_answer:       The ground-truth answer string.
        scope:             A unique :class:`~agent_memory_sdk.models.MemoryScope`
                           for this question — never shared across questions.
        haystack_sessions: All sessions in the haystack, in dataset order.
        evidence_session_ids: Set of session ids that are labelled evidence.
        haystack_messages: Flat list of all messages across all sessions, each
                           dict augmented with ``"session_id"`` in metadata so
                           retrieval results can be mapped back to sessions for
                           Recall@k computation.
    """

    question_id: str
    category: str
    question: str
    gold_answer: str
    scope: MemoryScope
    haystack_sessions: list[HaystackSession]
    evidence_session_ids: set[str]
    haystack_messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.haystack_messages:
            # Build flat message list from sessions if not supplied.
            self.haystack_messages = [
                msg for session in self.haystack_sessions for msg in session.messages
            ]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _default_cache_dir() -> Path:
    """Return the default local cache directory for the LongMemEval dataset.

    Respects the ``LONGMEMEVAL_CACHE_DIR`` environment variable so CI can
    point to a pre-warmed cache without any code changes.
    """
    env = os.environ.get("LONGMEMEVAL_CACHE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "longmemeval"


def _cache_path(split: str, cache_dir: Path) -> Path:
    return cache_dir / f"{split}.jsonl"


def _is_cached(split: str, cache_dir: Path) -> bool:
    return _cache_path(split, cache_dir).exists()


def _write_cache(rows: list[dict[str, Any]], split: str, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(split, cache_dir)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("LongMemEval %s cached to %s (%d rows)", split, path, len(rows))


def _read_cache(split: str, cache_dir: Path) -> list[dict[str, Any]]:
    path = _cache_path(split, cache_dir)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    logger.info("LongMemEval %s loaded from cache: %d rows", split, len(rows))
    return rows


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_longmemeval(
    split: str = "longmemeval_s",
    cache_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Load the LongMemEval dataset, downloading and caching on first call.

    After the first download the data is stored as a local JSONL file; all
    subsequent calls load from disk with no network access — so CI runs are
    fully offline once the cache is warm.

    Args:
        split:     Which LongMemEval split to load.  Must be one of
                   ``longmemeval_s``, ``longmemeval_m``, or
                   ``longmemeval_oracle``.
        cache_dir: Override the default cache directory
                   (``~/.cache/longmemeval`` or ``$LONGMEMEVAL_CACHE_DIR``).

    Returns:
        A list of raw dataset rows (dicts), one per question.  Use
        :func:`iter_questions` to map them to the SDK API.

    Raises:
        ValueError:  If *split* is not a known LongMemEval split name.
        ImportError: If the HuggingFace ``datasets`` package is not installed
                     and the dataset is not already cached locally.
    """
    if split not in VALID_SPLITS:
        raise ValueError(
            f"Unknown LongMemEval split {split!r}. "
            f"Expected one of: {', '.join(sorted(VALID_SPLITS))}."
        )

    resolved_cache = Path(cache_dir) if cache_dir is not None else _default_cache_dir()

    if _is_cached(split, resolved_cache):
        return _read_cache(split, resolved_cache)

    # --- Not cached yet — download from HuggingFace ---
    # NOTE: xiaowu0162/longmemeval's README declares data_files paths with a
    # ".json" suffix (e.g. "longmemeval_s.json"), but the blobs actually
    # committed to the repo have no extension (e.g. "longmemeval_s"). That
    # mismatch breaks `datasets.load_dataset()`'s auto config/format
    # resolution, so we fetch the raw JSON file directly instead.
    logger.info("LongMemEval %s not in cache; downloading from HuggingFace…", split)
    try:
        from huggingface_hub import hf_hub_download  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "The 'huggingface_hub' package is required to download LongMemEval. "
            "Install it with: pip install datasets\n"
            "Alternatively, set LONGMEMEVAL_CACHE_DIR to a pre-warmed cache."
        ) from exc

    path = hf_hub_download(repo_id=_HF_REPO, repo_type="dataset", filename=split)
    with open(path, encoding="utf-8") as f:
        rows: list[dict[str, Any]] = json.load(f)
    _write_cache(rows, split, resolved_cache)
    return rows


# ---------------------------------------------------------------------------
# Row → LongMemEvalQuestion mapping
# ---------------------------------------------------------------------------


def _parse_sessions(row: dict[str, Any]) -> tuple[list[HaystackSession], set[str]]:
    """Extract haystack sessions and evidence session ids from a raw row.

    LongMemEval rows carry the haystack as a list of sessions.  Each session
    is a dict (or list of dicts) with session-level metadata and a list of
    turn dicts with ``"role"`` / ``"content"`` keys.

    The dataset schema has evolved across versions; this function handles both
    the nested-dict (``"sessions": [{"session_id": ..., "turns": [...]}]``)
    shape and the flat list-of-turns shape by inspecting the actual types.

    Returns:
        (sessions, evidence_ids)  — the mapped sessions list and the set of
        evidence session id strings.
    """
    sessions: list[HaystackSession] = []
    evidence_ids: set[str] = set()

    raw_sessions: list[Any] = row.get("haystack_sessions") or row.get("sessions") or []
    raw_evidence: list[str] = row.get("evidence_session_ids") or row.get("evidence_ids") or []
    # Normalise to a set of strings for O(1) lookup.
    evidence_ids = {str(eid) for eid in raw_evidence}

    for sess_obj in raw_sessions:
        if isinstance(sess_obj, dict):
            session_id = str(sess_obj.get("session_id") or sess_obj.get("id") or "")
            turns_raw: list[Any] = (
                sess_obj.get("turns")
                or sess_obj.get("messages")
                or []
            )
        elif isinstance(sess_obj, list):
            # Some versions pack turns directly as a list.
            session_id = ""
            turns_raw = sess_obj
        else:
            logger.warning("Unexpected session object type %s; skipping.", type(sess_obj))
            continue

        messages: list[dict[str, Any]] = []
        for turn in turns_raw:
            if isinstance(turn, dict):
                content = str(turn.get("content") or turn.get("text") or "")
                role = str(turn.get("role") or "user")
            elif isinstance(turn, str):
                content = turn
                role = "user"
            else:
                continue
            if not content.strip():
                continue
            messages.append({
                "role": role,
                "content": content,
                "metadata": {"session_id": session_id, "lme_split": row.get("split", "")},
            })

        if not messages:
            continue
        is_evidence = bool(session_id and session_id in evidence_ids)
        sessions.append(
            HaystackSession(
                session_id=session_id,
                messages=messages,
                is_evidence=is_evidence,
            )
        )

    return sessions, evidence_ids


def _make_scope(run_id: str, question_index: int, question_id: str) -> MemoryScope:
    """Build a unique, question-scoped :class:`~agent_memory_sdk.models.MemoryScope`.

    The ``agent_id`` encodes the run, question index, and a short hash of the
    ``question_id`` so it is both human-readable and collision-proof even for
    adversarial question ids.
    """
    id_hash = hashlib.md5(question_id.encode()).hexdigest()[:8]  # noqa: S324 — not crypto
    return MemoryScope(
        tenant_id=f"lme-{run_id}",
        agent_id=f"lme-{run_id}-q{question_index:04d}-{id_hash}",
    )


def iter_questions(
    rows: list[dict[str, Any]],
    run_id: str,
    *,
    limit: int | None = None,
    category_filter: str | None = None,
) -> Iterator[LongMemEvalQuestion]:
    """Yield :class:`LongMemEvalQuestion` objects from raw LongMemEval rows.

    Each yielded object has a unique ``scope`` — no two questions share an
    ``agent_id``, preventing any cross-question leakage at the adapter layer.

    Args:
        rows:            Raw rows from :func:`load_longmemeval`.
        run_id:          A short run identifier (e.g. from
                         :func:`~benchmarks.common.scope_gen.new_run_id`) that
                         is embedded in every scope's ids so rows from different
                         runs never collide.
        limit:           If set, yield at most *limit* questions (useful for
                         smoke tests — does not affect caching or download).
        category_filter: If set, yield only questions whose ``question_type``
                         matches this string (e.g. ``"knowledge-update"``).

    Yields:
        One :class:`LongMemEvalQuestion` per row, in dataset order.
    """
    yielded = 0
    for idx, row in enumerate(rows):
        if limit is not None and yielded >= limit:
            break

        category_raw: str = str(
            row.get("question_type") or row.get("category") or "unknown"
        )
        if category_filter is not None and category_raw != category_filter:
            continue

        question_id: str = str(row.get("question_id") or row.get("id") or str(idx))
        question_text: str = str(row.get("question") or "")
        gold_answer: str = str(row.get("answer") or row.get("gold_answer") or "")

        sessions, evidence_ids = _parse_sessions(row)
        if not sessions:
            logger.warning(
                "LongMemEval row %s has no haystack sessions; skipping.", question_id
            )
            continue

        scope = _make_scope(run_id, idx, question_id)

        # Build flat haystack_messages list — include session_id in metadata
        # so downstream Recall@k can map retrieved records back to sessions.
        haystack_messages: list[dict[str, Any]] = []
        for session in sessions:
            haystack_messages.extend(session.messages)

        yield LongMemEvalQuestion(
            question_id=question_id,
            category=category_raw,
            question=question_text,
            gold_answer=gold_answer,
            scope=scope,
            haystack_sessions=sessions,
            evidence_session_ids=evidence_ids,
            haystack_messages=haystack_messages,
        )
        yielded += 1
