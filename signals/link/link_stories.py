#!/usr/bin/env python3
"""
link_stories.py — group events into project stories (Phase 3).

The same project surfaces across many meetings and boards over months. This
pass walks approved events chronologically and attaches each to a story in the
same town by (1) normalized street-address match, else (2) fuzzy project-name
match (rapidfuzz token_set_ratio >= 85) — address wins. No match creates a new
story. Events matching two or more candidate stories are never guessed: they go
to signals/link/ambiguous_links.txt for a human call.

Usage (from the repo root):  python -m signals.link.link_stories
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import uuid

from rapidfuzz import fuzz

from signals import db

sys.stdout.reconfigure(encoding="utf-8")

NAME_MATCH_THRESHOLD = 85
DORMANT_AFTER_DAYS = 120
AMBIGUOUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "ambiguous_links.txt")

# Common street-suffix abbreviations, both directions -> one canonical token.
_SUFFIXES = {
    "st": "street", "rd": "road", "ave": "avenue", "av": "avenue",
    "dr": "drive", "ln": "lane", "ct": "court", "cir": "circle",
    "hwy": "highway", "pkwy": "parkway", "pl": "place", "sq": "square",
    "ter": "terrace", "blvd": "boulevard", "wy": "way",
}


_TRAILING_NOISE = {"ma", "mass", "massachusetts", "usa"}


def normalize_address(addr: str | None, town_id: str | None = None) -> str | None:
    if not addr:
        return None
    addr = re.sub(r"\([^)]*\)", " ", addr.lower())  # drop parcel refs like "(Map 40, Lot 14)"
    tokens = re.sub(r"[^a-z0-9 ]", " ", addr).split()
    tokens = [_SUFFIXES.get(t, t) for t in tokens]
    # Strip the trailing ", <Town>, MA"-style suffix (candidates are already town-scoped).
    drop = _TRAILING_NOISE | set(town_id.split("_")[:-1] if town_id else ())
    while tokens and tokens[-1] in drop:
        tokens.pop()
    return " ".join(tokens) or None


def best_display_name(ev) -> str:
    return (ev["project_name"] or ev["address"]
            or f"{ev['event_type']} ({ev['meeting_date']})")


def candidate_stories(ev, stories):
    """Return (matches, how) — stories in the same town this event could join."""
    addr = normalize_address(ev["address"], ev["town_id"])
    if addr:
        hits = [s for s in stories.values()
                if s["town_id"] == ev["town_id"] and s["_addr_norm"] == addr]
        if hits:
            return hits, "address"
    name = ev["project_name"]
    if name:
        hits = [s for s in stories.values()
                if s["town_id"] == ev["town_id"] and s["canonical_name"]
                and fuzz.token_set_ratio(name, s["canonical_name"]) >= NAME_MATCH_THRESHOLD]
        if hits:
            return hits, "name"
    return [], None


def supersede_agenda_echoes(conn):
    """Merge agenda echoes: within a story, an agenda event sharing a
    (board, meeting_date) with a minutes event is superseded by that minutes
    event. The agenda event stays in the DB but leaves the feed; the minutes
    event carries both source links. Idempotent (recomputed each run).
    """
    conn.execute("UPDATE events SET superseded_by = NULL")
    rows = conn.execute(
        "SELECT e.event_id, e.story_id, e.board_id, e.meeting_date, d.doc_type "
        "FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.story_id IS NOT NULL "
        "AND e.review_status IN ('auto_approved', 'human_approved')").fetchall()
    groups = {}
    for r in rows:
        if r["doc_type"] in ("agenda", "minutes"):
            key = (r["story_id"], r["board_id"], r["meeting_date"])
            groups.setdefault(key, {"agenda": [], "minutes": []})[r["doc_type"]].append(r["event_id"])
    superseded = 0
    for g in groups.values():
        if g["agenda"] and g["minutes"]:
            survivor = g["minutes"][0]
            for agenda_id in g["agenda"]:
                conn.execute("UPDATE events SET superseded_by = ? WHERE event_id = ?",
                             (survivor, agenda_id))
                superseded += 1
    return superseded


def _bid_title(r):
    return (r["project_name"] or (r["summary"] or "")[:90] or "").lower().strip()


def supersede_duplicate_bids(conn):
    """Cross-source bid dedup (Step S1). The same solicitation can appear on a
    town's own bids page and on COMMBUYS. Within a town, two published bid_rfp
    events whose titles fuzzy-match >= 85 (same convention as story linking)
    collapse to one published card carrying both source links — the COMMBUYS copy
    (richer, real contact info) survives. Idempotent; runs after agenda merging so
    it never resets that. Returns the number merged."""
    rows = conn.execute(
        "SELECT e.event_id, e.town_id, e.project_name, e.summary, d.board_id "
        "FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.event_type='bid_rfp' AND e.superseded_by IS NULL "
        "AND e.review_status IN ('auto_approved','human_approved')").fetchall()
    by_town = {}
    for r in rows:
        by_town.setdefault(r["town_id"], []).append(r)
    merged = 0
    for bids in by_town.values():
        used = set()
        for i in range(len(bids)):
            if bids[i]["event_id"] in used:
                continue
            for j in range(i + 1, len(bids)):
                if bids[j]["event_id"] in used:
                    continue
                ti, tj = _bid_title(bids[i]), _bid_title(bids[j])
                if ti and tj and fuzz.token_set_ratio(ti, tj) >= NAME_MATCH_THRESHOLD:
                    survivor, dup = bids[i], bids[j]
                    if bids[j]["board_id"] == "commbuys" and bids[i]["board_id"] != "commbuys":
                        survivor, dup = bids[j], bids[i]
                    conn.execute("UPDATE events SET superseded_by=? WHERE event_id=?",
                                 (survivor["event_id"], dup["event_id"]))
                    used.add(dup["event_id"])
                    merged += 1
    return merged


def refresh_story(conn, story_id):
    """Recompute a story's rollup fields from its (non-superseded) events."""
    evs = conn.execute(
        "SELECT meeting_date, stage, residential_units FROM events "
        "WHERE story_id=? AND superseded_by IS NULL ORDER BY meeting_date",
        (story_id,)).fetchall()
    if not evs:
        return
    latest = evs[-1]
    units = max((e["residential_units"] for e in evs
                 if e["residential_units"] is not None), default=None)
    status = "active"
    if latest["stage"] in ("denied", "withdrawn"):
        status = "dead"
    else:
        last = dt.date.fromisoformat(latest["meeting_date"])
        if (dt.date.today() - last).days > DORMANT_AFTER_DAYS:
            status = "dormant"
    conn.execute(
        "UPDATE project_stories SET current_stage=?, first_seen=?, last_activity=?, "
        "total_units=?, status=? WHERE story_id=?",
        (latest["stage"], evs[0]["meeting_date"], latest["meeting_date"],
         units, status, story_id))


def main():
    conn = db.init_db()

    # Stories already in the DB (idempotent across runs), with normalized addresses.
    stories = {}
    for s in conn.execute("SELECT * FROM project_stories"):
        d = dict(s)
        d["_addr_norm"] = normalize_address(s["canonical_address"], s["town_id"])
        stories[s["story_id"]] = d

    events = conn.execute(
        "SELECT * FROM events WHERE story_id IS NULL "
        "AND review_status IN ('auto_approved', 'human_approved') "
        "ORDER BY meeting_date ASC, created_at ASC").fetchall()

    linked = created = 0
    ambiguous = []
    touched = set()

    for ev in events:
        matches, how = candidate_stories(ev, stories)
        if len(matches) > 1:
            ambiguous.append((ev, matches))
            continue
        if matches:
            story_id = matches[0]["story_id"]
            linked += 1
        else:
            story_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO project_stories (story_id, town_id, canonical_name, "
                "canonical_address, current_stage, first_seen, last_activity, "
                "total_units, status) VALUES (?,?,?,?,?,?,?,?, 'active')",
                (story_id, ev["town_id"], best_display_name(ev), ev["address"],
                 ev["stage"], ev["meeting_date"], ev["meeting_date"],
                 ev["residential_units"]))
            stories[story_id] = {
                "story_id": story_id, "town_id": ev["town_id"],
                "canonical_name": best_display_name(ev),
                "canonical_address": ev["address"],
                "_addr_norm": normalize_address(ev["address"], ev["town_id"]),
            }
            created += 1
        conn.execute("UPDATE events SET story_id=? WHERE event_id=?",
                     (story_id, ev["event_id"]))
        touched.add(story_id)

    superseded = supersede_agenda_echoes(conn)  # merge agenda echoes (Step 2)
    bid_dups = supersede_duplicate_bids(conn)    # cross-source bid dedup (Step S1)
    for story_id in stories:  # refresh all (also re-applies dormant/dead rules)
        refresh_story(conn, story_id)
    conn.commit()

    if ambiguous:
        with open(AMBIGUOUS_FILE, "w", encoding="utf-8") as fh:
            for ev, matches in ambiguous:
                fh.write(f"event {ev['event_id']} ({ev['town_id']} {ev['meeting_date']} "
                         f"{ev['event_type']}): {ev['summary'][:120]}\n")
                for m in matches:
                    fh.write(f"    candidate story {m['story_id']}: "
                             f"{m['canonical_name']} @ {m['canonical_address']}\n")
                fh.write("\n")

    n_stories = conn.execute("SELECT COUNT(*) FROM project_stories").fetchone()[0]
    multi = conn.execute(
        "SELECT COUNT(*) FROM (SELECT story_id FROM events WHERE story_id IS NOT NULL "
        "AND superseded_by IS NULL GROUP BY story_id HAVING COUNT(*) > 1)").fetchone()[0]
    print("=== story linking report ===")
    print(f"events processed: {len(events)} | linked to existing stories: {linked} "
          f"| new stories: {created}")
    print(f"agenda echoes superseded by their minutes twin: {superseded}")
    print(f"duplicate bids merged across sources (town page <-> COMMBUYS): {bid_dups}")
    print(f"stories total: {n_stories} ({multi} with 2+ events)")
    if ambiguous:
        print(f"AMBIGUOUS: {len(ambiguous)} event(s) matched 2+ stories — "
              f"review {AMBIGUOUS_FILE} (left unlinked, not guessed)")
    else:
        print("ambiguous cases: 0")
    print("\nmulti-event stories:")
    for s in conn.execute(
            "SELECT s.story_id, s.canonical_name, s.canonical_address, s.current_stage, "
            "s.status, COUNT(e.event_id) AS n FROM project_stories s "
            "JOIN events e ON e.story_id = s.story_id "
            "WHERE e.superseded_by IS NULL "
            "GROUP BY s.story_id HAVING n > 1 ORDER BY n DESC"):
        print(f"  [{s['n']} events] {s['canonical_name']} "
              f"@ {s['canonical_address']} -> {s['current_stage']} ({s['status']})")
    conn.close()


if __name__ == "__main__":
    main()
