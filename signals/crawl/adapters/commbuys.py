"""COMMBUYS (statewide MA procurement) adapter — Step S1.

COMMBUYS runs on Periscope/BidSync's JSF/PrimeFaces stack. The public open-bids
search server-renders results, but the organization filter is a stateful form
POST (GET query params are ignored), so we:

  1. prime a session at the BSO entry point (sets JSESSIONID / _csrf cookies),
  2. GET the open-bids page for a fresh javax.faces.ViewState + the org <select>,
  3. POST bidSearchForm with the town's organization option selected.

The response is the same datatable filtered to that buyer, so one POST yields a
town's open bids out of the ~900 statewide — far fewer requests than sweeping
every paginated page. Detail pages live at /bso/external/bidDetail.sda?docId=...

The server declares gzip but ships a body our client can't inflate, so every
request sends Accept-Encoding: identity.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from signals import config

CB_HEADERS = {"Accept-Encoding": "identity"}
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_HOST = "https://www.commbuys.com"


def prime(fetcher):
    """Establish a session (cookies) before any search. Call once per crawl."""
    fetcher.get_text(config.COMMBUYS_ENTRY, extra_headers=CB_HEADERS)


def _open_state(fetcher):
    """GET the open-bids page; return (form_fields, org_option_map) or None."""
    html = fetcher.get_text(config.COMMBUYS_OPENBIDS, extra_headers=CB_HEADERS)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="bidSearchForm")
    if not form:
        return None
    fields = {i.get("name"): i.get("value", "")
              for i in form.find_all("input") if i.get("name")}
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    if vs:
        fields["javax.faces.ViewState"] = vs.get("value", "")
    sel = form.find("select", id="bidSearchForm:organization")
    optmap = {o.get_text(strip=True): o.get("value")
              for o in sel.find_all("option")} if sel else {}
    return fields, optmap


def close_iso(raw):
    """'07/02/2026 14:00:00' -> '2026-07-02', or None."""
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    mm, dd, yyyy = m.groups()
    try:
        return dt.date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def _parse_rows(html):
    """Parse the filtered results datatable into bid dicts."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="bidDetail.sda"]'):
        m = re.search(r"docId=([^&]+)", a.get("href", ""))
        if not m:
            continue
        bid_number = m.group(1)
        if bid_number in seen:
            continue
        tr = a.find_parent("tr")
        if not tr:
            continue
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        # Verified layout: [docId, docId, buyer, '', '', contact, title, close].
        # Parse defensively so a column shuffle doesn't silently mislabel fields.
        close = next((c for c in cells if _DATE_RE.search(c)), None)
        buyer = cells[2] if len(cells) > 2 else None
        contact = cells[5] if len(cells) > 5 else None
        cand = [c for c in cells if c and c != bid_number and c not in (buyer, contact, close)
                and not _DATE_RE.search(c)]
        cand.sort(key=len, reverse=True)
        title = cand[0] if cand else None
        seen.add(bid_number)
        out.append({
            "bid_number": bid_number,
            "buyer": buyer or None,
            "contact": contact or None,
            "title": title or None,
            "close_raw": close,
            "close_iso": close_iso(close),
            "detail_url": urljoin(_HOST, a.get("href")),
        })
    return out


def search_org_bids(fetcher, org_name):
    """Return the open bids for one purchasing org. None if the org string isn't
    in the COMMBUYS dropdown (so the caller can flag a stale registry value)."""
    state = _open_state(fetcher)
    if not state:
        return []
    fields, optmap = state
    org_value = optmap.get(org_name)
    if org_value is None:
        return None
    data = dict(fields)
    data["bidSearchForm:organization"] = org_value
    data["bidSearchForm:btnBidSearch"] = "bidSearchForm:btnBidSearch"
    data["bidSearchForm"] = "bidSearchForm"
    html = fetcher.post_text(config.COMMBUYS_OPENBIDS, data, extra_headers=CB_HEADERS)
    return _parse_rows(html) if html else []
