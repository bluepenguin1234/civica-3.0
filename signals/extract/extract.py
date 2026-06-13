#!/usr/bin/env python3
"""
extract.py — Civica Signals extraction pipeline (Phase 2).

Pulls pending documents from the manifest, extracts their text with pdfplumber
(page-numbered), sends each through Claude with prompts/extraction_prompt.md,
validates the returned events against the schema, and inserts them with full
source traceability (doc_id + source_page).

Backend: the Claude Code CLI in headless mode (`claude -p`), authenticated by
the operator's existing Claude subscription — no API key or credits. All
backend specifics live in call_claude(); swapping to the anthropic SDK later
is a one-function change.

Usage (from the repo root):
    python -m signals.extract.extract --limit 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

import pdfplumber
import yaml

from signals import config, db

sys.stdout.reconfigure(encoding="utf-8")

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompts", "extraction_prompt.md")
CHUNK_CHARS = 80_000          # split docs bigger than this into page-range chunks
CALIBRATION_EVENTS = 200      # first N events are ALL needs_review (spec 2.2 #5)
AUTO_APPROVE_CONFIDENCE = 0.75
CLI_TIMEOUT_SECONDS = 300

EVENT_TYPES = {
    "residential_project", "commercial_project", "mixed_use_project",
    "subdivision", "40b_application", "zoning_amendment",
    "variance_special_permit", "tax_override_debt_exclusion",
    "infrastructure_project", "municipal_property", "master_plan_comp_plan",
    "bid_rfp", "other_notable",
}
STAGES = {
    "proposed", "hearing", "continued", "approved", "denied", "withdrawn",
    "permitted", "under_construction", "informational",
}
# Closed trade vocabulary (extraction v2). Anything else is dropped in
# validation — the validator also asserts published events stay inside it.
TRADES = {
    "site_excavation", "demolition", "paving_asphalt", "concrete_foundation",
    "framing_carpentry", "roofing", "electrical", "plumbing", "hvac",
    "masonry", "drywall_finishes", "landscaping", "utilities", "solar_energy",
    "stormwater_septic",
}
TENURES = {"rental", "ownership", "unknown"}


class ExtractionError(Exception):
    pass


# --- Claude backend (the ONLY backend-specific code) ------------------------

def call_claude(prompt: str) -> tuple[str, dict]:
    """Run one extraction through `claude -p`; return (result_text, usage)."""
    exe = shutil.which("claude")
    if not exe:
        raise ExtractionError("claude CLI not found on PATH — is Claude Code installed?")
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json", "--model", config.ANTHROPIC_MODEL],
        input=prompt, capture_output=True, text=True, encoding="utf-8",
        timeout=CLI_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise ExtractionError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ExtractionError(f"claude CLI returned non-JSON envelope: {proc.stdout[:200]}")
    if envelope.get("is_error"):
        raise ExtractionError(f"claude CLI error result: {str(envelope.get('result'))[:300]}")
    return envelope.get("result") or "", envelope.get("usage") or {}


# --- Document text -----------------------------------------------------------

def pdf_pages(path: str) -> list[str]:
    """Page texts, each prefixed with its [PAGE n] marker.

    Text sidecars take precedence over pdfplumber: `<path>.ocr.txt` for scanned
    PDFs (signals/extract/ocr.py) and `<path>.txt` for non-PDF documents like
    bid/RFP HTML pages (signals/crawl/crawl.py). This is how the extractor
    processes documents that aren't machine-readable PDFs.
    """
    for sidecar in (path + ".ocr.txt", path + ".txt"):
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                text = fh.read()
            parts = re.split(r"(?=\[PAGE \d+\])", text)
            pages = [p.strip() for p in parts if p.strip()]
            if pages:
                return pages
    if path.lower().endswith(".pdf"):
        with pdfplumber.open(path) as pdf:
            return [f"[PAGE {i}]\n{(p.extract_text() or '').strip()}"
                    for i, p in enumerate(pdf.pages, start=1)]
    raise ExtractionError(f"no text sidecar for non-PDF document: {path}")


def chunk_pages(pages: list[str]) -> list[str]:
    """Join pages into chunks of <= CHUNK_CHARS (a doc rarely needs > 1)."""
    chunks, cur, cur_len = [], [], 0
    for page in pages:
        if cur and cur_len + len(page) > CHUNK_CHARS:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        cur.append(page)
        cur_len += len(page)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def build_prompt(template: str, doc, town_name: str, board_name: str, text: str) -> str:
    # Metadata first, document text LAST so braces inside the doc can't be re-substituted.
    return (template
            .replace("{town}", town_name)
            .replace("{board}", board_name)
            .replace("{doc_type}", doc["doc_type"] or "unknown")
            .replace("{meeting_date}", doc["meeting_date"] or "unknown")
            .replace("{document_text}", text))


# --- Parsing + validation ----------------------------------------------------

def parse_json_array(text: str):
    """Defensive parse: strip fences/commentary, find the JSON array."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    if not t.startswith("["):
        start, end = t.find("["), t.rfind("]")
        if start == -1 or end <= start:
            return None
        t = t[start:end + 1]
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _int_or_none(value, field, problems):
    if value is None:
        return None
    if isinstance(value, bool):
        problems.append(f"{field}={value!r} not an int")
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        digits = value.replace(",", "").replace("$", "").strip()
        if digits.isdigit():
            return int(digits)
    problems.append(f"{field}={value!r} not an int")
    return None


def validate_event(ev: dict):
    """Return (clean_event, soft_problems) or (None, reason) if the event is invalid."""
    if not isinstance(ev, dict):
        return None, "not an object"
    if ev.get("event_type") not in EVENT_TYPES:
        return None, f"bad event_type {ev.get('event_type')!r}"
    stage = ev.get("stage")
    if stage is not None and stage not in STAGES:
        return None, f"bad stage {stage!r}"
    conf = ev.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0 <= conf <= 1):
        return None, f"bad confidence {conf!r}"
    summary = ev.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "missing summary"

    soft = []
    reps = ev.get("applicant_reps")
    if reps is not None and not isinstance(reps, list):
        soft.append(f"applicant_reps={reps!r} not a list")
        reps = None
    contact = ev.get("job_contact")
    if contact is not None and not isinstance(contact, dict):
        soft.append(f"job_contact={contact!r} not an object")
        contact = None

    # --- extraction v2 fields ---
    owner = ev.get("owner")
    if owner is not None and not isinstance(owner, str):
        soft.append(f"owner={owner!r} not a string")
        owner = None
    next_date = ev.get("next_date")
    if next_date is not None:
        try:
            dt.date.fromisoformat(str(next_date))
            next_date = str(next_date)
        except ValueError:
            soft.append(f"next_date={next_date!r} not ISO")
            next_date = None
    trades = ev.get("trades")
    if trades is None:
        trades = []
    if not isinstance(trades, list):
        soft.append(f"trades={trades!r} not a list")
        trades = []
    bad_trades = [t for t in trades if t not in TRADES]
    if bad_trades:
        soft.append(f"dropped non-vocabulary trades {bad_trades!r}")
    trades = [t for t in trades if t in TRADES]
    tenure = ev.get("tenure")
    if tenure is not None and tenure not in TENURES:
        soft.append(f"tenure={tenure!r} not in enum")
        tenure = None

    clean = {
        "event_type": ev["event_type"],
        "project_name": ev.get("project_name") or None,
        "address": ev.get("address") or None,
        "applicant": ev.get("applicant") or None,
        "applicant_reps": json.dumps(reps) if reps else None,
        "job_contact": json.dumps(contact) if contact else None,
        "residential_units": _int_or_none(ev.get("residential_units"), "residential_units", soft),
        "commercial_sqft": _int_or_none(ev.get("commercial_sqft"), "commercial_sqft", soft),
        "dollar_value": _int_or_none(ev.get("dollar_value"), "dollar_value", soft),
        "stage": stage,
        "summary": summary.strip(),
        "source_page": _int_or_none(ev.get("source_page"), "source_page", soft),
        "confidence": float(conf),
        "owner": owner,
        "next_date": next_date,
        "trades": json.dumps(trades) if trades else None,
        "is_public_work": 1 if ev.get("is_public_work") else 0,
        "tenure": tenure,
    }
    return clean, soft


# --- Registry names ----------------------------------------------------------

def registry_names():
    with open(config.REGISTRY_PATH, "r", encoding="utf-8") as fh:
        towns = yaml.safe_load(fh) or []
    town_names, board_names = {}, {}
    for t in towns:
        town_names[t["town_id"]] = f"{t.get('name', t['town_id'])}, {t.get('state', '')}".rstrip(", ")
        for b in t.get("boards", []):
            board_names[(t["town_id"], b["board_id"])] = b.get("name", b["board_id"])
    return town_names, board_names


# --- Main loop ----------------------------------------------------------------

def review_status_for(conn, confidence: float) -> str:
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if n < CALIBRATION_EVENTS:
        return "needs_review"  # calibration period: a human reviews everything
    return "auto_approved" if confidence >= AUTO_APPROVE_CONFIDENCE else "needs_review"


def extract_doc(conn, doc, template, town_names, board_names, totals):
    path = os.path.join(config.REPO_ROOT, doc["local_path"].replace("/", os.sep))
    if not os.path.exists(path):
        raise ExtractionError(f"missing PDF on disk: {doc['local_path']}")

    town_name = town_names.get(doc["town_id"], doc["town_id"])
    board_name = board_names.get((doc["town_id"], doc["board_id"]), doc["board_id"] or "unknown")

    raw_events = []
    for chunk in chunk_pages(pdf_pages(path)):
        prompt = build_prompt(template, doc, town_name, board_name, chunk)
        text, usage = call_claude(prompt)
        totals["in"] += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        totals["out"] += usage.get("output_tokens", 0)
        totals["calls"] += 1
        parsed = parse_json_array(text)
        if parsed is None:  # one retry, per spec
            text, usage = call_claude(prompt + "\n\nReturn only valid JSON.")
            totals["in"] += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            totals["out"] += usage.get("output_tokens", 0)
            totals["calls"] += 1
            parsed = parse_json_array(text)
        if parsed is None:
            raise ExtractionError("unparseable JSON after retry")
        raw_events.extend(parsed)

    valid = []
    for ev in raw_events:
        clean, problems = validate_event(ev)
        if clean is None:
            print(f"      ! dropped invalid event ({problems}): {str(ev)[:140]}")
            continue
        if problems:
            print(f"      ~ soft-nulled fields ({'; '.join(problems)})")
        valid.append(clean)

    # Idempotent: re-running a doc never duplicates its events.
    conn.execute("DELETE FROM events WHERE doc_id = ?", (doc["doc_id"],))
    now = dt.datetime.now().isoformat(timespec="seconds")
    for ev in valid:
        conn.execute(
            "INSERT INTO events (event_id, doc_id, town_id, board_id, meeting_date, "
            "event_type, project_name, address, applicant, applicant_reps, job_contact, "
            "residential_units, commercial_sqft, dollar_value, stage, summary, "
            "source_page, confidence, created_at, review_status, "
            "owner, next_date, trades, is_public_work, tenure) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), doc["doc_id"], doc["town_id"], doc["board_id"],
             doc["meeting_date"], ev["event_type"], ev["project_name"], ev["address"],
             ev["applicant"], ev["applicant_reps"], ev["job_contact"],
             ev["residential_units"], ev["commercial_sqft"], ev["dollar_value"],
             ev["stage"], ev["summary"], ev["source_page"], ev["confidence"],
             now, review_status_for(conn, ev["confidence"]),
             ev["owner"], ev["next_date"], ev["trades"], ev["is_public_work"],
             ev["tenure"]),
        )
    conn.execute(
        "UPDATE documents SET extraction_status='done', processed_at=? WHERE doc_id=?",
        (now, doc["doc_id"]),
    )
    conn.commit()
    return valid


def main(argv=None):
    parser = argparse.ArgumentParser(description="Civica Signals extraction (Phase 2).")
    parser.add_argument("--limit", type=int, default=None,
                        help="max documents to process this run")
    args = parser.parse_args(argv)

    with open(PROMPT_PATH, "r", encoding="utf-8") as fh:
        template = fh.read()
    town_names, board_names = registry_names()

    conn = db.init_db()
    query = ("SELECT * FROM documents WHERE extraction_status='pending' "
             "ORDER BY meeting_date ASC, doc_id ASC")
    docs = conn.execute(query).fetchall()
    if args.limit:
        docs = docs[:args.limit]
    if not docs:
        print("No pending documents.")
        return

    print(f"Extracting {len(docs)} document(s) via `claude -p` "
          f"(model {config.ANTHROPIC_MODEL}, subscription auth — no API credits).\n")
    totals = {"in": 0, "out": 0, "calls": 0}
    done = failed = events_total = 0

    for doc in docs:
        label = f"{doc['town_id']}/{doc['board_id']} {doc['doc_type']} {doc['meeting_date']}"
        print(f"== {label} ==")
        try:
            events = extract_doc(conn, doc, template, town_names, board_names, totals)
        except (ExtractionError, subprocess.TimeoutExpired, Exception) as exc:
            failed += 1
            conn.execute(
                "UPDATE documents SET extraction_status='failed', processed_at=? WHERE doc_id=?",
                (dt.datetime.now().isoformat(timespec="seconds"), doc["doc_id"]),
            )
            conn.commit()
            print(f"   FAILED: {exc.__class__.__name__}: {exc}\n")
            continue
        done += 1
        events_total += len(events)
        if not events:
            print("   (no qualifying events)")
        for ev in events:
            print(f"   [{ev['event_type']} | {ev['stage']} | conf {ev['confidence']:.2f} "
                  f"| p.{ev['source_page']}] {ev['summary']}")
            if ev["applicant"]:
                print(f"      applicant: {ev['applicant']}")
            if ev["owner"]:
                print(f"      owner: {ev['owner']}")
            if ev["applicant_reps"]:
                print(f"      reps: {ev['applicant_reps']}")
            if ev["job_contact"]:
                print(f"      job contact: {ev['job_contact']}")
            v2 = []
            if ev["next_date"]:
                v2.append(f"next: {ev['next_date']}")
            if ev["trades"]:
                v2.append(f"trades: {ev['trades']}")
            if ev["is_public_work"]:
                v2.append("public work")
            if ev["tenure"]:
                v2.append(f"tenure: {ev['tenure']}")
            if v2:
                print(f"      {' | '.join(v2)}")
        print()

    print("=== extraction run summary ===")
    print(f"docs: {done} done, {failed} failed | events inserted: {events_total}")
    print(f"claude calls: {totals['calls']} | tokens: {totals['in']:,} in / {totals['out']:,} out")
    print("cost: $0 marginal (Claude subscription); tokens count against the plan's usage window.")
    conn.close()


if __name__ == "__main__":
    main()
