#!/usr/bin/env python3
"""
validate_signals.py — the quality gate for the Signals subsystem.

Same philosophy as pipeline/validate_town.py: a red check means STOP and fix —
never weaken a check to make a run pass. Exit 0 = ALL GREEN; 1 = failures.

Usage (from the repo root):  python -m signals.validate_signals
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

import yaml

from signals import config, db
from signals.extract.extract import TRADES, TENURES

sys.stdout.reconfigure(encoding="utf-8")

EVENT_TYPES = {
    "residential_project", "commercial_project", "mixed_use_project",
    "subdivision", "40b_application", "zoning_amendment",
    "variance_special_permit", "tax_override_debt_exclusion",
    "infrastructure_project", "municipal_property", "master_plan_comp_plan",
    "bid_rfp", "other_notable",
}
STAGES = {
    "proposed", "hearing", "continued", "approved", "denied", "withdrawn",
    "permitted", "under_construction", "informational", None,
}
REVIEW_STATUSES = {"auto_approved", "needs_review", "human_approved", "rejected"}
STORY_STATUSES = {"active", "dormant", "completed", "dead"}

NEEDS_REVIEW_MAX_AGE_DAYS = 14
FRESHNESS_RED_DAYS = 45
FEED_PATH = os.path.join(config.PUBLISH_DIR, "feed.json")
TOWN_SCORES = os.path.join(config.REPO_ROOT, "pipeline", "town_scores.csv")

fails, warns = [], []


def check(cond, msg):
    if cond:
        print(f"  [ OK ] {msg}")
    else:
        fails.append(msg)
        print(f"  [FAIL] {msg}")


def warn(cond, msg):
    if cond:
        print(f"  [ OK ] {msg}")
    else:
        warns.append(msg)
        print(f"  [WARN] {msg}")


def main():
    conn = db.init_db()
    today = dt.date.today()

    with open(config.REGISTRY_PATH, "r", encoding="utf-8") as fh:
        registry = yaml.safe_load(fh) or []
    reg_towns = {t["town_id"]: t for t in registry}
    active_towns = [t for t in registry if t.get("status") == "active"]

    print("\n=== EVENTS ===")
    events = conn.execute("SELECT * FROM events").fetchall()
    docs = {d["doc_id"]: d for d in conn.execute("SELECT * FROM documents")}
    print(f"  events = {len(events)}, documents = {len(docs)}")

    bad_doc = [e for e in events if e["doc_id"] not in docs]
    check(not bad_doc, f"every event has a doc_id present in the manifest ({len(bad_doc)} orphans)")

    missing_files = []
    for d in docs.values():
        path = os.path.join(config.REPO_ROOT, d["local_path"].replace("/", os.sep))
        if not os.path.exists(path):
            missing_files.append(d["local_path"])
    check(not missing_files,
          f"every manifest document exists on disk ({len(missing_files)} missing)")

    bad_town = [e for e in events if e["town_id"] not in reg_towns]
    check(not bad_town, f"every event's town_id exists in the registry ({len(bad_town)} bad)")

    bad_type = [e for e in events if e["event_type"] not in EVENT_TYPES]
    bad_stage = [e for e in events if e["stage"] not in STAGES]
    bad_conf = [e for e in events
                if e["confidence"] is None or not (0 <= e["confidence"] <= 1)]
    bad_review = [e for e in events if e["review_status"] not in REVIEW_STATUSES]
    check(not bad_type, f"event_type enum ({len(bad_type)} bad)")
    check(not bad_stage, f"stage enum ({len(bad_stage)} bad)")
    check(not bad_conf, f"confidence in [0,1] ({len(bad_conf)} bad)")
    check(not bad_review, f"review_status enum ({len(bad_review)} bad)")

    stale_review = []
    cutoff = today - dt.timedelta(days=NEEDS_REVIEW_MAX_AGE_DAYS)
    for e in events:
        if e["review_status"] == "needs_review" and e["created_at"]:
            if dt.date.fromisoformat(e["created_at"][:10]) < cutoff:
                stale_review.append(e["event_id"])
    check(not stale_review,
          f"no needs_review events older than {NEEDS_REVIEW_MAX_AGE_DAYS} days "
          f"({len(stale_review)} stale — run signals/review/review.py)")

    print("\n=== DOCUMENTS (Step 6) ===")
    DOC_TYPES = {"agenda", "minutes", "packet", "bid", "other", None}
    bad_doctype = [d["doc_id"] for d in docs.values() if d["doc_type"] not in DOC_TYPES]
    check(not bad_doctype, f"document doc_type enum ({len(bad_doctype)} bad)")
    oversize_live = [d["doc_id"] for d in docs.values()
                     if d["page_count"] and d["page_count"] > config.PACKET_PAGE_CAP
                     and d["extraction_status"] in ("pending", "done")]
    check(not oversize_live,
          f"no document over the {config.PACKET_PAGE_CAP}-page cap is queued or "
          f"extracted (must be skipped_large) ({len(oversize_live)} over cap)")

    print("\n=== EXTRACTION V2 FIELDS ===")
    live = [e for e in events if e["review_status"] != "rejected"]
    bad_trades, bad_next, bad_tenure, trade_counts = [], [], [], []
    cont_total = cont_with = 0
    for e in live:
        try:
            tr = json.loads(e["trades"]) if e["trades"] else []
        except json.JSONDecodeError:
            tr = None
        if not isinstance(tr, list) or any(t not in TRADES for t in (tr or [])):
            bad_trades.append(e["event_id"])
        elif tr:
            trade_counts.append(len(tr))
        if e["next_date"]:
            try:
                dt.date.fromisoformat(e["next_date"])
            except (ValueError, TypeError):
                bad_next.append(e["event_id"])
        if e["tenure"] is not None and e["tenure"] not in TENURES:
            bad_tenure.append(e["event_id"])
        if e["summary"] and "continued to" in e["summary"].lower():
            cont_total += 1
            if e["next_date"]:
                cont_with += 1
    check(not bad_trades, f"trades within the closed vocabulary ({len(bad_trades)} bad)")
    check(not bad_next, f"next_date parses as ISO when present ({len(bad_next)} bad)")
    check(not bad_tenure, f"tenure enum ({len(bad_tenure)} bad)")
    if trade_counts:
        trade_counts.sort()
        median = trade_counts[len(trade_counts) // 2]
        check(median <= 4,
              f"median trades/event <= 4 over tagged events (median={median}, "
              f"n={len(trade_counts)})")
    else:
        warn(False, "no events carry trades yet (expected before re-extraction)")
    if cont_total:
        share = cont_with / cont_total
        check(share >= 0.6,
              f">=60% of 'continued to' events carry next_date "
              f"({cont_with}/{cont_total} = {share:.0%})")
    else:
        print("  [ -- ] no 'continued to' events to check next_date coverage against")

    bids = [e for e in live if e["event_type"] == "bid_rfp"]
    bid_no_date = [e["event_id"] for e in bids if not e["next_date"]]
    if bids:
        check(not bid_no_date,
              f"every bid_rfp carries a next_date (due date) "
              f"({len(bid_no_date)} of {len(bids)} missing)")
    else:
        print("  [ -- ] no bid_rfp events yet (town may have no open bids)")

    print("\n=== AGENDA/MINUTES MERGE (Step 2) ===")
    doc_type = {d["doc_id"]: d["doc_type"] for d in docs.values()}
    published = [e for e in live if e["review_status"] in ("auto_approved", "human_approved")
                 and not e["superseded_by"]]
    kinds_by_key = {}
    for e in published:
        dt_ = doc_type.get(e["doc_id"])
        if dt_ in ("agenda", "minutes"):
            kinds_by_key.setdefault((e["story_id"], e["board_id"], e["meeting_date"]), set()).add(dt_)
    twins = [k for k, v in kinds_by_key.items() if {"agenda", "minutes"} <= v]
    check(not twins,
          f"no published event keeps a published agenda/minutes twin "
          f"(same story+board+date) ({len(twins)} unmerged pair(s))")
    all_ids = {e["event_id"] for e in events}
    orphan_sup = [e["event_id"] for e in events
                  if e["superseded_by"] and e["superseded_by"] not in all_ids]
    check(not orphan_sup,
          f"every superseded_by points at a real event ({len(orphan_sup)} orphan)")

    print("\n=== STORIES ===")
    stories = conn.execute("SELECT * FROM project_stories").fetchall()
    story_ids = {s["story_id"] for s in stories}
    bad_status = [s for s in stories if s["status"] not in STORY_STATUSES]
    check(not bad_status, f"story status enum ({len(bad_status)} bad)")
    orphan_story_ref = [e for e in events
                        if e["story_id"] and e["story_id"] not in story_ids]
    check(not orphan_story_ref,
          f"every event.story_id points at a real story ({len(orphan_story_ref)} bad)")
    unlinked = [e for e in events
                if e["review_status"] in ("auto_approved", "human_approved")
                and not e["story_id"]]
    warn(not unlinked,
         f"every approved event is linked to a story ({len(unlinked)} unlinked — "
         f"ambiguous cases awaiting review are acceptable)")

    print("\n=== STORY BRIEFS (Step 5) ===")
    DOLLAR_RE = re.compile(r"\$\s?\d")  # same "$ amount" gate build_briefs enforces
    BRIEF_KEYS = {"what", "status", "whats_next", "outlook",
                  "trades", "est_value", "next_date", "generated_at"}
    pub_by_story = {}
    for e in events:
        if (e["review_status"] in ("auto_approved", "human_approved")
                and not e["superseded_by"] and e["story_id"]):
            pub_by_story.setdefault(e["story_id"], []).append(e)
    missing_brief, bad_json, missing_keys = [], [], []
    dollar_leak, brief_bad_trades, bad_est, bad_bnext = [], [], [], []
    for s in stories:
        members = pub_by_story.get(s["story_id"], [])
        has_dollar = any(m["dollar_value"] is not None for m in members)
        if len(members) >= 2 and s["status"] == "active" and not s["brief"]:
            missing_brief.append(s["story_id"])
        if not s["brief"]:
            continue
        try:
            b = json.loads(s["brief"])
        except (json.JSONDecodeError, TypeError):
            bad_json.append(s["story_id"])
            continue
        if not isinstance(b, dict) or not BRIEF_KEYS <= set(b):
            missing_keys.append(s["story_id"])
            continue
        if not has_dollar:
            for f in ("what", "outlook"):
                if DOLLAR_RE.search(str(b.get(f) or "")):
                    dollar_leak.append((s["story_id"], f))
        tr = b.get("trades") or []
        if not isinstance(tr, list) or any(t not in TRADES for t in tr):
            brief_bad_trades.append(s["story_id"])
        if b.get("est_value") is not None and not has_dollar:
            bad_est.append(s["story_id"])
        if b.get("next_date"):
            try:
                dt.date.fromisoformat(str(b["next_date"]))
            except (ValueError, TypeError):
                bad_bnext.append(s["story_id"])
    check(not missing_brief,
          f"every active multi-event story has a brief ({len(missing_brief)} missing — "
          f"run signals/synthesize/build_briefs.py)")
    check(not bad_json, f"every stored brief is valid JSON ({len(bad_json)} bad)")
    check(not missing_keys, f"every brief carries the required keys ({len(missing_keys)} bad)")
    check(not dollar_leak,
          f"no $ amount in brief what/outlook unless an event stated a dollar_value "
          f"({len(dollar_leak)} leak(s))")
    check(not brief_bad_trades,
          f"brief trades within the closed vocabulary ({len(brief_bad_trades)} bad)")
    check(not bad_est,
          f"brief est_value null unless an event stated a dollar_value ({len(bad_est)} bad)")
    check(not bad_bnext, f"brief next_date parses as ISO when present ({len(bad_bnext)} bad)")

    print("\n=== REGISTRY vs town_scores.csv ===")
    if os.path.exists(TOWN_SCORES):
        import csv
        with open(TOWN_SCORES, newline="", encoding="utf-8") as fh:
            known_fips = {row["fips"] for row in csv.DictReader(fh)}
        missing = [t["town_id"] for t in registry
                   if str(t.get("place_fips", "")) not in known_fips]
        warn(not missing,
             f"every registry place_fips exists in town_scores.csv ({missing or 'all ok'})")
    else:
        warn(False, "pipeline/town_scores.csv not found — fips cross-check skipped")

    print("\n=== COVERAGE FRESHNESS ===")
    for t in active_towns:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS latest FROM documents WHERE town_id=?",
            (t["town_id"],)).fetchone()
        latest = row["latest"]
        if latest is None:
            check(False, f"{t['town_id']}: RED — active but NO documents ever fetched")
            continue
        age = (today - dt.date.fromisoformat(latest[:10])).days
        check(age <= FRESHNESS_RED_DAYS,
              f"{t['town_id']}: last document fetched {age}d ago "
              f"(RED if > {FRESHNESS_RED_DAYS}d — registry entry or site may have changed)")

    print("\n=== PUBLISHED FEED ===")
    if os.path.exists(FEED_PATH):
        try:
            with open(FEED_PATH, "r", encoding="utf-8") as fh:
                feed = json.load(fh)
            check(True, "feed.json parses")
            db_event_ids = {e["event_id"] for e in events}
            feed_events = feed.get("events", [])
            unknown = [fe["event_id"] for fe in feed_events
                       if fe.get("event_id") not in db_event_ids]
            check(not unknown,
                  f"every feed event exists in the DB ({len(unknown)} unknown)")
            no_src = [fe.get("event_id") for fe in feed_events
                      if not fe.get("source_url")]
            check(not no_src,
                  f"every feed event has a non-empty source_url ({len(no_src)} missing)")
        except (json.JSONDecodeError, OSError) as exc:
            check(False, f"feed.json parses ({exc})")
    else:
        warn(False, "docs/output/signals/feed.json not built yet "
                    "(run signals/publish/build_signals_json.py)")

    print()
    if fails:
        print(f"RESULT: {len(fails)} FAILURE(S), {len(warns)} warning(s) — fix before publishing.")
        sys.exit(1)
    print(f"RESULT: ALL GREEN ({len(warns)} warning(s)).")


if __name__ == "__main__":
    main()
