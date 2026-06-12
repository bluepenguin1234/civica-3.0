"""Generic HTML fallback adapter.

For towns with no dedicated CMS adapter: fetch the configured agenda/minutes
page(s), collect every linked PDF, keep those that look like agendas or minutes
(by link text / URL keyword), and guess a meeting date from any date pattern in
the link text or URL. Untested against a live site — wire it to a real town and
verify when one is added with platform: generic_html.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from signals.crawl.adapters.base import BaseAdapter, DocumentRef

_AGENDA_KW = ("agenda",)
_MINUTES_KW = ("minutes", "mins")
_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[-/._](\d{1,2})[-/._](\d{2,4})"), ("m", "d", "y")),
]


def _guess_date(text):
    for rx, order in _DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        y = parts["y"]
        if len(y) == 2:
            y = "20" + y
        try:
            return dt.date(int(y), int(parts["m"]), int(parts["d"])).isoformat()
        except ValueError:
            continue
    return None


class GenericHtmlAdapter(BaseAdapter):
    platform = "generic_html"

    def discover(self, board):
        board_id = board.get("board_id", "board")
        seen, refs = set(), []
        for field in ("agenda_url", "minutes_url"):
            page = (board.get(field) or "").strip()
            if not page or page == "TODO":
                continue
            html = self.fetch(page)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ".pdf" not in href.lower():
                    continue
                url = urljoin(page, href)
                if url in seen:
                    continue
                label = f"{a.get_text(' ', strip=True)} {href}".lower()
                if any(k in label for k in _AGENDA_KW):
                    doc_type = "agenda"
                elif any(k in label for k in _MINUTES_KW):
                    doc_type = "minutes"
                else:
                    continue  # not obviously an agenda/minutes PDF — skip the noise
                seen.add(url)
                iso = _guess_date(label)
                refs.append(DocumentRef(
                    url=url,
                    doc_type=doc_type,
                    guessed_meeting_date=iso,
                    filename=href.rsplit("/", 1)[-1] or f"{board_id}_{doc_type}.pdf",
                ))
        return refs
