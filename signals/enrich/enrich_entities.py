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

# statuses that reach contacts.json. auto_verified = a researched website/phone/
# email that TWO independent agents confirmed on the firm's own site (machine-
# verified) — the only path that publishes without a human in review_entities.py.
PUBLISHABLE = {"auto", "human_verified", "auto_verified"}
PHONE_SOURCES = {"firm_site", "ma_soc_registry"}   # never broker/people-search
WEBSITE_SOURCES = {"firm_site", "ma_soc_registry"}
EMAIL_SOURCES = {"firm_site", "ma_soc_registry"}   # only a firm-site/registry email
INGEST_FIELDS = {"website", "phone", "email"}
# A machine-verified field must clear this confidence bar to auto-publish; below it
# we hold it for human review instead of trusting it. A wrong contact is worse than none.
AUTO_VERIFY_MIN_CONFIDENCE = 0.85
_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


_FIELD_SOURCES = {"website": WEBSITE_SOURCES, "phone": PHONE_SOURCES, "email": EMAIL_SOURCES}


def ingest(conn, path, today, status="needs_review"):
    """Load researched website/phone/email values. Input JSON is a list of
    {entity_id, field, value, source, confidence}. Sources are restricted to the
    firm's own site or the MA registry — anything else (broker/people-search/social)
    is refused. status='needs_review' (default, human path) holds the value invisible
    until approved; status='auto_verified' (machine-verified path) publishes it, but
    ONLY if it clears AUTO_VERIFY_MIN_CONFIDENCE — otherwise it is held for review.
    """
    with open(path, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    added = held = skipped = 0
    for it in items:
        eid = it.get("entity_id")
        field = it.get("field")
        value = (it.get("value") or "").strip()
        source = it.get("source")
        conf = float(it.get("confidence", 0.7))
        if field not in INGEST_FIELDS or not eid or not value:
            print(f"   ! skip (bad shape): {it}")
            skipped += 1
            continue
        if source not in _FIELD_SOURCES[field]:
            print(f"   ! REFUSED {field} for {eid}: source {source!r} not in "
                  f"{sorted(_FIELD_SOURCES[field])} (never people-search/broker/social)")
            skipped += 1
            continue
        if field == "email" and not _EMAIL_RE.match(value):
            print(f"   ! REFUSED email for {eid}: {value!r} is not a valid address")
            skipped += 1
            continue
        row = conn.execute("SELECT enrichment, canonical_name FROM entities WHERE entity_id=?",
                           (eid,)).fetchone()
        if not row:
            print(f"   ! skip (unknown entity): {eid}")
            skipped += 1
            continue
        # auto_verified must clear the confidence bar; otherwise hold for review.
        st = status
        if st == "auto_verified" and conf < AUTO_VERIFY_MIN_CONFIDENCE:
            st = "needs_review"
        enr = json.loads(row["enrichment"]) if row["enrichment"] else {}
        enr[field] = {"value": value, "source": source, "confidence": conf,
                      "verified": today, "status": st}
        conn.execute("UPDATE entities SET enrichment=? WHERE entity_id=?",
                     (json.dumps(enr, ensure_ascii=False), eid))
        tag = "PUBLISHED" if st in PUBLISHABLE else "held"
        print(f"   + {field} for {row['canonical_name']} ({tag} · {st} · {conf:.2f}): {value}")
        if st in PUBLISHABLE:
            added += 1
        else:
            held += 1
    conn.commit()
    return added, held, skipped


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
                        help="load researched website/phone/email JSON as needs_review (human path)")
    parser.add_argument("--ingest-verified", metavar="FILE", dest="ingest_verified",
                        help="load TWO-agent-confirmed website/phone/email JSON as auto_verified "
                             "(publishes if confidence >= the auto-verify bar)")
    args = parser.parse_args(argv)

    conn = db.init_db()
    today = dt.date.today().isoformat()

    if args.worklist:
        print_worklist(conn)
        conn.close()
        return
    if args.ingest or args.ingest_verified:
        path = args.ingest_verified or args.ingest
        status = "auto_verified" if args.ingest_verified else "needs_review"
        added, held, skipped = ingest(conn, path, today, status=status)
        print(f"ingest: {added} published, {held} held for review, {skipped} skipped/refused.")
        if held:
            print("review the held fields with: python -m signals.review.review_entities")
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
