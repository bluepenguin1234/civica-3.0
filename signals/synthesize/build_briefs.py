#!/usr/bin/env python3
"""
build_briefs.py — synthesize a short brief per project story (Step 5).

Runs AFTER linking. For each multi-event story whose timeline changed since its
brief was last built (last_activity moved), it sends the story's events through
`claude -p` and stores a JSON brief on project_stories.brief.

The honesty contract is enforced in code, not left to the model:
  - what / status / whats_next / outlook are the model's prose. "outlook" is the
    ONLY field allowed to look forward; the UI labels it a projection.
  - trades, est_value, next_date are computed from the structured record and
    OVERRIDE whatever the model returns — they can't be invented.
  - When no member event states a dollar_value, any "$" figure the model puts in
    what/outlook is redacted, so a fabricated number can never reach the feed.

Backend is the same `claude -p` path as extraction (extract.call_claude) — the
operator's subscription, no API key.

Usage (from the repo root):
    python -m signals.synthesize.build_briefs            # only dirty stories
    python -m signals.synthesize.build_briefs --all      # rebuild every brief
    python -m signals.synthesize.build_briefs --limit 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

from signals import config, db
from signals.extract.extract import TRADES, call_claude, registry_names, ExtractionError

sys.stdout.reconfigure(encoding="utf-8")

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompts", "brief_prompt.md")
PROSE_FIELDS = ("what", "status", "whats_next", "outlook")
# A "$ amount": a dollar sign then a figure (matches validate_signals' gate).
_DOLLAR_TOKEN = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|thousand|m|b|k)?\b",
    re.IGNORECASE)


def parse_json_object(text: str):
    """Defensive parse: strip fences/commentary, return the first JSON object."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            return None
        t = t[start:end + 1]
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def member_events(conn, story_id):
    """The story's published, non-superseded events, oldest first."""
    return conn.execute(
        "SELECT e.*, d.doc_type FROM events e JOIN documents d ON d.doc_id = e.doc_id "
        "WHERE e.story_id = ? AND e.superseded_by IS NULL "
        "AND e.review_status IN ('auto_approved','human_approved') "
        "ORDER BY e.meeting_date ASC, e.created_at ASC", (story_id,)).fetchall()


def format_event(e, board_names) -> str:
    board = board_names.get((e["town_id"], e["board_id"]), e["board_id"] or "board")
    bits = [f"type={e['event_type']}"]
    if e["stage"]:
        bits.append(f"stage={e['stage']}")
    if e["residential_units"] is not None:
        bits.append(f"units={e['residential_units']}")
    if e["commercial_sqft"] is not None:
        bits.append(f"sqft={e['commercial_sqft']}")
    if e["dollar_value"] is not None:
        bits.append(f"dollar_value=${e['dollar_value']:,}")
    if e["tenure"] and e["tenure"] != "unknown":
        bits.append(f"tenure={e['tenure']}")
    try:
        trades = json.loads(e["trades"]) if e["trades"] else []
    except json.JSONDecodeError:
        trades = []
    if trades:
        bits.append(f"trades=[{', '.join(trades)}]")
    if e["next_date"]:
        bits.append(f"next_date={e['next_date']}")
    line = f"- {e['meeting_date'] or '?'} · {board} · " + " · ".join(bits)
    line += f"\n  {(e['summary'] or '').strip()}"
    who = []
    if e["applicant"]:
        who.append(f"applicant={e['applicant']}")
    if e["owner"]:
        who.append(f"owner={e['owner']}")
    if who:
        line += "\n  " + "; ".join(who)
    return line


def computed_trades(events):
    """Union of the closed-vocabulary trades actually present in the events."""
    out = []
    for e in events:
        try:
            for t in (json.loads(e["trades"]) if e["trades"] else []):
                if t in TRADES and t not in out:
                    out.append(t)
        except json.JSONDecodeError:
            pass
    return out


def computed_est_value(events):
    """Largest stated dollar_value among the events, or None — never inferred."""
    vals = [e["dollar_value"] for e in events if e["dollar_value"] is not None]
    return max(vals) if vals else None


def computed_next_date(events, today):
    """Soonest stated future continuation/hearing/bid-due date, or None."""
    ds = sorted(d for d in (e["next_date"] for e in events) if d and d >= today)
    return ds[0] if ds else None


def build_brief(template, story, events, board_names, today):
    """One claude -p call -> a sanitized brief dict (honesty contract enforced)."""
    addr = story["canonical_address"]
    prompt = (template
              .replace("{story_name}", story["canonical_name"] or "Unnamed project")
              .replace("{address_clause}", f" at {addr}" if addr else "")
              .replace("{town}", story["town_id"].split("_")[0].title())
              .replace("{current_stage}", story["current_stage"] or "unknown")
              .replace("{events_block}",
                       "\n".join(format_event(e, board_names) for e in events)))

    text, _ = call_claude(prompt)
    parsed = parse_json_object(text)
    if parsed is None:  # one retry, mirroring the extractor
        text, _ = call_claude(prompt + "\n\nReturn ONLY the JSON object.")
        parsed = parse_json_object(text)
    if parsed is None:
        raise ExtractionError("unparseable brief JSON after retry")

    brief = {}
    for f in PROSE_FIELDS:
        v = parsed.get(f)
        brief[f] = v.strip() if isinstance(v, str) else ""
    if not brief["what"]:
        raise ExtractionError("brief missing 'what'")

    # Structured fields are computed from the record, never the model.
    est_value = computed_est_value(events)
    brief["trades"] = computed_trades(events)
    brief["est_value"] = est_value
    brief["next_date"] = computed_next_date(events, today)

    # Dollar guard: with no stated value in the record, no "$" figure may stand.
    if est_value is None:
        for f in ("what", "outlook"):
            redacted = _DOLLAR_TOKEN.sub("[amount not stated]", brief[f])
            if redacted != brief[f]:
                print(f"      ~ redacted fabricated $ figure from {f}")
            brief[f] = redacted

    brief["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return brief


def main(argv=None):
    parser = argparse.ArgumentParser(description="Civica Signals story briefs (Step 5).")
    parser.add_argument("--all", action="store_true",
                        help="rebuild every multi-event story's brief, not just dirty ones")
    parser.add_argument("--limit", type=int, default=None,
                        help="max stories to synthesize this run")
    parser.add_argument("--shard", type=str, default=None,
                        help="only stories where rowid %% N == K, given as 'K/N' "
                             "(run N parallel workers over disjoint, stable story sets)")
    args = parser.parse_args(argv)

    with open(PROMPT_PATH, "r", encoding="utf-8") as fh:
        template = fh.read()
    _, board_names = registry_names()

    conn = db.init_db()
    today = dt.date.today().isoformat()
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        stories = conn.execute("SELECT * FROM project_stories WHERE (rowid % ?) = ?", (n, k)).fetchall()
    else:
        stories = conn.execute("SELECT * FROM project_stories").fetchall()

    todo = []
    for s in stories:
        events = member_events(conn, s["story_id"])
        if len(events) < 2:
            continue  # single-event stories: the event's own summary is the brief
        dirty = args.all or not s["brief"] or s["brief_last_activity"] != s["last_activity"]
        if dirty:
            todo.append((s, events))
    if args.limit:
        todo = todo[:args.limit]

    if not todo:
        print("No stories need a brief (all current).")
        conn.close()
        return

    print(f"Synthesizing {len(todo)} story brief(s) via `claude -p` "
          f"(model {config.ANTHROPIC_MODEL}, subscription auth — no API credits).\n")
    done = failed = 0
    for s, events in todo:
        print(f"== {s['canonical_name']} ({len(events)} events, {s['status']}) ==")
        try:
            brief = build_brief(template, s, events, board_names, today)
        except (ExtractionError, Exception) as exc:
            failed += 1
            print(f"   FAILED: {exc.__class__.__name__}: {exc}\n")
            continue
        conn.execute(
            "UPDATE project_stories SET brief = ?, brief_last_activity = ? WHERE story_id = ?",
            (json.dumps(brief, ensure_ascii=False), s["last_activity"], s["story_id"]))
        conn.commit()
        done += 1
        print(f"   what: {brief['what']}")
        print(f"   status: {brief['status']}")
        print(f"   next: {brief['whats_next']}")
        print(f"   outlook: {brief['outlook']}")
        extra = []
        if brief["trades"]:
            extra.append(f"trades={brief['trades']}")
        if brief["est_value"] is not None:
            extra.append(f"est_value=${brief['est_value']:,}")
        if brief["next_date"]:
            extra.append(f"next_date={brief['next_date']}")
        if extra:
            print(f"   [{' | '.join(extra)}]")
        print()

    print("=== brief synthesis summary ===")
    print(f"stories: {done} done, {failed} failed")
    print("cost: $0 marginal (Claude subscription); tokens count against the plan's usage window.")
    conn.close()


if __name__ == "__main__":
    main()
