"""
tests/test_tru4_reproducibility.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
TRU-4 self-audit: verify that the Run E embedding-swap reproducibility
infrastructure has been added to the relevant project files.

These tests do **not** execute any benchmarks — they check that the
documentation, reproduce command, and decision record are present so
that a future engineer can run the check and interpret the result.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BENCHMARKS_MD = REPO_ROOT / "project-management" / "BENCHMARKS.md"
RUN_BENCHMARKS_PY = REPO_ROOT / "scripts" / "run_benchmarks.py"
DECISIONS_MD = REPO_ROOT / "project-management" / "DECISIONS.md"


def test_benchmarks_md_contains_run_e_section() -> None:
    """BENCHMARKS.md must contain a Run E section header."""
    text = BENCHMARKS_MD.read_text(encoding="utf-8")
    assert "Run E" in text, (
        "BENCHMARKS.md does not contain a 'Run E' section. "
        "Add the embedding-swap reproducibility check section (TRU-4)."
    )


def test_benchmarks_md_run_e_has_methodology_table() -> None:
    """BENCHMARKS.md Run E section must document the mxbai-embed-large swap."""
    text = BENCHMARKS_MD.read_text(encoding="utf-8")
    assert "mxbai-embed-large" in text, (
        "BENCHMARKS.md Run E section is missing the 'mxbai-embed-large' "
        "embedding model reference."
    )


def test_benchmarks_md_run_e_cites_lightmem() -> None:
    """BENCHMARKS.md must cite the LightMem reproduction paper (arXiv 2607.29104)."""
    text = BENCHMARKS_MD.read_text(encoding="utf-8")
    assert "2607.29104" in text, (
        "BENCHMARKS.md is missing the LightMem reproduction citation "
        "(arXiv 2607.29104). TRU-4 requires this as the motivating critique."
    )


def test_benchmarks_md_run_e_cites_mempalace() -> None:
    """BENCHMARKS.md must cite the MemPalace audit paper (arXiv 2604.21284)."""
    text = BENCHMARKS_MD.read_text(encoding="utf-8")
    assert "2604.21284" in text, (
        "BENCHMARKS.md is missing the MemPalace audit citation "
        "(arXiv 2604.21284). TRU-4 requires this as the motivating critique."
    )


def test_benchmarks_md_summary_has_run_e_row() -> None:
    """The 'Summary across runs' table must include a Run E row."""
    text = BENCHMARKS_MD.read_text(encoding="utf-8")
    # The summary section must contain both the heading and a Run E reference
    assert "Summary across runs" in text, (
        "BENCHMARKS.md is missing the 'Summary across runs' section."
    )
    summary_start = text.index("Summary across runs")
    summary_section = text[summary_start:]
    assert "Run E" in summary_section, (
        "The 'Summary across runs' table does not contain a Run E row. "
        "Add a row with status 'not yet run' (TRU-4)."
    )


def test_run_benchmarks_py_contains_mxbai_embed_large() -> None:
    """scripts/run_benchmarks.py must document the mxbai-embed-large reproduce command."""
    text = RUN_BENCHMARKS_PY.read_text(encoding="utf-8")
    assert "mxbai-embed-large" in text, (
        "scripts/run_benchmarks.py does not contain the 'mxbai-embed-large' "
        "reproduce command for Run E (TRU-4)."
    )


def test_run_benchmarks_py_contains_run_e_comment() -> None:
    """scripts/run_benchmarks.py must contain the Run E comment block."""
    text = RUN_BENCHMARKS_PY.read_text(encoding="utf-8")
    assert "Run E" in text, (
        "scripts/run_benchmarks.py is missing the 'Run E' comment block (TRU-4)."
    )


def test_decisions_md_contains_tru4_entry() -> None:
    """DECISIONS.md must contain the TRU-4 decision entry."""
    text = DECISIONS_MD.read_text(encoding="utf-8")
    assert "TRU-4" in text, (
        "DECISIONS.md does not contain a TRU-4 entry. "
        "Append the 2026-08-09 EPIC-11 TRU-4 decision block (TRU-4)."
    )


def test_decisions_md_tru4_mentions_embedding_swap() -> None:
    """The TRU-4 DECISIONS.md entry must reference the embedding-swap check."""
    text = DECISIONS_MD.read_text(encoding="utf-8")
    assert "mxbai-embed-large" in text, (
        "DECISIONS.md TRU-4 entry does not mention 'mxbai-embed-large'. "
        "The entry must describe the embedding-swap check."
    )


def test_decisions_md_tru4_cites_both_papers() -> None:
    """The TRU-4 DECISIONS.md entry must cite both motivating papers."""
    text = DECISIONS_MD.read_text(encoding="utf-8")
    assert "2607.29104" in text, (
        "DECISIONS.md TRU-4 entry is missing the LightMem citation (arXiv 2607.29104)."
    )
    assert "2604.21284" in text, (
        "DECISIONS.md TRU-4 entry is missing the MemPalace citation (arXiv 2604.21284)."
    )
