"""
agent-memory-sdk
~~~~~~~~~~~~~~~~
Governed multi-type memory system for AI agents backed by IBM Db2 LUW.

Memory types:
  - working   : raw current-session/thread turns (short-lived)
  - episodic  : summarized past runs/threads/events
  - semantic  : extracted facts + aggregated entity/user profiles
  - procedural: learned skills/instructions/how-to knowledge

Quick start::

    from agent_memory_sdk.db.connection import ConnectionPool
    pool = ConnectionPool()          # reads env vars automatically
    # Step 3+ adds: from agent_memory_sdk import MemoryStore
"""

__version__ = "0.1.0"
