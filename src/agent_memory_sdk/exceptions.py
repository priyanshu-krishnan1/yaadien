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
