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


def main():
    conn = db.init_db()
    with open(config.REGISTRY_PATH, "r", encoding="utf-8") as fh:
        registry = yaml.safe_load(fh) or []
    towns = {t["town_id"]: t for t in registry}
    board_names = {(t["town_id"], b["board_id"]): b.get("name", b["board_id"])
                   for t in registry for b in t.get("boards", [])}
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
        "SELECT e.*, d.source_url FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.review_status IN ('auto_approved','human_approved') "
        "ORDER BY e.meeting_date DESC, e.created_at DESC").fetchall()

    events = []
    for e in rows:
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
        })

    stories = []
    for s in conn.execute("SELECT * FROM project_stories ORDER BY last_activity DESC"):
        member_events = [ev for ev in events if ev["story_id"] == s["story_id"]]
        if not member_events:
            continue  # stories whose events were all rejected/unpublished
        t = towns.get(s["town_id"], {})
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
            "events": [{"event_id": ev["event_id"], "date": ev["date"],
                        "board": ev["board"], "event_type": ev["event_type"],
                        "stage": ev["stage"], "summary": ev["summary"],
                        "source_url": ev["source_url"]}
                       for ev in sorted(member_events, key=lambda x: x["date"] or "")],
        })

    upcoming = [ev for ev in events if ev["date"] and ev["date"] >= today.isoformat()]

    feed = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "coverage": coverage,
        "stories": stories,
        "events": events,
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
            "upcoming": [e for e in upcoming if e["place_fips"] == fips],
        }
        with open(os.path.join(config.PUBLISH_DIR, f"{fips}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(town_feed, fh, ensure_ascii=False, indent=1)
        print(f"{fips}.json ({cov['name']}): {len(town_feed['events'])} events")

    conn.close()


if __name__ == "__main__":
    main()
