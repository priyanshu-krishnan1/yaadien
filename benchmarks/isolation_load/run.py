"""
benchmarks/isolation_load/run.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Concurrent writers/readers across many synthetic tenants/agents, asserting
zero cross-scope result leakage under real concurrent load against a live
Db2 instance.

VER-5 (project-management/DECISIONS.md) verified the scope-isolation
boundary (``_scope_predicates`` bound `?` params on all 7 SQL paths) with
mocked cursors in single-threaded unit tests. That is a correct static/SQL
check but it cannot catch a class of bug static analysis can't see:
concurrent connections from a shared pool returning a row to the wrong
caller due to a connection-reuse or result-buffering bug. This suite
exercises that path for real: many threads sharing one ``ConnectionPool``,
each writing and then reading only its own scope, checking every returned
row for another scope's marker.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.report import IsolationLoadResult
from benchmarks.common.scope_gen import make_scope, marker_for, new_run_id
from benchmarks.common.timing import timed


@dataclass
class _WorkerResult:
    write_ops: int
    read_assertions: int
    leakage_incidents: int


def _worker(
    store: MemoryStore,
    embedding_provider,
    scope,
    own_marker: str,
    all_markers: list[str],
    ops_per_worker: int,
) -> _WorkerResult:
    write_ops = 0
    for i in range(ops_per_worker):
        store.remember(
            WorkingMemory(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                thread_id=scope.thread_id,
                content=f"{own_marker} synthetic isolation-load content, turn {i}.",
            ),
            scope,
        )
        write_ops += 1

    fetch_limit = max(50, ops_per_worker * 2)
    query_embedding = embedding_provider(own_marker)
    search_results = store.working.search(
        query_embedding=query_embedding, scope=scope, top_k=fetch_limit
    )
    list_results = store.working.list_all(scope=scope, limit=fetch_limit)

    read_assertions = 0
    leakage_incidents = 0
    for results in (search_results, list_results):
        for record in results:
            read_assertions += 1
            # Defense-in-depth: the returned row's own scope fields must
            # match the scope we queried with, independent of content.
            if record.agent_id != scope.agent_id or record.tenant_id != scope.tenant_id:
                leakage_incidents += 1
                continue
            for other_marker in all_markers:
                if other_marker != own_marker and other_marker in record.content:
                    leakage_incidents += 1

    return _WorkerResult(
        write_ops=write_ops,
        read_assertions=read_assertions,
        leakage_incidents=leakage_incidents,
    )


def run_isolation_load(
    store: MemoryStore,
    embedding_provider,
    tenants: int = 10,
    agents_per_tenant: int = 2,
    concurrent_workers: int = 20,
    ops_per_worker: int = 5,
) -> IsolationLoadResult:
    """Execute the isolation-under-load suite and return the aggregated result.

    Args:
        store:              A live ``MemoryStore`` (real Db2 connection pool
                             — size it to at least ``concurrent_workers``,
                             or expect queueing rather than failure, since
                             ``ConnectionPool.get_connection()`` blocks up to
                             ``pool_timeout`` for a free slot).
        embedding_provider:  Callable ``text -> list[float]``.
        tenants:             Number of synthetic tenants.
        agents_per_tenant:   Number of synthetic agents per tenant.
        concurrent_workers:  Max concurrent threads hammering the store at once.
        ops_per_worker:      Number of ``remember()`` writes per (tenant, agent)
                             scope before it reads its own scope back.
    """
    run_id = new_run_id()
    scopes = [
        make_scope(run_id, tenant_index=t, agent_index=a, user_index=a, thread_index=a)
        for t in range(tenants)
        for a in range(agents_per_tenant)
    ]
    markers = [marker_for(s) for s in scopes]

    total_write_ops = 0
    total_read_assertions = 0
    total_leakage = 0

    with (
        timed() as elapsed,
        concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_workers) as executor,
    ):
        futures = [
            executor.submit(
                _worker, store, embedding_provider, scope, marker, markers, ops_per_worker
            )
            for scope, marker in zip(scopes, markers, strict=True)
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            total_write_ops += result.write_ops
            total_read_assertions += result.read_assertions
            total_leakage += result.leakage_incidents

    return IsolationLoadResult(
        tenants=tenants,
        agents_per_tenant=agents_per_tenant,
        concurrent_workers=concurrent_workers,
        ops_per_worker=ops_per_worker,
        total_write_ops=total_write_ops,
        total_read_assertions=total_read_assertions,
        leakage_incidents=total_leakage,
        elapsed_s=elapsed[0] / 1000.0,
    )
