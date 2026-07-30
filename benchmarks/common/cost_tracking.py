"""
benchmarks/common/cost_tracking.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Wraps a Consolidator/Reconciler/Summarizer hook (agent_memory_sdk.types) to
record call counts and an estimated token cost, for the latency/cost suite.

Why this exists: this SDK's write path is developer-controlled (you decide
what to write via ``remember()``) rather than a passive background
LLM-extraction pipeline like Mem0/Bedrock. The only place an LLM call can
happen at all is inside a caller-supplied Consolidator/Reconciler/Summarizer
hook — and the default for all three is a NoOp that never calls an LLM. That
asymmetry (bounded, opt-in LLM cost vs. always-on extraction pipelines) is
itself one of the comparison points the latency/cost suite is meant to
surface, per the market study's SWOT.

Token counting caveat
----------------------
There is no live LLM wired into this harness by default, so there is no real
token-usage API response to read. Cost here is an ESTIMATE using the common
~4-characters-per-token rule of thumb for English text (the same
approximation OpenAI's own docs suggest for quick estimates), applied to the
combined input+output text of each hook call. This is clearly labeled as an
estimate everywhere it is reported — it is not a substitute for real
token-usage accounting from an actual API response.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Rule-of-thumb character-to-token ratio for English text (documented
#: estimate, not a real tokenizer — see module docstring).
CHARS_PER_TOKEN_ESTIMATE = 4.0


def _flatten_text(value: Any) -> str:
    """Best-effort stringification of a hook's input/output for token estimation.

    Handles the three shapes hooks in this SDK actually receive/return:
    list of memory model instances (has ``.content``), list of
    ``SupersedeDecision`` (has ``.reason``), or a plain string (Summarizer's
    return value). Falls back to ``str(value)`` for anything else.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if hasattr(item, "content"):
                parts.append(str(item.content))
            elif hasattr(item, "reason"):
                parts.append(str(item.reason))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(value)


@dataclass
class CostTrackingHook:
    """Wraps any Consolidator/Reconciler/Summarizer callable.

    Drop-in replacement: has the same ``__call__`` shape as the wrapped hook,
    so it can be passed straight to ``MemoryStore(consolidator=...)`` /
    ``reconciler=`` / ``summarizer=`` while transparently recording call
    counts and an estimated token cost.

    Args:
        wrapped:                The real Consolidator/Reconciler/Summarizer
                                 to delegate to. Pass a NoOp instance to
                                 measure the "hook configured but does
                                 nothing" baseline.
        cost_per_1k_tokens_usd: Blended input+output rate used for the
                                 estimated cost figure. Default 0.0 — the
                                 harness does not assume any particular
                                 model's pricing; set this to your model's
                                 published rate for a meaningful $ figure.
    """

    wrapped: Callable[..., Any]
    cost_per_1k_tokens_usd: float = 0.0
    call_count: int = field(default=0, init=False)
    total_estimated_tokens: int = field(default=0, init=False)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        input_text = " ".join(_flatten_text(a) for a in args)
        result = self.wrapped(*args, **kwargs)
        output_text = _flatten_text(result)
        combined_chars = len(input_text) + len(output_text)
        self.total_estimated_tokens += int(combined_chars / CHARS_PER_TOKEN_ESTIMATE)
        return result

    @property
    def total_estimated_cost_usd(self) -> float:
        return (self.total_estimated_tokens / 1000.0) * self.cost_per_1k_tokens_usd

    def summary(self) -> dict[str, Any]:
        return {
            "hook_call_count": self.call_count,
            "estimated_tokens_total": self.total_estimated_tokens,
            "estimated_cost_usd_total": round(self.total_estimated_cost_usd, 6),
            "token_estimate_method": (
                f"~{CHARS_PER_TOKEN_ESTIMATE:.0f} chars/token rule of thumb "
                "(no live LLM token-usage API wired in) — see cost_tracking.py docstring"
            ),
        }
