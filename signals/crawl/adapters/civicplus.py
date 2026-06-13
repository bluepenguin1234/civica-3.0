"""CivicPlus (CivicEngage) AgendaCenter adapter.

A board's agenda_url looks like:  https://<host>/AgendaCenter/<Name>-<catID>
The default page lists the current year; previous years come from the AJAX
endpoint  /AgendaCenter/UpdateCategoryList?catID=<catID>&year=<YYYY>  (verified
against danversma.gov), which returns the same ViewFile links. We sweep the
default page plus one request per year from the backfill floor to this year.

ViewFile link shape:  /AgendaCenter/ViewFile/<Type>/_<MMDDYYYY>-<id>
where <Type> is Agenda, Minutes, or — when a town posts the full application
packet behind a meeting — a packet/attachment type (e.g. Agenda_Packet, Packet,
Attachments). Step 6 widened the regex from the original (Agenda|Minutes) to any
type segment and classifies it; unknown types are ignored so we don't grab junk.

NOTE (verified 2026-06-12): the North Shore CivicPlus towns (Danvers, Beverly,
Salem, Swampscott) do NOT expose a separate packet type via AgendaCenter — only
Agenda + Minutes. This adapter captures packets wherever a town does post them,
but for those towns the packet pass legitimately finds zero.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlsplit, urlunsplit

from signals import config
from signals.crawl.adapters.base import BaseAdapter, DocumentRef

_CATID_RE = re.compile(r"-(\d+)/?$")
_VIEWFILE_RE = re.compile(
    r"/AgendaCenter/ViewFile/([A-Za-z0-9_]+)/_(\d{2})(\d{2})(\d{4})-(\d+)"
)


def _classify(type_seg):
    """Map a ViewFile <Type> segment to a doc_type, or None to ignore it."""
    t = type_seg.lower()
    if "packet" in t or "attach" in t:
        return "packet"
    if t == "agenda":
        return "agenda"
    if t == "minutes":
        return "minutes"
    return None  # unknown ViewFile type — don't download it


def _iso(mm, dd, yyyy):
    try:
        return dt.date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


class CivicPlusAdapter(BaseAdapter):
    platform = "civicplus"

    def discover(self, board):
        candidates = {
            (board.get("agenda_url") or "").strip(),
            (board.get("minutes_url") or "").strip(),
        }
        candidates.discard("")
        candidates.discard("TODO")
        if not candidates:
            return []
        base = sorted(candidates)[0]

        parts = urlsplit(base)
        m = _CATID_RE.search(parts.path)
        if not m:
            return []
        cat_id = m.group(1)
        host = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        board_id = board.get("board_id", "board")

        start_year = int(config.crawl_cutoff_date()[:4])
        this_year = dt.date.today().year
        list_urls = [base] + [
            f"{host}/AgendaCenter/UpdateCategoryList?catID={cat_id}&year={y}"
            for y in range(start_year, this_year + 1)
        ]

        seen, refs = set(), []
        for list_url in list_urls:
            html = self.fetch(list_url)
            if not html:
                continue
            for type_seg, mm, dd, yyyy, vid in _VIEWFILE_RE.findall(html):
                key = (type_seg, mm, dd, yyyy, vid)
                if key in seen:
                    continue
                seen.add(key)
                doc_type = _classify(type_seg)
                if doc_type is None:
                    continue  # not an agenda/minutes/packet ViewFile — skip
                iso = _iso(mm, dd, yyyy)
                refs.append(DocumentRef(
                    url=f"{host}/AgendaCenter/ViewFile/{type_seg}/_{mm}{dd}{yyyy}-{vid}",
                    doc_type=doc_type,
                    guessed_meeting_date=iso,
                    filename=f"{board_id}_{doc_type}_{iso or 'undated'}_{vid}.pdf",
                ))
        return refs
