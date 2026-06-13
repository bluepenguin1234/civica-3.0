#!/usr/bin/env python3
"""
build_signals_json.py — publish approved events + stories to the static site.

Writes docs/output/signals/:
  feed.json          the combined dashboard feed (coverage, stories with event
                     timelines, all approved events newest-first, upcoming items)
  {place_fips}.json  the same shape filtered to one town

Only approved events (auto_approved | human_approved) are published; every one
carries its source_url back to the town website. Rejected and needs_review
events never leave the database.

Usage (from the repo root):  python -m signals.publish.build_signals_json
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

import yaml

from signals import config, db

sys.stdout.reconfigure(encoding="utf-8")


def _j(value):
    """Parse a stored JSON string field, or pass None through."""
    return json.loads(value) if value else None


_SOURCE_LABELS = {"commbuys": "COMMBUYS", "bids": "Town bids"}


def _source_label(board_id, doc_type):
    """Human label for a source link, naming where the record came from (Step S1)."""
    if board_id in _SOURCE_LABELS:
        return _SOURCE_LABELS[board_id]
    return {"agenda": "Agenda", "minutes": "Minutes", "packet": "Packet",
            "bid": "Bid"}.get(doc_type, doc_type or "source")


def _dedupe_contacts(items):
    """Collapse an event's mentions to one entry per entity (merge roles/sources)."""
    out = {}
    for c in items:
        e = out.get(c["entity_id"])
        if e is None:
            out[c["entity_id"]] = {"entity_id": c["entity_id"], "name": c["name"],
                                   "kind": c["kind"], "roles": [c["role"]],
                                   "sources": [c["source"]]}
        else:
            if c["role"] not in e["roles"]:
                e["roles"].append(c["role"])
            if c["source"] not in e["sources"]:
                e["sources"].append(c["source"])
    return list(out.values())


def main():
    conn = db.init_db()
    with open(config.REGISTRY_PATH, "r", encoding="utf-8") as fh:
        registry = yaml.safe_load(fh) or []
    towns = {t["town_id"]: t for t in registry}
    board_names = {(t["town_id"], b["board_id"]): b.get("name", b["board_id"])
                   for t in registry for b in t.get("boards", [])}
    for t in registry:  # the bid sources are not registry boards
        board_names[(t["town_id"], "bids")] = "Bids & RFPs"
        board_names[(t["town_id"], "commbuys")] = "COMMBUYS"
    today = dt.date.today()

    coverage = []
    for t in registry:
        if t.get("status") != "active":
            continue
        latest = conn.execute(
            "SELECT MAX(fetched_at) FROM documents WHERE town_id=?",
            (t["town_id"],)).fetchone()[0]
        coverage.append({
            "town_id": t["town_id"],
            "name": t.get("name", t["town_id"]),
            "place_fips": str(t.get("place_fips", "")),
            "doc_freshness_days": (today - dt.date.fromisoformat(latest[:10])).days
                                  if latest else None,
        })

    rows = conn.execute(
        "SELECT e.*, d.source_url, d.doc_type FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.review_status IN ('auto_approved','human_approved') "
        "ORDER BY e.meeting_date DESC, e.created_at DESC").fetchall()

    # Agenda echoes merged in the link stage carry superseded_by -> their minutes
    # twin. They leave the feed; the surviving event carries both source links.
    children = {}
    for r in rows:
        if r["superseded_by"]:
            children.setdefault(r["superseded_by"], []).append(r)

    events = []
    for e in rows:
        if e["superseded_by"]:
            continue  # merged into its minutes twin
        sources = [{"kind": e["doc_type"], "url": e["source_url"],
                    "label": _source_label(e["board_id"], e["doc_type"])}]
        for child in children.get(e["event_id"], []):
            sources.append({"kind": child["doc_type"], "url": child["source_url"],
                            "label": _source_label(child["board_id"], child["doc_type"])})
        t = towns.get(e["town_id"], {})
        events.append({
            "event_id": e["event_id"],
            "date": e["meeting_date"],
            "town_id": e["town_id"],
            "town": t.get("name", e["town_id"]),
            "place_fips": str(t.get("place_fips", "")),
            "board": board_names.get((e["town_id"], e["board_id"]), e["board_id"]),
            "event_type": e["event_type"],
            "stage": e["stage"],
            "project_name": e["project_name"],
            "address": e["address"],
            "applicant": e["applicant"],
            "applicant_reps": _j(e["applicant_reps"]),
            "job_contact": _j(e["job_contact"]),
            "contacts_enriched": _j(e["contacts_enriched"]),
            "residential_units": e["residential_units"],
            "commercial_sqft": e["commercial_sqft"],
            "dollar_value": e["dollar_value"],
            "summary": e["summary"],
            "story_id": e["story_id"],
            "source_url": e["source_url"],
            "source_page": e["source_page"],
            "confidence": e["confidence"],
            "owner": e["owner"],
            "next_date": e["next_date"],
            "trades": _j(e["trades"]) or [],
            "is_public_work": bool(e["is_public_work"]),
            "tenure": e["tenure"],
            "source_kind": e["doc_type"],
            "sources": sources,
        })

    # --- Entities + contacts (Step 7) ---------------------------------------
    # Resolved parties (signals.enrich.resolve_entities) become a directory: each
    # event/story carries a deduped contacts[] and the feed carries an entities[]
    # the dashboard turns into clickable entity pages.
    pub_ids = {e["event_id"] for e in events}
    entities_by_id, contacts_by_event = {}, {}
    for r in conn.execute("SELECT ee.event_id, ee.role, ee.source, en.* "
                          "FROM event_entities ee JOIN entities en "
                          "ON en.entity_id = ee.entity_id"):
        if r["event_id"] not in pub_ids:
            continue
        eid = r["entity_id"]
        contacts_by_event.setdefault(r["event_id"], []).append(
            {"entity_id": eid, "name": r["canonical_name"], "kind": r["kind"],
             "role": r["role"], "source": r["source"]})
        if eid not in entities_by_id:
            t = towns.get(r["town_scope"], {})
            entities_by_id[eid] = {
                # Public-record directory data only. Enriched contact fields
                # (website/phone/linkedin) live in the gated contacts.json.
                "entity_id": eid, "name": r["canonical_name"], "kind": r["kind"],
                "town_id": r["town_scope"], "town": t.get("name", r["town_scope"]),
                "place_fips": str(t.get("place_fips", "")),
                "review_status": r["review_status"],
                "_roles": set(), "_stories": set(),
            }
        entities_by_id[eid]["_roles"].add(r["role"])

    for e in events:  # attach deduped contacts; tally each entity's stories
        e["contacts"] = _dedupe_contacts(contacts_by_event.get(e["event_id"], []))
        if e["story_id"]:
            for c in e["contacts"]:
                entities_by_id[c["entity_id"]]["_stories"].add(e["story_id"])

    entities = []
    for ent in entities_by_id.values():
        ent["roles"] = sorted(ent.pop("_roles"))
        ent["story_ids"] = sorted(ent.pop("_stories"))
        ent["n_projects"] = len(ent["story_ids"])
        entities.append(ent)
    entities.sort(key=lambda x: (-x["n_projects"], x["name"].lower()))

    stories = []
    for s in conn.execute("SELECT * FROM project_stories ORDER BY last_activity DESC"):
        member_events = [ev for ev in events if ev["story_id"] == s["story_id"]]
        if not member_events:
            continue  # stories whose events were all rejected/unpublished
        t = towns.get(s["town_id"], {})
        story_contacts = _dedupe_contacts(
            [{"entity_id": c["entity_id"], "name": c["name"], "kind": c["kind"],
              "role": c["roles"][0], "source": c["sources"][0]}
             for ev in member_events for c in ev["contacts"]])
        stories.append({
            "story_id": s["story_id"],
            "town_id": s["town_id"],
            "town": t.get("name", s["town_id"]),
            "place_fips": str(t.get("place_fips", "")),
            "name": s["canonical_name"],
            "address": s["canonical_address"],
            "current_stage": s["current_stage"],
            "total_units": s["total_units"],
            "first_seen": s["first_seen"],
            "last_activity": s["last_activity"],
            "status": s["status"],
            "brief": _j(s["brief"]),
            "contacts": story_contacts,
            "events": [{"event_id": ev["event_id"], "date": ev["date"],
                        "board": ev["board"], "event_type": ev["event_type"],
                        "stage": ev["stage"], "summary": ev["summary"],
                        "source_url": ev["source_url"], "sources": ev["sources"]}
                       for ev in sorted(member_events, key=lambda x: x["date"] or "")],
        })

    upcoming = [ev for ev in events if ev["date"] and ev["date"] >= today.isoformat()]

    feed = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "coverage": coverage,
        "stories": stories,
        "events": events,
        "entities": entities,
        "upcoming": upcoming,
    }

    os.makedirs(config.PUBLISH_DIR, exist_ok=True)
    feed_path = os.path.join(config.PUBLISH_DIR, "feed.json")
    with open(feed_path, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, ensure_ascii=False, indent=1)
    print(f"feed.json: {len(events)} events, {len(stories)} stories, "
          f"{len(coverage)} towns, {len(upcoming)} upcoming "
          f"({os.path.getsize(feed_path):,} bytes)")

    for cov in coverage:
        fips = cov["place_fips"]
        if not fips:
            continue
        town_feed = {
            "generated_at": feed["generated_at"],
            "coverage": [cov],
            "stories": [s for s in stories if s["place_fips"] == fips],
            "events": [e for e in events if e["place_fips"] == fips],
            "entities": [en for en in entities if en["place_fips"] == fips],
            "upcoming": [e for e in upcoming if e["place_fips"] == fips],
        }
        with open(os.path.join(config.PUBLISH_DIR, f"{fips}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(town_feed, fh, ensure_ascii=False, indent=1)
        print(f"{fips}.json ({cov['name']}): {len(town_feed['events'])} events")

    conn.close()


if __name__ == "__main__":
    main()
