"""
exceptions.py
~~~~~~~~~~~~~
Custom exception classes for agent-memory-sdk.
"""

from __future__ import annotations


class StaleWriteError(Exception):
    """Raised when an optimistic-concurrency update is rejected.

    The ``update()`` method on every repository conditions the UPDATE on
    ``version = record.version``.  If the row has been modified by another
    writer between the caller's ``get_by_id()`` and their ``update()`` call,
    the UPDATE affects 0 rows and :class:`StaleWriteError` is raised.

    The caller should re-fetch the latest row with ``get_by_id()``, apply
    their changes to the fresh copy, and retry the ``update()``.

    Example::

        for attempt in range(3):
            record = repo.get_by_id(record_id, scope)
            if record is None:
                break
            record.content = new_content
            try:
                repo.update(record, scope)
                break                       # success
            except StaleWriteError:
                continue                    # retry with refreshed record
        else:
            raise RuntimeError("Could not update after 3 attempts")
    """


class InvalidMetadataFilterError(ValueError):
    """Raised when a ``metadata_filter`` dict contains an unrecognised operator key.

    Only the following operator keys are recognized inside a field's value dict:
    ``$not``, ``$array_contains``, ``$array_contains_any``.  Any other key that
    starts with ``$`` is rejected immediately rather than silently ignored.

    Example of an **invalid** filter (``$in`` is not supported)::

        store.working.list_all(scope, metadata_filter={"status": {"$in": ["a", "b"]}})
        # → raises InvalidMetadataFilterError: unrecognized operator '$in' on field 'status'

    Supported operators:
        - Exact match:           ``{"field": "value"}``
        - ``$not``:              ``{"field": {"$not": "value"}}``
        - ``$array_contains``:   ``{"field": {"$array_contains": "value"}}``
        - ``$array_contains_any``: ``{"field": {"$array_contains_any": ["a", "b"]}}``
    """


class ScopeMismatchError(ValueError):
    """Raised by :meth:`~agent_memory_sdk.store.MemoryStore.import_scope` when an
    imported record's scope columns don't match the target scope.

    ``BaseRepository.create()`` unconditionally overwrites a record's
    ``tenant_id``/``agent_id``/``user_id``/``thread_id`` fields with the
    *target* scope's values before inserting — so without an explicit check,
    importing a record captured under one scope (e.g. ``user_id="alice"``)
    into a different target scope (e.g. ``user_id="bob"``) would silently
    rewrite the record's scope rather than surfacing the mismatch.
    :meth:`~agent_memory_sdk.store.MemoryStore.import_scope` checks every
    record's scope against the target scope *before* calling ``create()``
    (or ``insert_chunk()`` for ``memory_chunks`` records) and raises this
    error instead of proceeding.

    The caller should either re-export from the correct scope, or call
    ``import_scope()`` once per distinct scope present in the record stream.
    """


class ScopeImportError(ScopeMismatchError):
    """Raised by :meth:`~agent_memory_sdk.store.MemoryStore.import_scope` when an
    imported record's stored scope does not match the target import scope.

    This is a subclass of :class:`ScopeMismatchError` (and therefore also a
    :class:`ValueError`) — callers that already catch :class:`ScopeMismatchError`
    will continue to catch it without modification.

    See :class:`ScopeMismatchError` for the full reasoning behind why this check
    is performed before any ``create()`` / ``insert_chunk()`` call.
    """


class SchemaPolicyError(RuntimeError):
    """Raised when :class:`~agent_memory_sdk.db.migrate.SchemaPolicy.REQUIRE_EXISTING`
    validation fails.

    The error message lists every missing table, column, and index so the DBA
    can provision them in a single pass before restarting the application.

    Example::

        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.db.connection import ConnectionPool

        pool = ConnectionPool()
        try:
            Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).validate()
        except SchemaPolicyError as exc:
            # exc.args[0] contains the full list of missing objects
            sys.exit(str(exc))
    """
