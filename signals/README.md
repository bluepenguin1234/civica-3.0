# Civica Signals

Massachusetts municipal-development intelligence — a **separate pipeline that
lives alongside `pipeline/` and never touches it**. It reads town-government
documents (Planning Board / ZBA / Select Board / Conservation Commission agendas
and minutes), extracts structured development events with Claude, links them into
project "stories," and publishes a B2B "Signals" dashboard reachable from the
site's top nav. Full design + phase plan: `../civica-signals-build-spec.md`.

## Pipeline (build order — most stages arrive in later phases)

```
registry/towns.yaml      the moat: per-town board URLs + CMS platform   [Phase 1]
  -> crawl/              download new agenda/minutes PDFs -> raw/ + documents table
  -> extract/            PDF -> Claude API -> events (with source citations)
  -> link/               dedup + group events into project_stories
  -> publish/            events DB -> docs/output/signals/*.json
  -> validate_signals.py the gate (same philosophy as pipeline/validate_town.py)
  -> review/             CLI to approve/reject needs_review events
```

## Data store — `signals.db` (SQLite, gitignored)

- **documents** — one row per fetched PDF (sha256 PK, source URL, local path, status).
- **events** — the atomic unit; every event traces to a `doc_id` + page. Carries the
  contact fields (applicant / applicant_reps / job_contact / contacts_enriched).
- **project_stories** — groups events that are the same project across many meetings.

Create / inspect it (from the repo root):

```
python -m signals.db --init      # creates signals.db (WAL) and prints the schema
```

## Conventions (inherited from this repo)

- Paths resolve via `__file__` (`config.py`) so scripts run from any directory.
- **Never delete raw PDFs** — `signals/raw/` is the archive and the moat.
- Design is locked: any Signals UI imports `docs/clean.css` tokens — no new colors.
- No secrets in code; the Anthropic key is read from the environment (Phase 2).

## Status

Phase 0 only — scaffold, schema, registry format, config. `crawl/`, `extract/`,
`link/`, `publish/`, `review/`, and `validate_signals.py` are built in Phases 1–5.
