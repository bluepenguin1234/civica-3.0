#!/usr/bin/env python3
"""
crawl.py — Civica Signals crawl orchestrator.

Reads the town registry, picks a CMS adapter per active town, discovers new
agenda/minutes PDFs, downloads them politely (one request per
config.CRAWL_DELAY_SECONDS per host, with bounded retries/backoff), and records
one manifest row per document (sha256 doc_id, extraction_status='pending', or
'skipped_scan' for image-only PDFs). Documents already in the manifest (by
source_url or content hash) are skipped, so re-running is cheap and idempotent.
Each town is wrapped in try/except so one broken town never kills the run.

Usage (from the repo root):
    python -m signals.crawl.crawl                    # all active towns
    python -m signals.crawl.crawl --town danvers_ma  # one town (debugging)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import os
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml
from bs4 import BeautifulSoup

from signals import config, db
from signals.crawl.adapters.civicplus import CivicPlusAdapter
from signals.crawl.adapters.civicclerk import CivicClerkAdapter
from signals.crawl.adapters.granicus import GranicusAdapter
from signals.crawl.adapters.generic_html import GenericHtmlAdapter

sys.stdout.reconfigure(encoding="utf-8")

ADAPTERS = {
    "civicplus": CivicPlusAdapter,
    "civicclerk": CivicClerkAdapter,
    "granicus": GranicusAdapter,
    "generic_html": GenericHtmlAdapter,
}


class PoliteFetcher:
    """Centralized HTTP: per-host rate limiting, shared UA, bounded retries."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = config.USER_AGENT
        self._last_hit = {}  # host -> monotonic timestamp

    def _wait(self, url):
        host = urlsplit(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            gap = config.CRAWL_DELAY_SECONDS - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last_hit[host] = time.monotonic()

    def request(self, url):
        """GET with rate limiting + retries. Returns Response or None."""
        for attempt in range(1, config.MAX_RETRIES + 1):
            self._wait(url)
            try:
                resp = self._session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                if attempt == config.MAX_RETRIES:
                    print(f"      ! request error ({exc.__class__.__name__}); gave up: {url}")
                    return None
                time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
                continue
            if resp.status_code < 400:
                return resp
            if 400 <= resp.status_code < 500:
                return None  # missing/forbidden — retrying won't help
            if attempt == config.MAX_RETRIES:
                print(f"      ! HTTP {resp.status_code}; gave up: {url}")
                return None
            time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
        return None

    def fetch_text(self, url):
        resp = self.request(url)
        return resp.text if resp is not None else None

    def fetch_bytes(self, url):
        resp = self.request(url)
        return resp.content if resp is not None else None


def analyze_pdf(content):
    """Return (page_count, is_scanned). is_scanned=1 if <50 chars/page average."""
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = pdf.pages
            n = len(pages)
            if n == 0:
                return 0, 0
            chars = sum(len(p.extract_text() or "") for p in pages)
            return n, (1 if (chars / n) < 50 else 0)
    except Exception:
        return None, 0


def sanitize_filename(name, fallback):
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name or name in (".", ".."):
        name = fallback
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def load_towns():
    with open(config.REGISTRY_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def crawl_town(town, fetcher, conn, backfill_start):
    town_id = town.get("town_id", "?")
    platform = town.get("platform", "")
    stats = {"found": 0, "new": 0, "dup": 0, "skip": 0, "scan": 0,
             "large": 0, "fail": 0, "note": ""}

    adapter_cls = ADAPTERS.get(platform)
    if adapter_cls is None or platform == "manual":
        stats["note"] = f"no adapter for platform {platform!r}"
        print(f"  - {stats['note']} — skipping {town_id}")
        return stats
    adapter = adapter_cls(fetcher.fetch_text)

    for board in town.get("boards", []):
        board_id = board.get("board_id", "board")
        try:
            refs = adapter.discover(board)
        except NotImplementedError as exc:
            stats["note"] = str(exc)
            print(f"  - {town_id}/{board_id}: {exc}")
            continue
        print(f"  - {town_id}/{board_id}: discovered {len(refs)} document(s)")

        for ref in refs:
            if ref.guessed_meeting_date and ref.guessed_meeting_date < backfill_start:
                continue
            stats["found"] += 1

            if conn.execute("SELECT 1 FROM documents WHERE source_url = ? LIMIT 1",
                            (ref.url,)).fetchone():
                stats["skip"] += 1
                continue

            content = fetcher.fetch_bytes(ref.url)
            if not content or content[:5] != b"%PDF-":
                stats["fail"] += 1
                print(f"      ! not a PDF / download failed: {ref.url}")
                continue

            doc_id = hashlib.sha256(content).hexdigest()
            if conn.execute("SELECT 1 FROM documents WHERE doc_id = ? LIMIT 1",
                            (doc_id,)).fetchone():
                stats["dup"] += 1
                continue

            dest_dir = os.path.join(config.RAW_DIR, town_id, board_id)
            os.makedirs(dest_dir, exist_ok=True)
            filename = sanitize_filename(ref.filename, f"{board_id}_{doc_id[:12]}.pdf")
            with open(os.path.join(dest_dir, filename), "wb") as fh:
                fh.write(content)
            local_path = f"signals/raw/{town_id}/{board_id}/{filename}"

            page_count, is_scanned = analyze_pdf(content)
            if page_count is not None and page_count > config.PACKET_PAGE_CAP:
                # Step 6 page cap: archive it, but never feed a giant packet to
                # extraction. extract.py only picks up extraction_status='pending'.
                status = "skipped_large"
                stats["large"] += 1
                print(f"      ~ {page_count}p > cap {config.PACKET_PAGE_CAP} — "
                      f"archived as skipped_large: {ref.doc_type}")
            elif is_scanned:
                status = "skipped_scan"
                stats["scan"] += 1
            else:
                status = "pending"
            try:
                conn.execute(
                    "INSERT INTO documents (doc_id, town_id, board_id, doc_type, "
                    "meeting_date, source_url, local_path, fetched_at, processed_at, "
                    "extraction_status, page_count, is_scanned) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, town_id, board_id, ref.doc_type, ref.guessed_meeting_date,
                     ref.url, local_path, dt.datetime.now().isoformat(timespec="seconds"),
                     None, status, page_count, is_scanned),
                )
                conn.commit()
                stats["new"] += 1
            except sqlite3.IntegrityError:
                stats["dup"] += 1
    return stats


def _clean_bid_html(html):
    """Strip a CivicPlus bid detail page to readable key/value text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    txt = re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True))
    return re.sub(r"\n{2,}", "\n", txt).strip()


def _bid_closing_iso(text):
    """Parse the 'Closing Date/Time' field of a bid page to an ISO date."""
    m = re.search(r"Closing Date/Time\s*:?\s*\n?\s*([^\n]+)", text, re.I)
    if not m:
        return None
    d = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", m.group(1))
    if not d:
        return None
    mo, dy, yr = d.groups()
    try:
        return dt.date(int(yr), int(mo), int(dy)).isoformat()
    except ValueError:
        return None


def crawl_bids(town, fetcher, conn, cutoff):
    """Crawl the town's public bids/RFP module (CivicPlus). Bid detail pages are
    HTML, not PDF: each is saved with a `.txt` text sidecar so extract.py reads
    it the same way it reads scanned-PDF `.ocr.txt` sidecars. Only the N most
    recent bids are fetched; stale closed bids (closing date before the lookback
    cutoff) are skipped."""
    bids_url = (town.get("bids_url") or "").strip()
    if not bids_url:
        return None
    town_id = town["town_id"]
    parts = urlsplit(bids_url)
    host = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    index_url = bids_url + ("&" if parts.query else "?") + "showAllBids=on"
    stats = {"found": 0, "new": 0, "dup": 0, "skip": 0, "fail": 0}

    html = fetcher.fetch_text(index_url)
    if not html:
        print(f"  - {town_id}/bids: index fetch failed")
        return stats
    ids = sorted({int(m) for m in re.findall(r"bids\.aspx\?bidID=(\d+)", html, re.I)},
                 reverse=True)[:config.BIDS_RECENT_COUNT]
    print(f"  - {town_id}/bids: checking {len(ids)} most-recent bid(s)")
    for bid in ids:
        url = f"{host}/bids.aspx?bidID={bid}"
        if conn.execute("SELECT 1 FROM documents WHERE source_url=? LIMIT 1",
                        (url,)).fetchone():
            stats["skip"] += 1
            continue
        detail = fetcher.fetch_text(url)
        if not detail:
            stats["fail"] += 1
            continue
        text = _clean_bid_html(detail)
        closing = _bid_closing_iso(text)
        stats["found"] += 1
        if closing and closing < cutoff:
            continue  # stale closed bid outside the lookback window
        data = detail.encode("utf-8")
        doc_id = hashlib.sha256(data).hexdigest()
        if conn.execute("SELECT 1 FROM documents WHERE doc_id=? LIMIT 1",
                        (doc_id,)).fetchone():
            stats["dup"] += 1
            continue
        dest = os.path.join(config.RAW_DIR, town_id, "bids")
        os.makedirs(dest, exist_ok=True)
        fname = f"bid_{bid}.html"
        with open(os.path.join(dest, fname), "w", encoding="utf-8") as fh:
            fh.write(detail)
        with open(os.path.join(dest, fname + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(f"[PAGE 1]\nPUBLIC BID / RFP NOTICE\n\n{text}")
        conn.execute(
            "INSERT INTO documents (doc_id, town_id, board_id, doc_type, meeting_date, "
            "source_url, local_path, fetched_at, processed_at, extraction_status, "
            "page_count, is_scanned) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, town_id, "bids", "bid", closing, url,
             f"signals/raw/{town_id}/bids/{fname}",
             dt.datetime.now().isoformat(timespec="seconds"), None, "pending", 1, 0))
        conn.commit()
        stats["new"] += 1
    return stats


def print_report(results, active):
    print("\n=== crawl coverage report ===")
    hdr = (f"{'town':<16}{'found':>6}{'new':>5}{'dup':>5}{'skip':>6}"
           f"{'scan':>6}{'large':>6}{'fail':>6}  notes")
    print(hdr)
    print("-" * len(hdr))
    flags = []
    for town in active:
        tid = town.get("town_id", "?")
        s = results.get(tid, {})
        note = s.get("error") or s.get("note") or ""
        print(f"{tid:<16}{s.get('found', 0):>6}{s.get('new', 0):>5}{s.get('dup', 0):>5}"
              f"{s.get('skip', 0):>6}{s.get('scan', 0):>6}{s.get('large', 0):>6}"
              f"{s.get('fail', 0):>6}  {note}")
        if s.get("found", 0) == 0 and not s.get("error"):
            flags.append(tid)
    for tid in flags:
        print(f"[FLAG] {tid}: active but discovered ZERO documents — "
              f"check the registry entry or the site.")
    if not flags:
        print("All active towns discovered at least one document.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Civica Signals crawl orchestrator.")
    parser.add_argument("--town", help="crawl a single town_id (for debugging)")
    args = parser.parse_args(argv)

    towns = load_towns()
    active = [t for t in towns if t.get("status") == "active"]
    if args.town:
        match = [t for t in towns if t.get("town_id") == args.town]
        if not match:
            parser.error(f"town_id {args.town!r} not found in the registry")
        if match[0].get("status") != "active":
            print(f"note: {args.town} status is {match[0].get('status')!r} "
                  f"(not 'active') — crawling it anyway")
        active = match

    if not active:
        print("No active towns to crawl.")
        return

    conn = db.init_db()
    fetcher = PoliteFetcher()
    cutoff = config.crawl_cutoff_date()
    print(f"Crawling {len(active)} town(s); lookback cutoff {cutoff} "
          f"(~{config.CRAWL_LOOKBACK_DAYS}d); "
          f"{config.CRAWL_DELAY_SECONDS:.0f}s between requests per host.\n")

    results = {}
    for town in active:
        town_id = town.get("town_id", "?")
        print(f"== {town_id} ({town.get('platform')}) ==")
        try:
            stats = crawl_town(town, fetcher, conn, cutoff)
        except Exception as exc:  # one broken town must not kill the run
            print(f"  !! {town_id} failed: {exc.__class__.__name__}: {exc}")
            results[town_id] = {"found": 0, "new": 0, "dup": 0, "skip": 0, "scan": 0,
                                "large": 0, "fail": 0, "error": f"{exc.__class__.__name__}: {exc}"}
            continue
        try:
            bids = crawl_bids(town, fetcher, conn, cutoff)
            if bids:
                for k in ("found", "new", "dup", "skip", "fail"):
                    stats[k] = stats.get(k, 0) + bids.get(k, 0)
        except Exception as exc:  # a bids failure must not drop the board results
            print(f"  - {town_id}/bids failed: {exc.__class__.__name__}: {exc}")
        results[town_id] = stats

    print_report(results, active)
    conn.close()


if __name__ == "__main__":
    main()
