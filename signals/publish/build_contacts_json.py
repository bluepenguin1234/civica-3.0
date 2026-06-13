#!/usr/bin/env python3
"""
build_contacts_json.py — publish the GATED contact directory (Step 8).

Writes docs/output/signals/contacts.json: per-entity enriched contact fields,
keyed by entity_id. This is the paid moat, kept OUT of the public feed.json — the
dashboard fetches it separately through the checkAccess() seam, so a future
paid-tier check can withhold it while the feed stays open.

Only PUBLISHABLE fields ship: the constructed LinkedIn/registry SEARCH links
(status 'auto') and any website/phone a human approved in review_entities.py
(status 'human_verified'). needs_review / rejected fields never appear here.

Usage (from the repo root):  python -m signals.publish.build_contacts_json
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

from signals import config, db
from signals.enrich.enrich_entities import PUBLISHABLE

sys.stdout.reconfigure(encoding="utf-8")

_KEEP = ("value", "source", "confidence", "verified", "kind")


def main():
    conn = db.init_db()
    out, held = {}, 0
    for r in conn.execute("SELECT entity_id, enrichment FROM entities "
                          "WHERE enrichment IS NOT NULL"):
        enr = json.loads(r["enrichment"])
        pub = {}
        for field, prov in enr.items():
            if not isinstance(prov, dict) or not prov.get("value"):
                continue
            if prov.get("status") in PUBLISHABLE:
                pub[field] = {k: prov[k] for k in _KEEP if k in prov}
            elif prov.get("status") == "needs_review":
                held += 1
        if pub:
            out[r["entity_id"]] = pub

    contacts = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "contacts": out,
    }
    os.makedirs(config.PUBLISH_DIR, exist_ok=True)
    path = os.path.join(config.PUBLISH_DIR, "contacts.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(contacts, fh, ensure_ascii=False, indent=1)

    n_fields = sum(len(v) for v in out.values())
    print(f"contacts.json: {len(out)} entities, {n_fields} published field(s), "
          f"{held} field(s) held for review ({os.path.getsize(path):,} bytes)")
    conn.close()


if __name__ == "__main__":
    main()
