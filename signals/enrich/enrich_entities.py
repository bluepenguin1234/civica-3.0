#!/usr/bin/env python3
"""
enrich_entities.py — contact enrichment for the directory (Step 8).

Only entities on ACTIVE, non-informational stories are enriched. Every enriched
field carries source + confidence + verified date and a per-field review status,
stored in entities.enrichment (JSON). Publishing is gated: build_contacts_json.py
writes a SEPARATE contacts.json (fetched through the dashboard's checkAccess seam,
not the public feed) and includes ONLY fields whose status is auto or
human_verified. Researched website/phone land as needs_review and stay invisible
until a human approves them in signals/review/review_entities.py — a wrong phone
number is worse than none.

Strict source ladder (spec):
  1. MA Secretary of the Commonwealth corporate registry (authoritative for LLCs).
  2. The firm's own website (web search on canonical name + town/state).
  3. Phone ONLY from the firm's own site or the registry — never people-search /
     broker sites (enforced here: --ingest rejects a phone with any other source).
  4. LinkedIn: a CONSTRUCTED search URL only — never scraped. A direct profile URL
     only if the firm's own site links it.

The only fields this engine writes on its own are the two zero-risk CONSTRUCTED
search URLs (linkedin, registry); they publish without review. website/phone come
in through --ingest <file.json> from human/agent research and are held for review.

Usage (from the repo root):
    python -m signals.enrich.enrich_entities                 # refresh safe links
    python -m signals.enrich.enrich_entities --worklist      # what to research
    python -m signals.enrich.enrich_entities --ingest found.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from urllib.parse import quote_plus

from signals import db

sys.stdout.reconfigure(encoding="utf-8")

PUBLISHABLE = {"auto", "human_verified"}     # statuses that reach contacts.json
PHONE_SOURCES = {"firm_site", "ma_soc_registry"}   # never broker/people-search
WEBSITE_SOURCES = {"firm_site", "ma_soc_registry"}
INGEST_FIELDS = {"website", "phone"}


def _town_state(town_id):
    parts = town_id.split("_")
    town = parts[0].title() if parts else town_id
    state = parts[-1].upper() if len(parts) > 1 else ""
    return town, state


def linkedin_search_url(name, town, state):
    return ("https://www.linkedin.com/search/results/all/?keywords="
            + quote_plus(f"{name} {town} {state}".strip()))


def registry_search_url(name):
    # A constructed search that lands on the firm's MA registry page — not a
    # scraped value. (The registry's own search is an ASP.NET postback with no
    # stable GET, so we scope a web search to its domain.)
    return ("https://www.google.com/search?q="
            + quote_plus(f'"{name}" site:corp.sec.state.ma.us'))


def worklist(conn):
    """Entities (firm|person) with >=1 published event on an active,
    non-informational story. Public offices are excluded — the corporate
    registry / firm-site / LinkedIn ladder doesn't apply to a town department."""
    return conn.execute(
        "SELECT DISTINCT en.* FROM entities en "
        "JOIN event_entities ee ON ee.entity_id = en.entity_id "
        "JOIN events e ON e.event_id = ee.event_id "
        "JOIN project_stories s ON s.story_id = e.story_id "
        "WHERE en.kind IN ('firm','person') "
        "AND e.review_status IN ('auto_approved','human_approved') "
        "AND e.superseded_by IS NULL "
        "AND s.status = 'active' "
        "AND s.current_stage IS NOT 'informational' "
        "ORDER BY en.kind, en.canonical_name").fetchall()


def refresh_safe_links(conn, today):
    """Set/refresh the two constructed-search-URL fields for every worklist
    entity, preserving any researched website/phone already on the record."""
    n = 0
    for ent in worklist(conn):
        town, state = _town_state(ent["town_scope"])
        enr = json.loads(ent["enrichment"]) if ent["enrichment"] else {}
        enr["linkedin"] = {
            "value": linkedin_search_url(ent["canonical_name"], town, state),
            "kind": "search", "source": "constructed_search",
            "confidence": 1.0, "verified": today, "status": "auto"}
        if ent["kind"] == "firm":
            enr["registry"] = {
                "value": registry_search_url(ent["canonical_name"]),
                "kind": "search", "source": "constructed_search",
                "confidence": 1.0, "verified": today, "status": "auto"}
        conn.execute("UPDATE entities SET enrichment=? WHERE entity_id=?",
                     (json.dumps(enr, ensure_ascii=False), ent["entity_id"]))
        n += 1
    conn.commit()
    return n


def ingest(conn, path, today):
    """Load researched website/phone values as needs_review. Input JSON is a list
    of {entity_id, field, value, source, confidence}. Phone/website sources are
    restricted to the firm's own site or the registry — anything else is refused.
    """
    with open(path, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    added = skipped = 0
    for it in items:
        eid = it.get("entity_id")
        field = it.get("field")
        value = (it.get("value") or "").strip()
        source = it.get("source")
        if field not in INGEST_FIELDS or not eid or not value:
            print(f"   ! skip (bad shape): {it}")
            skipped += 1
            continue
        allowed = PHONE_SOURCES if field == "phone" else WEBSITE_SOURCES
        if source not in allowed:
            print(f"   ! REFUSED {field} for {eid}: source {source!r} not in {sorted(allowed)} "
                  f"(never people-search/broker)")
            skipped += 1
            continue
        row = conn.execute("SELECT enrichment, canonical_name FROM entities WHERE entity_id=?",
                           (eid,)).fetchone()
        if not row:
            print(f"   ! skip (unknown entity): {eid}")
            skipped += 1
            continue
        enr = json.loads(row["enrichment"]) if row["enrichment"] else {}
        enr[field] = {"value": value, "source": source,
                      "confidence": float(it.get("confidence", 0.7)),
                      "verified": today, "status": "needs_review"}
        conn.execute("UPDATE entities SET enrichment=? WHERE entity_id=?",
                     (json.dumps(enr, ensure_ascii=False), eid))
        print(f"   + {field} for {row['canonical_name']} (needs_review): {value}")
        added += 1
    conn.commit()
    return added, skipped


def print_worklist(conn):
    rows = worklist(conn)
    print(f"=== enrichment worklist: {len(rows)} entit(y/ies) on active, "
          f"non-informational stories ===")
    for ent in rows:
        town, state = _town_state(ent["town_scope"])
        print(f"\n[{ent['kind']}] {ent['canonical_name']}  ({ent['entity_id']})")
        if ent["kind"] == "firm":
            print(f"    registry: {registry_search_url(ent['canonical_name'])}")
        print(f"    linkedin: {linkedin_search_url(ent['canonical_name'], town, state)}")
        print("    -> research website/phone from the registry or the firm's own "
              "site ONLY, then --ingest")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Civica Signals contact enrichment (Step 8).")
    parser.add_argument("--worklist", action="store_true",
                        help="print what needs researching (no DB writes)")
    parser.add_argument("--ingest", metavar="FILE",
                        help="load researched website/phone JSON as needs_review")
    args = parser.parse_args(argv)

    conn = db.init_db()
    today = dt.date.today().isoformat()

    if args.worklist:
        print_worklist(conn)
        conn.close()
        return
    if args.ingest:
        added, skipped = ingest(conn, args.ingest, today)
        print(f"ingest: {added} field(s) loaded as needs_review, {skipped} skipped.")
        print("review + approve with: python -m signals.review.review_entities")
        conn.close()
        return

    n = refresh_safe_links(conn, today)
    pend = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE enrichment LIKE '%needs_review%'").fetchone()[0]
    print(f"=== enrichment safe-links pass ===")
    print(f"refreshed constructed search URLs (linkedin/registry) on {n} worklist entit(y/ies).")
    print(f"entities with researched fields still pending review: {pend}")
    print("research targets: python -m signals.enrich.enrich_entities --worklist")
    conn.close()


if __name__ == "__main__":
    main()
