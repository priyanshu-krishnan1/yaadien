"""
benchmarks/lifecycle/
~~~~~~~~~~~~~~~~~~~~~
Lifecycle & governance benchmarks (BM-10, C1–C8).

Covers:
  C1  forget()      — soft-delete tombstone latency & rows/s
  C2  purge_expired() — hard-delete of already-tombstoned rows at varying scale
  C3  erase_all()   — compliance erasure asserting completeness across all 6 tables
  C4  delete_user/delete_agent cascade over deep hierarchies
  C7  reconcile()   — soft-supersession over growing candidate-set sizes
  C8  export_scope()/import_scope() round-trip with streaming RSS validation
"""
