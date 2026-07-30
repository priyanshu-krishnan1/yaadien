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


def _gen_extraction(rng: random.Random, run_id: str, index: int) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    city = rng.choice(_CITIES)
    scope = make_scope(run_id, tenant_index=0, agent_index=1000 + index, user_index=index, thread_index=index)
    sessions = [[f"{name} mentioned that they live in {city}."]]
    return BenchmarkQuestion(
        category="extraction",
        scope=scope,
        sessions=sessions,
        question=f"What city does {name} live in?",
        gold_answer=city,
        id=f"extraction-{index}",
    )


def _gen_multi_session(rng: random.Random, run_id: str, index: int) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    hobby = rng.choice(_HOBBIES)
    project = rng.choice(_PROJECTS)
    scope = make_scope(run_id, tenant_index=0, agent_index=2000 + index, user_index=index, thread_index=index)
    sessions = [
        [f"{name} said their favorite hobby is {hobby}."],
        [f"{name} said the project inspired by their favorite hobby is called {project}."],
    ]
    return BenchmarkQuestion(
        category="multi_session",
        scope=scope,
        sessions=sessions,
        question=f"What is the name of the project {name} named after their favorite hobby?",
        gold_answer=project,
        id=f"multi_session-{index}",
    )


def _gen_temporal_reasoning(rng: random.Random, run_id: str, index: int) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    start_year = 2024 + index % 3
    start_date = f"{start_year}-01-10"
    promo_date = f"{start_year}-09-22"
    scope = make_scope(run_id, tenant_index=0, agent_index=3000 + index, user_index=index, thread_index=index)
    sessions = [
        [f"On {start_date}, {name} started a new job at Acme Corp as a Junior Engineer."],
        [f"On {promo_date}, {name} was promoted to Senior Engineer at Acme Corp."],
    ]
    return BenchmarkQuestion(
        category="temporal_reasoning",
        scope=scope,
        sessions=sessions,
        question=f"What date did {name} start their job at Acme Corp, before the promotion?",
        gold_answer=start_date,
        id=f"temporal_reasoning-{index}",
    )


def _gen_knowledge_update(rng: random.Random, run_id: str, index: int) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    old_lang, new_lang = rng.sample(_LANGUAGES, 2)
    scope = make_scope(run_id, tenant_index=0, agent_index=4000 + index, user_index=index, thread_index=index)
    sessions = [
        [f"{name} said their favorite programming language is {old_lang}."],
        [f"{name} said: actually, I've switched — my favorite programming language is now {new_lang}, not {old_lang} anymore."],
    ]
    return BenchmarkQuestion(
        category="knowledge_update",
        scope=scope,
        sessions=sessions,
        question=f"What is {name}'s CURRENT favorite programming language?",
        gold_answer=new_lang,
        id=f"knowledge_update-{index}",
    )


def _gen_abstention(rng: random.Random, run_id: str, index: int) -> BenchmarkQuestion:
    name = rng.choice(_NAMES)
    city = rng.choice(_CITIES)
    unasked = rng.choice(_UNASKED_ATTRS)
    scope = make_scope(run_id, tenant_index=0, agent_index=5000 + index, user_index=index, thread_index=index)
    # Plant an unrelated fact so the scope isn't empty — abstention means
    # "don't answer a question this scope's memories don't support," not
    # "the scope has no memories at all."
    sessions = [[f"{name} mentioned that they live in {city}."]]
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
) -> list[BenchmarkQuestion]:
    """Generate a synthetic LongMemEval-shaped dataset.

    Args:
        run_id:         Value from ``benchmarks.common.scope_gen.new_run_id()``,
                         used to build collision-free scopes for this run.
        n_per_category: Number of questions generated per ability category
                         (default 4; total dataset size = 5 * n_per_category).
        seed:           RNG seed — same seed + n_per_category reproduces the
                         same dataset.

    Returns:
        A list of :class:`BenchmarkQuestion`, grouped by category in the
        order of :data:`ABILITY_CATEGORIES`.
    """
    rng = random.Random(seed)
    questions: list[BenchmarkQuestion] = []
    for category in ABILITY_CATEGORIES:
        gen = _GENERATORS[category]
        for i in range(n_per_category):
            questions.append(gen(rng, run_id, i))
    return questions
