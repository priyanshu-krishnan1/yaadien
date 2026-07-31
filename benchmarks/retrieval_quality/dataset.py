"""
benchmarks/retrieval_quality/dataset.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Synthetic multi-session conversation dataset shaped after LongMemEval's five
long-term-memory ability categories (Wu et al., arXiv 2410.10813):

1. ``extraction``           — a fact stated once; the question asks for it directly.
2. ``multi_session``        — two facts stated in separate sessions that must
                               both be retrieved to answer the question.
3. ``temporal_reasoning``   — dated events; the question requires reasoning
                               about order/duration, not just recall of one fact.
4. ``knowledge_update``     — a fact is stated, then contradicted by a later
                               session; the question asks for the *current*
                               value, so retrieving the stale fact is wrong.
5. ``abstention``           — the question asks about something never
                               mentioned in this scope's sessions; the correct
                               gold answer is empty, meaning the system should
                               not assert an unsupported answer.

This is a **synthetic, template-generated dataset**, not the real LongMemEval
500-question benchmark (that dataset is not redistributed here). It follows
the same five-category shape and the same "plant facts across sessions, ask
a question, judge the answer" structure so a score produced against it is
methodologically comparable in *kind* — see benchmarks/common/report.py for
where the harness stamps whether a given run's judge/embedding config makes
the resulting number honestly comparable to vendor-reported figures.

Determinism: every generator function takes a ``seed`` so the same dataset
is reproduced across runs unless the seed is changed — required for a
reproducible benchmark report.

Session scaling (BENCH-5): :func:`generate_dataset` accepts an
``extra_turns_per_session`` parameter (default 0 = existing behaviour).
When > 0, each session is padded with that many additional unrelated
"noise" turns — sentences about a different randomly-chosen name/city/
language/hobby drawn from the same vocabulary pools — before the planted
fact turn.  This increases the total context size handed to the flat-
context baseline (stressing its ability to find the needle in a growing
haystack) while the SDK's vector-retrieval path is unaffected in
principle (it only needs to find the 1–2 semantically-relevant turns
regardless of how many noise turns are in the same scope).  The default
value of 0 leaves the existing dataset shape entirely unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from agent_memory_sdk.models import MemoryScope
from benchmarks.common.scope_gen import make_scope

ABILITY_CATEGORIES = (
    "extraction",
    "multi_session",
    "temporal_reasoning",
    "knowledge_update",
    "abstention",
)

_NAMES = ["Priya", "Marcus", "Yuki", "Fatima", "Diego", "Ingrid", "Kwame", "Elena"]
_LANGUAGES = ["Python", "Rust", "Go", "TypeScript", "Kotlin", "Elixir"]
_CITIES = ["Lisbon", "Nairobi", "Osaka", "Toronto", "Wellington", "Tallinn"]
_PROJECTS = ["Project Nightingale", "Project Meridian", "Project Alkaline", "Project Fenwick"]
_HOBBIES = ["chess", "pottery", "trail running", "birdwatching", "woodworking"]
_UNASKED_ATTRS = ["favorite type of cheese", "childhood nickname", "shoe size", "favorite constellation"]

# ---------------------------------------------------------------------------
# Noise-turn templates for BENCH-5 session padding
# ---------------------------------------------------------------------------
# These are used to pad each session with unrelated distractor turns when
# extra_turns_per_session > 0.  They must not contain any of the vocabulary
# tokens that could accidentally answer the benchmark questions.
_NOISE_TEMPLATES = [
    "{name} mentioned that their favourite drink is {item}.",
    "{name} said they spent the weekend doing {item}.",
    "{name} noted that the weather in {city} was unusually cold last week.",
    "{name} mentioned they recently read a book about {item}.",
    "{name} said they are thinking of learning {item} next year.",
    "{name} commented that {city} has great public transport.",
    "{name} mentioned that {item} is their favourite board game.",
    "{name} said their commute takes about forty minutes each day.",
    "{name} noted they prefer working in the mornings.",
    "{name} mentioned that their desk plant is a succulent.",
]
_NOISE_ITEMS = [
    "cycling", "cooking", "hiking", "photography", "meditation",
    "gardening", "origami", "knitting", "calligraphy", "swimming",
]


@dataclass
class BenchmarkQuestion:
    """One synthetic multi-session conversation + a question over it.

    Attributes:
        category:    One of :data:`ABILITY_CATEGORIES`.
        scope:       A unique :class:`MemoryScope` for this conversation
                     (fresh agent_id/user_id/thread_id — no overlap with any
                     other question in the dataset).
        sessions:    List of sessions; each session is a list of turn strings
                     to write via ``store.remember(WorkingMemory(...), scope)``,
                     in order.
        question:    The natural-language question to answer from retrieval.
        gold_answer: The expected answer. Empty string for the abstention
                     category — the correct system behavior is to not assert
                     an answer not supported by retrieved content.
    """

    category: str
    scope: MemoryScope
    sessions: list[list[str]]
    question: str
    gold_answer: str
    id: str = field(default="")


def _noise_turns(rng: random.Random, n: int) -> list[str]:
    """Return *n* unrelated distractor sentences for session padding (BENCH-5).

    Each turn is drawn from :data:`_NOISE_TEMPLATES` and is independent of
    the planted fact for any question category so it cannot accidentally
    answer the benchmark question.  Uses different names/cities/items from the
    same vocabulary pools, but the sentences themselves carry no
    question-answer signal.
    """
    turns: list[str] = []
    for _ in range(n):
        template = rng.choice(_NOISE_TEMPLATES)
        turn = template.format(
            name=rng.choice(_NAMES),
            city=rng.choice(_CITIES),
            item=rng.choice(_NOISE_ITEMS),
        )
        turns.append(turn)
    return turns


def _gen_extraction(
    rng: random.Random, run_id: str, index: int, extra_turns: int = 0
) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    city = rng.choice(_CITIES)
    scope = make_scope(run_id, tenant_index=0, agent_index=1000 + index, user_index=index, thread_index=index)
    # Noise turns precede the planted fact within the same session so the
    # relevant turn is never reliably "the last one" — the retriever must
    # actually find it, not rely on recency.
    planted_turn = f"{name} mentioned that they live in {city}."
    sessions = [_noise_turns(rng, extra_turns) + [planted_turn]]
    return BenchmarkQuestion(
        category="extraction",
        scope=scope,
        sessions=sessions,
        question=f"What city does {name} live in?",
        gold_answer=city,
        id=f"extraction-{index}",
    )


def _gen_multi_session(
    rng: random.Random, run_id: str, index: int, extra_turns: int = 0
) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    hobby = rng.choice(_HOBBIES)
    project = rng.choice(_PROJECTS)
    scope = make_scope(run_id, tenant_index=0, agent_index=2000 + index, user_index=index, thread_index=index)
    sessions = [
        _noise_turns(rng, extra_turns) + [f"{name} said their favorite hobby is {hobby}."],
        _noise_turns(rng, extra_turns) + [f"{name} said the project inspired by their favorite hobby is called {project}."],
    ]
    return BenchmarkQuestion(
        category="multi_session",
        scope=scope,
        sessions=sessions,
        question=f"What is the name of the project {name} named after their favorite hobby?",
        gold_answer=project,
        id=f"multi_session-{index}",
    )


def _gen_temporal_reasoning(
    rng: random.Random, run_id: str, index: int, extra_turns: int = 0
) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    start_year = 2024 + index % 3
    start_date = f"{start_year}-01-10"
    promo_date = f"{start_year}-09-22"
    scope = make_scope(run_id, tenant_index=0, agent_index=3000 + index, user_index=index, thread_index=index)
    sessions = [
        _noise_turns(rng, extra_turns) + [f"On {start_date}, {name} started a new job at Acme Corp as a Junior Engineer."],
        _noise_turns(rng, extra_turns) + [f"On {promo_date}, {name} was promoted to Senior Engineer at Acme Corp."],
    ]
    return BenchmarkQuestion(
        category="temporal_reasoning",
        scope=scope,
        sessions=sessions,
        question=f"What date did {name} start their job at Acme Corp, before the promotion?",
        gold_answer=start_date,
        id=f"temporal_reasoning-{index}",
    )


def _gen_knowledge_update(
    rng: random.Random, run_id: str, index: int, extra_turns: int = 0
) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    old_lang, new_lang = rng.sample(_LANGUAGES, 2)
    scope = make_scope(run_id, tenant_index=0, agent_index=4000 + index, user_index=index, thread_index=index)
    sessions = [
        _noise_turns(rng, extra_turns) + [f"{name} said their favorite programming language is {old_lang}."],
        _noise_turns(rng, extra_turns) + [f"{name} said: actually, I've switched — my favorite programming language is now {new_lang}, not {old_lang} anymore."],
    ]
    return BenchmarkQuestion(
        category="knowledge_update",
        scope=scope,
        sessions=sessions,
        question=f"What is {name}'s CURRENT favorite programming language?",
        gold_answer=new_lang,
        id=f"knowledge_update-{index}",
    )


def _gen_abstention(
    rng: random.Random, run_id: str, index: int, extra_turns: int = 0
) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    city = rng.choice(_CITIES)
    unasked = rng.choice(_UNASKED_ATTRS)
    scope = make_scope(run_id, tenant_index=0, agent_index=5000 + index, user_index=index, thread_index=index)
    # Plant an unrelated fact so the scope isn't empty — abstention means
    # "don't answer a question this scope's memories don't support," not
    # "the scope has no memories at all."
    sessions = [_noise_turns(rng, extra_turns) + [f"{name} mentioned that they live in {city}."]]
    return BenchmarkQuestion(
        category="abstention",
        scope=scope,
        sessions=sessions,
        question=f"What is {name}'s {unasked}?",
        gold_answer="",
        id=f"abstention-{index}",
    )


_GENERATORS = {
    "extraction": _gen_extraction,
    "multi_session": _gen_multi_session,
    "temporal_reasoning": _gen_temporal_reasoning,
    "knowledge_update": _gen_knowledge_update,
    "abstention": _gen_abstention,
}


def generate_dataset(
    run_id: str,
    n_per_category: int = 4,
    seed: int = 42,
    extra_turns_per_session: int = 0,
) -> list[BenchmarkQuestion]:
    """Generate a synthetic LongMemEval-shaped dataset.

    Args:
        run_id:                  Value from ``benchmarks.common.scope_gen.new_run_id()``,
                                  used to build collision-free scopes for this run.
        n_per_category:          Number of questions generated per ability category
                                  (default 4; total dataset size = 5 * n_per_category).
        seed:                    RNG seed — same seed + n_per_category reproduces the
                                  same dataset.
        extra_turns_per_session: Number of unrelated noise turns to prepend to each
                                  session (default 0 — existing dataset shape unchanged).
                                  Used by BENCH-5 to stress-test the flat-context
                                  baseline at larger context scale while the SDK's
                                  vector retrieval path should remain unaffected.
                                  With ``n`` noise turns per session:
                                  - extraction:         1 session  × (n+1) turns
                                  - multi_session:      2 sessions × (n+1) turns
                                  - temporal_reasoning: 2 sessions × (n+1) turns
                                  - knowledge_update:   2 sessions × (n+1) turns
                                  - abstention:         1 session  × (n+1) turns

    Returns:
        A list of :class:`BenchmarkQuestion`, grouped by category in the
        order of :data:`ABILITY_CATEGORIES`.
    """
    rng = random.Random(seed)
    questions: list[BenchmarkQuestion] = []
    for category in ABILITY_CATEGORIES:
        gen = _GENERATORS[category]
        for i in range(n_per_category):
            questions.append(gen(rng, run_id, i, extra_turns=extra_turns_per_session))
    return questions
