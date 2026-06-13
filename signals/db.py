#!/usr/bin/env python3
"""
db.py — SQLite store for the Civica Signals subsystem.

Three tables (schema = civica-signals-build-spec.md sections 2.2 / 2.3 / 2.4):
  documents        the raw-PDF manifest; one row per fetched document.
  events           the atomic product unit; every event traces to a doc_id + page.
  project_stories  groups events that are the same project across many meetings.

The events table also carries contacts_enriched (web-lookup contacts populated in
Phase 2.4); it is declared now so no schema migration is needed later.

Smoke test / first run (from the repo root):
    python -m signals.db --init
creates signals.db (WAL mode) and prints the schema.
"""

import argparse
import sqlite3
import sys

from signals import config

sys.stdout.reconfigure(encoding="utf-8")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id            TEXT PRIMARY KEY,                  -- sha256 of the file bytes
    town_id           TEXT NOT NULL,                     -- FK -> registry/towns.yaml
    board_id          TEXT,
    doc_type          TEXT,                              -- agenda | minutes | packet | other
    meeting_date      TEXT,                              -- ISO date (nullable)
    source_url        TEXT,
    local_path        TEXT,                              -- signals/raw/{town_id}/{board_id}/{filename}
    fetched_at        TEXT,
    processed_at      TEXT,                              -- null until extraction runs
    extraction_status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | skipped_scan
    page_count        INTEGER,
    is_scanned        INTEGER NOT NULL DEFAULT 0         -- 1 if image-only PDF (needs OCR path)
);

CREATE TABLE IF NOT EXISTS project_stories (
    story_id          TEXT PRIMARY KEY,
    town_id           TEXT NOT NULL,
    canonical_name    TEXT,
    canonical_address TEXT,
    current_stage     TEXT,
    first_seen        TEXT,
    last_activity     TEXT,
    total_units       INTEGER,
    status            TEXT NOT NULL DEFAULT 'active'     -- active | dormant | completed | dead
);

CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT PRIMARY KEY,                  -- uuid
    doc_id            TEXT NOT NULL REFERENCES documents(doc_id),  -- every event traces to a source doc
    town_id           TEXT NOT NULL,
    board_id          TEXT,
    meeting_date      TEXT,
    event_type        TEXT NOT NULL,                     -- see enum in the spec / extraction prompt
    project_name      TEXT,
    address           TEXT,
    applicant         TEXT,                              -- developer / owner / petitioner, as named
    applicant_reps    TEXT,                              -- JSON: [{role, name, firm}]
    job_contact       TEXT,                              -- JSON: {role, name, org, contact_info, source}
    contacts_enriched TEXT,                              -- JSON: web-lookup contacts (Phase 2.4); kept separate from doc-sourced fields
    residential_units INTEGER,
    commercial_sqft   INTEGER,
    dollar_value      INTEGER,                           -- only if stated in the document
    stage             TEXT,                              -- proposed|hearing|continued|approved|denied|withdrawn|permitted|under_construction|informational
    summary           TEXT,                              -- 1-3 sentence plain-language summary
    source_page       INTEGER,                           -- page in the source PDF
    confidence        REAL,                              -- 0-1 from extraction
    story_id          TEXT REFERENCES project_stories(story_id),   -- set in Phase 3
    created_at        TEXT,
    review_status     TEXT NOT NULL DEFAULT 'needs_review',  -- auto_approved | needs_review | human_approved | rejected
    owner             TEXT,                              -- property owner as named, when distinct from applicant
    next_date         TEXT,                              -- ISO continuation/next-hearing/bid-due date, ONLY if stated
    trades            TEXT,                              -- JSON array from the closed trade vocabulary (extract.TRADES)
    is_public_work    INTEGER,                           -- 1 when the buyer is the town/district
    tenure            TEXT,                              -- rental | ownership | unknown (housing only, never guessed)
    superseded_by     TEXT                               -- event_id of the minutes twin that merged this agenda echo (Phase/Step 2); set in link stage
);

CREATE INDEX IF NOT EXISTS idx_events_town_date ON events(town_id, meeting_date);
CREATE INDEX IF NOT EXISTS idx_events_story     ON events(story_id);
"""


def connect():
    """Open signals.db with WAL journaling and foreign-key enforcement on."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# MIGRATIONS: columns added after the initial schema shipped. CREATE TABLE
# above includes them for fresh databases; ALTER covers existing ones.
_EVENT_MIGRATIONS = (
    ("owner", "TEXT"),
    ("next_date", "TEXT"),
    ("trades", "TEXT"),
    ("is_public_work", "INTEGER"),
    ("tenure", "TEXT"),
    ("superseded_by", "TEXT"),
)


def init_db():
    """Create the schema if needed (idempotent) and return an open connection."""
    conn = connect()
    conn.executescript(SCHEMA)
    for col, typ in _EVENT_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


def print_schema(conn):
    """Print the DDL of every table and index currently in the database."""
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL ORDER BY type DESC, name;"
    ).fetchall()
    for row in rows:
        print(f"\n-- {row['type']}: {row['name']}")
        print(row["sql"].strip() + ";")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Civica Signals SQLite store.")
    parser.add_argument(
        "--init", action="store_true",
        help="create signals.db (if needed) and print the schema",
    )
    args = parser.parse_args(argv)

    if not args.init:
        parser.error("nothing to do — pass --init to create the database")

    conn = init_db()
    journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    print(f"Initialized {config.DB_PATH}  (journal_mode={journal})")
    print_schema(conn)
    conn.close()


if __name__ == "__main__":
    main()
