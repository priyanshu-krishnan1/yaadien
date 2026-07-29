-- Migration 0001: schema_migrations tracking table
-- This table must exist before any other migration runs.
-- The migrate.py runner creates it on first run if absent,
-- but keeping it as migration 0001 makes the state self-documenting.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version      VARCHAR(255) NOT NULL,
    applied_at   TIMESTAMP    NOT NULL DEFAULT CURRENT TIMESTAMP,
    CONSTRAINT pk_schema_migrations PRIMARY KEY (version)
);
