#!/usr/bin/env python3
"""
review.py — human review queue for Civica Signals events (Phase 2).

Walks every needs_review event (oldest first), showing the summary, type, town,
confidence, and the source PDF path + page so the event can be checked against
the document. Single-keystroke actions:

    a  approve  (review_status -> human_approved)
    r  reject   (review_status -> rejected)
    e  edit the event_type, then approve
    s  skip (leave needs_review)
    q  quit

Usage (from the repo root):  python -m signals.review.review
"""

from __future__ import annotations

import sys

from signals import db
from signals.extract.extract import EVENT_TYPES

sys.stdout.reconfigure(encoding="utf-8")

TYPE_LIST = sorted(EVENT_TYPES)


def getkey() -> str:
    try:  # single keystroke on Windows
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):  # swallow arrow/function-key prefixes
            msvcrt.getch()
            return ""
        return ch.decode(errors="ignore").lower()
    except ImportError:  # fallback: line input elsewhere
        return (input("> ").strip()[:1] or "").lower()


def show(ev, idx, total):
    print(f"\n--- event {idx}/{total} ---")
    print(f"  {ev['event_type']}  |  stage: {ev['stage']}  |  confidence: {ev['confidence']:.2f}")
    print(f"  {ev['town_id']} / {ev['board_id']}  |  meeting {ev['meeting_date']}")
    if ev["project_name"]:
        print(f"  project: {ev['project_name']}")
    if ev["address"]:
        print(f"  address: {ev['address']}")
    if ev["applicant"]:
        print(f"  applicant: {ev['applicant']}")
    if ev["applicant_reps"]:
        print(f"  reps: {ev['applicant_reps']}")
    if ev["job_contact"]:
        print(f"  job contact: {ev['job_contact']}")
    print(f"  summary: {ev['summary']}")
    print(f"  source: {ev['local_path']}  (page {ev['source_page']})")
    print("  [a]pprove  [r]eject  [e]dit type  [s]kip  [q]uit")


def edit_type() -> str | None:
    for i, t in enumerate(TYPE_LIST, 1):
        print(f"    {i:2d}. {t}")
    raw = input("  new type number (blank = cancel): ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(TYPE_LIST):
        return TYPE_LIST[int(raw) - 1]
    return None


def main():
    conn = db.init_db()
    rows = conn.execute(
        "SELECT e.*, d.local_path FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.review_status = 'needs_review' "
        "ORDER BY e.meeting_date ASC, e.created_at ASC"
    ).fetchall()
    if not rows:
        print("Review queue is empty.")
        return
    print(f"{len(rows)} event(s) need review.")

    decided = 0
    for idx, ev in enumerate(rows, 1):
        show(ev, idx, len(rows))
        while True:
            key = getkey()
            if key == "a":
                conn.execute("UPDATE events SET review_status='human_approved' "
                             "WHERE event_id=?", (ev["event_id"],))
                conn.commit()
                decided += 1
                print("  -> approved")
                break
            if key == "r":
                conn.execute("UPDATE events SET review_status='rejected' "
                             "WHERE event_id=?", (ev["event_id"],))
                conn.commit()
                decided += 1
                print("  -> rejected")
                break
            if key == "e":
                new_type = edit_type()
                if new_type:
                    conn.execute("UPDATE events SET event_type=?, "
                                 "review_status='human_approved' WHERE event_id=?",
                                 (new_type, ev["event_id"]))
                    conn.commit()
                    decided += 1
                    print(f"  -> type set to {new_type}, approved")
                    break
                print("  (cancelled)")
            elif key == "s":
                print("  -> skipped")
                break
            elif key == "q":
                print(f"\nDone for now: {decided} decided, "
                      f"{len(rows) - idx + 1} still pending.")
                conn.close()
                return

    print(f"\nQueue finished: {decided} decided.")
    conn.close()


if __name__ == "__main__":
    main()
