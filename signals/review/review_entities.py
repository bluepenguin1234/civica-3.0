#!/usr/bin/env python3
"""
review_entities.py — human review queue for Step 8 contact enrichment.

Researched website/phone values land as needs_review (a wrong phone number is
worse than none). This walks every pending enriched field and lets a human
approve or reject it before it can reach the gated contacts.json. The constructed
LinkedIn/registry SEARCH links are not shown here — they carry no factual claim
and publish automatically.

    a  approve  (field status -> human_verified; it may now publish)
    r  reject   (field status -> rejected; never published)
    s  skip     (leave needs_review)
    q  quit

Usage (from the repo root):  python -m signals.review.review_entities
"""

from __future__ import annotations

import json
import sys

from signals import db
from signals.review.review import getkey  # reuse the single-keystroke reader

sys.stdout.reconfigure(encoding="utf-8")

REVIEWABLE = {"website", "phone"}  # constructed search URLs are auto, not reviewed


def pending(enr):
    return [(f, d) for f, d in enr.items()
            if f in REVIEWABLE and isinstance(d, dict) and d.get("status") == "needs_review"]


def main():
    conn = db.init_db()
    rows = conn.execute(
        "SELECT entity_id, kind, canonical_name, town_scope, enrichment FROM entities "
        "WHERE enrichment LIKE '%needs_review%' ORDER BY canonical_name").fetchall()
    queue = []
    for r in rows:
        enr = json.loads(r["enrichment"]) if r["enrichment"] else {}
        for field, prov in pending(enr):
            queue.append((r, field, prov))
    if not queue:
        print("Enrichment review queue is empty.")
        return
    print(f"{len(queue)} enriched field(s) need review.\n")

    decided = 0
    for idx, (r, field, prov) in enumerate(queue, 1):
        print(f"\n--- {idx}/{len(queue)}: {r['canonical_name']} ({r['kind']}, {r['town_scope']}) ---")
        print(f"  {field}: {prov['value']}")
        print(f"  source: {prov['source']}  |  confidence: {prov.get('confidence')}  "
              f"|  verified: {prov.get('verified')}")
        print("  verify against the source before approving — a wrong phone is worse than none.")
        print("  [a]pprove  [r]eject  [s]kip  [q]uit")
        while True:
            key = getkey()
            if key in ("a", "r"):
                enr = json.loads(conn.execute(
                    "SELECT enrichment FROM entities WHERE entity_id=?",
                    (r["entity_id"],)).fetchone()["enrichment"])
                enr[field]["status"] = "human_verified" if key == "a" else "rejected"
                conn.execute("UPDATE entities SET enrichment=? WHERE entity_id=?",
                             (json.dumps(enr, ensure_ascii=False), r["entity_id"]))
                conn.commit()
                decided += 1
                print(f"  -> {'approved' if key == 'a' else 'rejected'}")
                break
            if key == "s":
                print("  -> skipped")
                break
            if key == "q":
                print(f"\nDone for now: {decided} decided, {len(queue) - idx + 1} still pending.")
                conn.close()
                return

    print(f"\nQueue finished: {decided} decided. "
          f"Re-publish (signals.publish.build_contacts_json) to push approved contacts.")
    conn.close()


if __name__ == "__main__":
    main()
