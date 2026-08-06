# SDD-7: Extensibility Architecture

**EPIC-9 — Pluggable Hooks & Adapter Layer**
**Status:** Implemented

---

## Table of Contents

1. [The Pluggable-Protocol Pattern](#1-the-pluggable-protocol-pattern)
2. [NoOp Defaults](#2-noop-defaults)
3. [Adapter Architecture](#3-adapter-architecture)
4. [How to Add a New Adapter or Hook](#4-how-to-add-a-new-adapter-or-hook)

---

## 1. The Pluggable-Protocol Pattern

### What it is

The SDK defines a set of **single-callable protocols** — each a Python `Protocol` class with exactly one `__call__` method — that callers inject into [`MemoryStore`](../../src/agent_memory_sdk/store.py:248) at construction time. `MemoryStore` invokes each hook synchronously at a well-defined point in its read or write path. All five protocols have a paired **NoOp default** that is used when the caller does not supply a custom implementation.

This pattern is named the **pluggable-protocol pattern** in this codebase: "pluggable" because hooks are opt-in, defaulting to no-ops; "protocol" because they rely on Python structural subtyping (`typing.Protocol`) rather than inheritance.

The key design rationale is that it **enables LLM-backed behaviour without imposing a mandatory LLM dependency**. The core SDK has no LLM import anywhere. An LLM is only needed if a caller constructs and injects one of the protocol implementations (e.g. an `LLMConsolidator`). The default no-ops add zero overhead to the write path.

### The five protocols

All five are defined in [`src/agent_memory_sdk/types.py`](../../src/agent_memory_sdk/types.py).

| Protocol | Callable shape | Triggered by |
|---|---|---|
| [`Consolidator`](../../src/agent_memory_sdk/types.py:71) | `(raw_memories: list[_MemoryBase]) -> list[_MemoryBase]` | `MemoryStore.remember()` after a `working` or `episodic` write (ADD path only) |
| [`Reconciler`](../../src/agent_memory_sdk/types.py:242) | `(candidates: list[SemanticFact]) -> list[SupersedeDecision]` | `MemoryStore.reconcile()`, explicitly by the caller |
| [`IngestResolver`](../../src/agent_memory_sdk/types.py:438) | `(candidate: _MemoryBase, similar: list[tuple[_MemoryBase, float]]) -> IngestDecision` | `MemoryStore.remember()` **before** writing, against the top-k most-similar existing records |
| [`MemoryExtractor`](../../src/agent_memory_sdk/types.py:614) | `(messages: list[WorkingMemory], scope: MemoryScope) -> list[_MemoryBase]` | `MemoryStore.add_messages()` when `extract_memories=True`, once per batch |
| [`Summarizer`](../../src/agent_memory_sdk/types.py:790) | `(turns: list[WorkingMemory]) -> str` | `MemoryStore.get_context_card()` after the turns list is assembled |

### Invocation: synchronous, inline

All hooks are called synchronously on the same thread as the triggering store method. There is no queue, no background thread, no async dispatch. This is intentional: zero additional infrastructure is required to run the SDK. For production workloads where LLM calls are too slow to run inline, the recommended pattern is to leave the hook as its NoOp default (keeping the hot path fast) and run the LLM logic out-of-band in a polling worker — see the `Consolidator` docstring for the async/background pattern reference.

### Error-handling contracts per hook

Each hook's error-handling contract is determined by the `try/except` in `store.py`, not by the protocol definition itself. The contracts were verified by reading the actual store source.

**[`Consolidator`](../../src/agent_memory_sdk/types.py:71)**
Invoked inside [`_run_consolidator()`](../../src/agent_memory_sdk/store.py:659). Wrapped in `try/except Exception`: an exception is **caught, logged, and not propagated**. The original `remember()` write already succeeded; a consolidation failure does not roll it back.

```python
# store.py — _run_consolidator()
try:
    derived = self._consolidator(raw_memories)
except Exception:
    logger.exception(
        "Consolidator raised an exception; derived memories not written."
    )
    return
```

**[`Reconciler`](../../src/agent_memory_sdk/types.py:242)**
Invoked inside [`reconcile()`](../../src/agent_memory_sdk/store.py:1270). Wrapped in `try/except Exception`: an exception is **caught, logged, and not propagated** — `reconcile()` returns an empty list.

```python
# store.py — reconcile()
try:
    decisions = self._reconciler(candidates)
except Exception:
    logger.exception(
        "Reconciler raised an exception; no supersession decisions applied."
    )
    return []
```

**[`IngestResolver`](../../src/agent_memory_sdk/types.py:438)**
Invoked inside [`_resolve_and_act()`](../../src/agent_memory_sdk/store.py:526). Wrapped in `try/except Exception`: an exception is **caught, logged, and not propagated** — the decision falls back to `IngestAction.ADD`, so the write proceeds as a normal insert.

```python
# store.py — _resolve_and_act()
try:
    decision = self._ingest_resolver(record, similar)
except Exception:
    logger.exception(
        "IngestResolver raised an exception; falling back to ADD."
    )
    decision = IngestDecision(action=IngestAction.ADD)
```

**[`MemoryExtractor`](../../src/agent_memory_sdk/types.py:614)**
Invoked inside [`add_messages()`](../../src/agent_memory_sdk/store.py:1693). Wrapped in `try/except Exception`: an exception is **caught, logged, and not propagated**. This contract is also stated explicitly in the `MemoryExtractor` docstring: *"Extractor errors are caught and logged, never propagated — an extraction failure must not roll back the original `add_messages()` write."*

```python
# store.py — add_messages()
try:
    derived = self._memory_extractor(stored_records, scope)
except Exception:
    logger.exception(
        "MemoryExtractor raised an exception; derived memories not written."
    )
    derived = []
```

**[`Summarizer`](../../src/agent_memory_sdk/types.py:790)**
Invoked inside [`get_context_card()`](../../src/agent_memory_sdk/store.py:1480). Wrapped in `try/except Exception`: an exception is **caught, logged, and not propagated** — `ContextCard.summary` is set to `None` rather than the exception bubbling out.

```python
# store.py — get_context_card()
if not isinstance(self._summarizer, NoOpSummarizer):
    try:
        result = self._summarizer(turns)
        summary = result if result else None
    except Exception:
        logger.exception(
            "Summarizer raised an exception; ContextCard.summary set to None."
        )
```

**Summary table**

| Hook | Exception behaviour |
|---|---|
| `Consolidator` | Caught + logged; original write unaffected; derived memories not written |
| `Reconciler` | Caught + logged; `reconcile()` returns `[]`; no supersession applied |
| `IngestResolver` | Caught + logged; falls back to `ADD`; write proceeds normally |
| `MemoryExtractor` | Caught + logged; derived memories not written; original `add_messages()` write unaffected |
| `Summarizer` | Caught + logged; `ContextCard.summary` set to `None` |

---

## 2. NoOp Defaults

All five NoOp classes are defined in [`src/agent_memory_sdk/types.py`](../../src/agent_memory_sdk/types.py) and re-exported from the top-level [`__init__.py`](../../src/agent_memory_sdk/__init__.py).

`MemoryStore.__init__` constructs the appropriate NoOp when a caller does not supply a hook argument:

```python
# store.py — MemoryStore.__init__()
self._consolidator    = consolidator     if consolidator     is not None else NoOpConsolidator()
self._reconciler      = reconciler       if reconciler       is not None else NoOpReconciler()
self._summarizer      = summarizer       if summarizer       is not None else NoOpSummarizer()
self._ingest_resolver = ingest_resolver  if ingest_resolver  is not None else NoOpIngestResolver()
self._memory_extractor = memory_extractor if memory_extractor is not None else NoOpMemoryExtractor()
```

### [`NoOpConsolidator`](../../src/agent_memory_sdk/types.py:203)

**Behaviour:** always returns `[]`.

No derived memories are written. With this default, `remember()` for working/episodic writes has the same cost as a plain `repo.create()` — the consolidation path is entered but exits immediately after the empty-list check.

### [`NoOpReconciler`](../../src/agent_memory_sdk/types.py:376)

**Behaviour:** always returns `[]`.

`MemoryStore.reconcile()` with this default is a no-op: no facts are ever superseded automatically.

### [`NoOpIngestResolver`](../../src/agent_memory_sdk/types.py:590)

**Behaviour:** always returns `IngestDecision(action=IngestAction.ADD)`.

This NoOp has a **special-case optimisation** in `remember()`: [`store.py` line 512](../../src/agent_memory_sdk/store.py:512) checks `isinstance(self._ingest_resolver, NoOpIngestResolver)` and, when true, skips the similarity `search()` round-trip entirely and calls `repo.create()` directly. This means using the default resolver produces exactly the same DB operations as the pre-PIPE-2 path — zero added overhead.

```python
# store.py — remember()
if isinstance(self._ingest_resolver, NoOpIngestResolver):
    # Fast path — unchanged pre-PIPE-2 behavior, no similarity search.
    stored: _MemoryBase = repo.create(record, scope)
    did_add = True
else:
    stored, did_add = self._resolve_and_act(repo, record, scope)
```

No other NoOp is `isinstance`-checked; only `NoOpIngestResolver` has this special-cased fast path.

The `Summarizer` NoOp check in `get_context_card()` is a minor optimisation that skips the `try/except` wrapper:

```python
# store.py — get_context_card()
if not isinstance(self._summarizer, NoOpSummarizer):
    ...  # only enter when a real summarizer is configured
```

### [`NoOpMemoryExtractor`](../../src/agent_memory_sdk/types.py:723)

**Behaviour:** always returns `[]`.

`add_messages()` with this default (or when `extract_memories=False`) does not invoke the extractor at all — the `isinstance(self._memory_extractor, NoOpMemoryExtractor)` check in `add_messages()` gates entry.

### [`NoOpSummarizer`](../../src/agent_memory_sdk/types.py:860)

**Behaviour:** always returns `""`.

`get_context_card()` with this default sets `ContextCard.summary = None` (no LLM call, no overhead). The `isinstance` guard prevents the `try/except` block from being entered.

---

## 3. Adapter Architecture

### Integration targets and extras groups

The SDK ships four adapter modules, one per integration target. Each is an optional dependency gated by a `pip` extras group defined in [`pyproject.toml`](../../pyproject.toml:46).

| Integration target | Module | Extras group | Install command |
|---|---|---|---|
| LangChain | [`adapters/langchain.py`](../../src/agent_memory_sdk/adapters/langchain.py) | `langchain` | `pip install 'agent-memory-sdk[langchain]'` |
| OpenAI Agents SDK | [`adapters/openai_agents.py`](../../src/agent_memory_sdk/adapters/openai_agents.py) | `openai-agents` | `pip install 'agent-memory-sdk[openai-agents]'` |
| Model Context Protocol | [`adapters/mcp_server.py`](../../src/agent_memory_sdk/adapters/mcp_server.py) | `mcp` | `pip install 'agent-memory-sdk[mcp]'` |
| Microsoft Agent Framework | [`adapters/agent_framework.py`](../../src/agent_memory_sdk/adapters/agent_framework.py) | `agent-framework` | `pip install 'agent-memory-sdk[agent-framework]'` |

The `all` group installs all four:

```toml
# pyproject.toml
[project.optional-dependencies]
langchain = [
    "langchain-core>=1.5.3",
]
openai-agents = [
    "openai-agents>=0.19.1",
]
mcp = [
    "mcp>=1.19.0,<2",
]
agent-framework = [
    "agent-framework",
]
all = [
    "agent-memory-sdk[langchain]",
    "agent-memory-sdk[openai-agents]",
    "agent-memory-sdk[mcp]",
    "agent-memory-sdk[agent-framework]",
]
```

### The thin-layer-over-MemoryStore shape

Every adapter is a thin wrapper that exposes the integration framework's own memory or history interface while delegating all reads and writes to a `MemoryStore` instance that is injected at construction time. No adapter contains any storage logic of its own.

```
LangChain chain
  └─ Db2ChatMessageHistory          # implements BaseChatMessageHistory
       └─ self._store (MemoryStore) # all reads/writes go here
            └─ store.working        # WorkingMemoryRepository → Db2

OpenAI Agents SDK Runner
  └─ Db2Session                     # implements Session protocol
       └─ self._store (MemoryStore)

MCP-compatible agent (any language)
  └─ mcp.server.Server              # four tools: remember/recall/forget/list
       └─ store (MemoryStore)       # closed over by tool handlers

Microsoft Agent Framework Agent
  └─ MemoryStoreContextProvider     # subclasses ContextProvider
       └─ self._store (MemoryStore)
  └─ MemoryStoreHistoryProvider     # subclasses HistoryProvider
       └─ self._store (MemoryStore)
```

The `MemoryStore` is always the canonical storage layer. Adapters translate between framework-specific message/session types and `MemoryStore`'s model types (`WorkingMemory`, `EpisodicMemory`, etc.).

### Import-guard isolation

Each adapter imports its optional framework dependency only at instantiation time (or, in the case of the Agent Framework adapter, at module load with a graceful fallback). Attempting to instantiate an adapter class without the optional dependency installed raises `ImportError` with an actionable install command — the rest of the SDK remains importable without any adapter dependency present.

Every adapter implements this via a `_require_*()` guard function that is called in `__init__`. The canonical pattern, taken from [`adapters/openai_agents.py`](../../src/agent_memory_sdk/adapters/openai_agents.py:92):

```python
# adapters/openai_agents.py

def _require_openai_agents() -> None:
    """Raise ImportError with an actionable message if openai-agents is absent."""
    try:
        import agents  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The OpenAI Agents SDK adapter requires openai-agents>=0.0.10. "
            "Install it with: pip install 'agent-memory-sdk[openai-agents]'"
        ) from exc


class Db2Session:
    def __init__(self, store, agent_id, session_id=None, user_id=None, tenant_id=None):
        _require_openai_agents()   # <-- called eagerly in __init__
        self._store = store
        ...
```

The same pattern appears in every adapter:

| Adapter | Guard function |
|---|---|
| `langchain.py` | `_require_langchain()` |
| `openai_agents.py` | `_require_openai_agents()` |
| `mcp_server.py` | `_require_mcp()` |
| `agent_framework.py` | `_require_agent_framework()` |

The Agent Framework adapter has one variation: because `MemoryStoreContextProvider` and `MemoryStoreHistoryProvider` must **subclass** `agent_framework.ContextProvider` / `HistoryProvider` (rather than duck-type them), the base classes are imported at module scope inside a `try/except ImportError`, falling back to `object` if absent. The `_require_agent_framework()` guard is still called eagerly in `__init__`, so instantiation — not import — is where the actionable error is raised, matching all other adapters.

---

## 4. How to Add a New Adapter or Hook

### Adding a new protocol hook

Follow these four steps, mirroring the existing hooks in [`types.py`](../../src/agent_memory_sdk/types.py) and [`store.py`](../../src/agent_memory_sdk/store.py).

#### (a) Define the `Protocol` in `types.py`

Add a `Protocol` class with a single `__call__` method. Document the signature, when it is called, and what it must return.

```python
# src/agent_memory_sdk/types.py

class MyHook(Protocol):
    """Called by MemoryStore after ... Returns ..."""
    def __call__(self, arg: SomeType) -> ReturnType:
        ...
```

#### (b) Define the NoOp default alongside it

Add a plain class (not a `Protocol`) that returns the appropriate "do nothing" value. Name it `NoOp<HookName>`.

```python
class NoOpMyHook:
    """Default: does nothing."""
    def __call__(self, arg: SomeType) -> ReturnType:
        return <appropriate_empty_value>
```

#### (c) Add an optional parameter to `MemoryStore.__init__`

In [`store.py`](../../src/agent_memory_sdk/store.py:345), add `my_hook: Any | None = None` to `__init__`, store it with the NoOp fallback, and import both the NoOp and (if needed) the action/decision type from `types.py`.

```python
# store.py — MemoryStore.__init__()
from agent_memory_sdk.types import NoOpMyHook

def __init__(self, ..., my_hook: Any | None = None):
    ...
    self._my_hook = my_hook if my_hook is not None else NoOpMyHook()
```

#### (d) Invoke the hook at the appropriate point in the read/write path

Call `self._my_hook(...)` at the appropriate place in the store. Wrap in `try/except Exception`, log the error, and degrade gracefully — a hook failure must never crash the caller's write or read.

```python
# store.py — inside the relevant store method
try:
    result = self._my_hook(some_arg)
except Exception:
    logger.exception("MyHook raised an exception; <fallback behaviour>.")
    result = <fallback>
```

Expose the new protocol and its NoOp from `__init__.py` so callers can import them from the top-level package.

---

### Adding a new integration adapter

Follow these four steps, mirroring an existing adapter in [`src/agent_memory_sdk/adapters/`](../../src/agent_memory_sdk/adapters/).

#### (a) Create `adapters/my_framework.py`

The adapter must accept a `MemoryStore` instance at construction time and delegate all reads/writes to it. It should implement the target framework's interface (protocol, base class, or duck-type).

```python
# src/agent_memory_sdk/adapters/my_framework.py

from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.models import MemoryScope, WorkingMemory


class Db2MyFrameworkMemory:
    def __init__(self, store: MemoryStore, agent_id: str) -> None:
        _require_my_framework()
        self._store = store
        self._scope = MemoryScope(agent_id=agent_id)

    def write(self, text: str) -> None:
        record = WorkingMemory(agent_id=self._scope.agent_id, content=text)
        self._store.remember(record, self._scope)
```

#### (b) Add a `_require_my_framework()` import guard

Place the guard at the top of the adapter module. Call `_require_my_framework()` in every class `__init__`.

```python
# src/agent_memory_sdk/adapters/my_framework.py

def _require_my_framework() -> None:
    """Raise ImportError with an actionable message if my-framework is absent."""
    try:
        import my_framework  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The my-framework adapter requires my-framework>=1.0. "
            "Install it with: pip install 'agent-memory-sdk[my-framework]'"
        ) from exc
```

#### (c) Add an extras group to `pyproject.toml`

Add a named group under `[project.optional-dependencies]` and include it in the `all` convenience group.

```toml
# pyproject.toml

[project.optional-dependencies]
my-framework = [
    "my-framework>=1.0",
]
all = [
    "agent-memory-sdk[langchain]",
    "agent-memory-sdk[openai-agents]",
    "agent-memory-sdk[mcp]",
    "agent-memory-sdk[agent-framework]",
    "agent-memory-sdk[my-framework]",   # add here
]
```

#### (d) Document it in `adapters/__init__.py`

Add a short entry for the new adapter to the module docstring in [`adapters/__init__.py`](../../src/agent_memory_sdk/adapters/__init__.py), following the format of the existing four entries. If the adapter exposes classes that belong in the top-level public API, add them to the `__all__` list in [`src/agent_memory_sdk/__init__.py`](../../src/agent_memory_sdk/__init__.py) as well.
