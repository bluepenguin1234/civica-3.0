#!/usr/bin/env python3
"""
resolve_entities.py — resolve people/firms/offices from events (Step 7).

Deterministic, NO LLM. Walks every published event and pulls the named parties
out of applicant / owner / applicant_reps / job_contact, then dedupes them within
a town with the same rapidfuzz token_set_ratio >= 85 convention the story-link
stage uses. Each resolved party becomes one row in `entities`; each mention
becomes one row in `event_entities` (event_id, entity_id, role, source). A name
that matches two or more existing clusters is NOT guessed — it gets its own
cluster and is logged to ambiguous_entities.txt for a human.

This is what collapses "Forest River Realty" / "Forest River Realty, LLC" and
"Abiomed" / "Abiomed, Inc." into a single directory entity.

Enrichment columns (website/phone/linkedin/enrich_*) are left untouched — they
are Step 8's job — so re-running this never wipes verified contact data:
entity_ids are deterministic and the entity row is upserted, not replaced.

Usage (from the repo root):  python -m signals.enrich.resolve_entities
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys

from rapidfuzz import fuzz

from signals import db

sys.stdout.reconfigure(encoding="utf-8")

MATCH_THRESHOLD = 85  # same as the story-link stage
AMBIGUOUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "ambiguous_entities.txt")

ROLES = {"developer", "owner", "engineer", "architect", "attorney",
         "surveyor", "gc", "public_contact", "representative"}
KINDS = {"person", "firm", "public_office"}

# A government body — checked before the firm test (a "District" is an office).
_OFFICE_RE = re.compile(
    r"\b(town of|city of|dpw|department|district|sewerage|commission|"
    r"authority|selectmen|select board|conservation)\b", re.I)
# Corporate / institutional markers — if present, the name is an organization.
_FIRM_RE = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|co|ltd|lp|llp|pc|pllc|"
    r"trust|realty|properties|property|associates|assoc|group|company|"
    r"development|developers|engineering|capital|solutions|partners|holdings|"
    r"enterprises|builders|construction|contractors|management|grid|electric|"
    r"gas|design|school|academy|preparatory|university|tech|bank|aec)\b", re.I)
# Corporate suffixes stripped from the MATCH key so "Foo" == "Foo, LLC".
_SUFFIX_RE = re.compile(
    r"\b(llc|inc|incorporated|corp|corporation|co|ltd|lp|llp|pc|pllc)\b", re.I)


def classify(name: str) -> str:
    if _OFFICE_RE.search(name):
        return "public_office"
    if _FIRM_RE.search(name):
        return "firm"
    toks = re.sub(r"[^A-Za-z0-9& ]", " ", name).split()
    if 2 <= len(toks) <= 4:
        return "person"
    return "firm"  # single token (Abiomed) or long org-ish strings


def match_key(name: str) -> str:
    """Normalized form used for fuzzy comparison (suffixes dropped)."""
    s = name.lower()
    s = _SUFFIX_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9& ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def map_rep_role(raw: str | None) -> str:
    t = (raw or "").lower()
    if "engineer" in t:
        return "engineer"
    if "architect" in t:
        return "architect"
    if "attorney" in t or "counsel" in t or "esq" in t or t.strip() == "law":
        return "attorney"
    if "survey" in t:
        return "surveyor"
    if "general contractor" in t or t.strip() in ("gc", "contractor", "builder"):
        return "gc"
    if "owner" in t:
        return "owner"
    if "develop" in t:
        return "developer"
    return "representative"  # presenter / broker / consultant / chairman / generic


def iter_mentions(ev):
    """Yield (raw_name, role, source) for every named party in one event."""
    if ev["applicant"]:
        if ev["event_type"] == "permit_issued":
            # a permit's applicant is the contractor pulling it (Step S3) — the GC
            # when it's a firm, the homeowner when it's a person.
            role = "gc" if classify(ev["applicant"]) == "firm" else "owner"
        else:
            role = "developer"
        yield ev["applicant"], role, "applicant"
    if ev["owner"]:
        yield ev["owner"], "owner", "owner"
    try:
        reps = json.loads(ev["applicant_reps"]) if ev["applicant_reps"] else []
    except json.JSONDecodeError:
        reps = []
    for r in reps if isinstance(reps, list) else []:
        if not isinstance(r, dict):
            continue
        role = map_rep_role(r.get("role"))
        if r.get("name"):
            yield r["name"], role, "applicant_reps"
        if r.get("firm"):
            yield r["firm"], role, "applicant_reps"
    try:
        jc = json.loads(ev["job_contact"]) if ev["job_contact"] else None
    except json.JSONDecodeError:
        jc = None
    if isinstance(jc, dict):
        if jc.get("name"):
            yield jc["name"], "public_contact", "job_contact"
        if jc.get("org"):
            yield jc["org"], "public_contact", "job_contact"


def cluster_names(names, town_scope, kind, ambiguous):
    """Single-linkage cluster of surface names (same town+kind) at >=85.

    Seeds shortest-first so a base name ("Abiomed") anchors the cluster its
    longer variants ("Abiomed, Inc.") join. Returns {raw_name -> entity_id} and
    {entity_id -> canonical_name}. A name matching 2+ clusters is logged and
    kept separate (never guessed).
    """
    clusters = []  # each: {"key": seed_key, "names": [raw, ...]}
    for name in sorted(names, key=lambda n: (len(n), n.lower())):
        key = match_key(name)
        if not key:
            continue
        hits = [c for c in clusters if fuzz.token_set_ratio(key, c["key"]) >= MATCH_THRESHOLD]
        if len(hits) >= 2:
            ambiguous.append((town_scope, kind, name, [c["names"][0] for c in hits]))
            clusters.append({"key": key, "names": [name]})
        elif hits:
            hits[0]["names"].append(name)
        else:
            clusters.append({"key": key, "names": [name]})

    name_to_id, id_to_canon = {}, {}
    for c in clusters:
        eid = hashlib.sha1(f"{town_scope}|{kind}|{c['key']}".encode()).hexdigest()[:16]
        # canonical display = the most complete surface form (longest, tie alpha)
        canon = sorted(c["names"], key=lambda n: (-len(n), n))[0]
        id_to_canon[eid] = canon
        for n in c["names"]:
            name_to_id[n] = eid
    return name_to_id, id_to_canon


def main():
    conn = db.init_db()
    events = conn.execute(
        "SELECT * FROM events WHERE review_status IN ('auto_approved','human_approved') "
        "AND superseded_by IS NULL").fetchall()

    # 1) collect mentions, bucket distinct surface names by (town, kind)
    mentions = []  # (event_id, raw, role, source, town, kind)
    buckets = {}   # (town, kind) -> set(raw names)
    for ev in events:
        town = ev["town_id"]
        for raw, role, source in iter_mentions(ev):
            raw = " ".join(str(raw).split()).strip(" ,;")
            if not raw:
                continue
            kind = classify(raw)
            mentions.append((ev["event_id"], raw, role, source, town, kind))
            buckets.setdefault((town, kind), set()).add(raw)

    # 2) cluster within each (town, kind)
    ambiguous = []
    name_to_id, id_to_canon, id_meta = {}, {}, {}
    for (town, kind), names in buckets.items():
        n2i, i2c = cluster_names(names, town, kind, ambiguous)
        for raw, eid in n2i.items():
            name_to_id[(town, kind, raw)] = eid
        for eid, canon in i2c.items():
            id_to_canon[eid] = canon
            id_meta[eid] = (kind, town)

    # 3) upsert entities (preserve Step 8 enrichment columns), rebuild links
    now = dt.datetime.now().isoformat(timespec="seconds")
    for eid, canon in id_to_canon.items():
        kind, town = id_meta[eid]
        conn.execute(
            "INSERT INTO entities (entity_id, kind, canonical_name, town_scope, review_status) "
            "VALUES (?,?,?,?, 'auto_resolved') ON CONFLICT(entity_id) DO UPDATE SET "
            "kind=excluded.kind, canonical_name=excluded.canonical_name, "
            "town_scope=excluded.town_scope",
            (eid, kind, canon, town))

    conn.execute("DELETE FROM event_entities")
    for event_id, raw, role, source, town, kind in mentions:
        eid = name_to_id[(town, kind, raw)]
        conn.execute(
            "INSERT OR IGNORE INTO event_entities (event_id, entity_id, role, source) "
            "VALUES (?,?,?,?)", (event_id, eid, role, source))

    # 4) prune entities that no longer have any link (e.g. event was rejected)
    conn.execute("DELETE FROM entities WHERE entity_id NOT IN "
                 "(SELECT DISTINCT entity_id FROM event_entities)")
    conn.commit()

    if ambiguous:
        with open(AMBIGUOUS_FILE, "w", encoding="utf-8") as fh:
            for town, kind, name, against in ambiguous:
                fh.write(f"{town} {kind}: {name!r} matched 2+ clusters "
                         f"-> {against}  (kept separate, not guessed)\n")

    n_ent = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    n_link = conn.execute("SELECT COUNT(*) FROM event_entities").fetchone()[0]
    by_kind = conn.execute("SELECT kind, COUNT(*) FROM entities GROUP BY kind").fetchall()
    print("=== entity resolution report ===")
    print(f"events scanned: {len(events)} | mentions: {len(mentions)} | "
          f"entities: {n_ent} | event-entity links: {n_link}")
    print("by kind: " + ", ".join(f"{k}={c}" for k, c in by_kind))
    if ambiguous:
        print(f"AMBIGUOUS: {len(ambiguous)} name(s) matched 2+ clusters — "
              f"review {AMBIGUOUS_FILE} (kept separate, not guessed)")
    else:
        print("ambiguous merges: 0")

    print("\nentities by project count:")
    for r in conn.execute(
            "SELECT e.canonical_name, e.kind, COUNT(DISTINCT ev.story_id) AS projects, "
            "COUNT(*) AS mentions FROM entities e "
            "JOIN event_entities ee ON ee.entity_id = e.entity_id "
            "JOIN events ev ON ev.event_id = ee.event_id "
            "GROUP BY e.entity_id ORDER BY projects DESC, mentions DESC LIMIT 15"):
        print(f"  [{r['projects']}p / {r['mentions']}m] {r['kind']:13} {r['canonical_name']}")
    conn.close()


if __name__ == "__main__":
    main()
