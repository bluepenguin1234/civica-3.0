"""Open-data building-permit adapter (Step S3).

For cities that publish issued building permits as an open dataset (CKAN/CSV),
this maps the structured rows straight into permit dicts — no LLM, because the
fields are already clean. Only open-data cities reach here; the North Shore towns
publish permits through robots-blocked OpenGov/ViewPoint Cloud portals and have
no permit source (see the registry permits: blocks, status=blocked).

Compliance: the datastore/search API is robots-disallowed, so we take the full
bulk CSV from the /dataset resource path (allowed) and filter client-side. It's a
large weekly pull, streamed to a temp file and parsed with the csv module (which
handles the quoted multi-line description fields the data contains).

Per-city column names differ, so each source declares a `profile` in its registry
permits: block; the column map lives in PROFILES here.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import re
import sys
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from signals import config

csv.field_size_limit(10 * 1024 * 1024)  # some description fields are large

PROFILES = {
    "boston": {
        "ref_number": "permitnumber", "issued_date": "issued_date",
        "applicant": "applicant", "value": "declared_valuation",
        "worktype": "worktype", "typedescr": "permittypedescr",
        "description": "description", "occupancy": "occupancytype",
        "sqft": "sq_feet", "address": "address", "city": "city",
        "state": "state", "zip": "zip",
    },
}

# Stated work -> trade vocabulary. Only keywords actually present in the permit's
# worktype/description set a trade (document-bound; no inferring the full crew).
_TRADE_KEYWORDS = [
    ("roof", "roofing"), ("electric", "electrical"), ("plumb", "plumbing"),
    ("hvac", "hvac"), ("mechanical", "hvac"), ("mason", "masonry"),
    ("demolition", "demolition"), ("raze", "demolition"),
    ("solar", "solar_energy"), ("photovoltaic", "solar_energy"),
    ("paving", "paving_asphalt"), ("asphalt", "paving_asphalt"),
    ("driveway", "paving_asphalt"), ("excavat", "site_excavation"),
    ("foundation", "concrete_foundation"), ("concrete", "concrete_foundation"),
    ("siding", "drywall_finishes"), ("addition", "framing_carpentry"),
    ("framing", "framing_carpentry"), ("landscap", "landscaping"),
]
_NEWCON = ("new construction", "erect", "new building", "newcon")
_RES = ("fam", "dwelling", "residential", "condo", "apart", "multi")


def _iso(raw):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return dt.date(int(yyyy), int(mm), int(dd)).isoformat()
        except ValueError:
            return None
    return None


def _int(raw):
    try:
        return int(round(float(str(raw).replace(",", "").replace("$", "").strip())))
    except (ValueError, TypeError):
        return None


def _classify(text):
    """Return (trades, is_new_dwelling, is_demo, is_solar) from the work text."""
    t = text.lower()
    trades = []
    for kw, trade in _TRADE_KEYWORDS:
        if kw in t and trade not in trades:
            trades.append(trade)
    is_solar = "solar" in t or "photovoltaic" in t
    is_demo = "demolition" in t or "raze" in t or re.search(r"\bdemo\b", t) is not None
    is_newcon = any(k in t for k in _NEWCON)
    is_new_dwelling = is_newcon and any(k in t for k in _RES)
    return trades, is_new_dwelling, is_demo, is_solar


def resolve_csv_url(fetcher, dataset_url):
    """Find the current CSV resource download link on the CKAN dataset page."""
    html = fetcher.get_text(dataset_url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/download/" in href and href.lower().endswith(".csv"):
            return urljoin(dataset_url, href)
    # fallback: a data-format=csv resource link
    for a in soup.select('a[href*="/resource/"][href*="/download/"]'):
        return urljoin(dataset_url, a["href"])
    return None


def fetch_permits(fetcher, source, cutoff_iso):
    """Stream the city's permit CSV to a temp file and return the bounded slice of
    recent + relevant permits (value >= threshold OR notable type), newest first.
    `source` is the town's registry permits: block."""
    profile = PROFILES.get(source.get("profile"))
    if profile is None:
        raise ValueError(f"no permit profile for {source.get('profile')!r}")
    csv_url = resolve_csv_url(fetcher, source["url"])
    if not csv_url:
        raise ValueError(f"could not resolve CSV link from {source['url']}")

    tmp_dir = os.path.join(config.RAW_DIR, "_permit_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, re.sub(r"\W+", "_", source["profile"]) + "_full.csv")
    resp = fetcher.stream(csv_url)
    if resp is None:
        raise ValueError(f"permit CSV download failed: {csv_url}")
    with open(tmp_path, "wb") as fh:
        for chunk in resp.iter_content(1 << 20):
            fh.write(chunk)
    resp.close()

    out = []
    thresh = config.PERMIT_VALUE_THRESHOLD
    with open(tmp_path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            issued = _iso(row.get(profile["issued_date"], ""))
            if not issued or issued < cutoff_iso:
                continue
            worktext = " ".join(filter(None, (
                row.get(profile["worktype"], ""), row.get(profile["typedescr"], ""),
                row.get(profile["description"], ""), row.get(profile["occupancy"], ""))))
            trades, new_dwelling, is_demo, is_solar = _classify(worktext)
            value = _int(row.get(profile["value"]))
            notable = new_dwelling or is_demo or is_solar
            if not notable and not (value is not None and value >= thresh):
                continue
            addr_bits = [row.get(profile["address"], ""), row.get(profile["city"], ""),
                         row.get(profile["state"], ""), row.get(profile["zip"], "")]
            address = ", ".join(b.strip() for b in addr_bits[:2] if b.strip())
            tail = " ".join(b.strip() for b in addr_bits[2:] if b.strip())
            if tail:
                address = f"{address} {tail}".strip()
            out.append({
                "ref_number": (row.get(profile["ref_number"]) or "").strip() or None,
                "issued_date": issued,
                "applicant": (row.get(profile["applicant"]) or "").strip() or None,
                "value": value,
                "worktype": (row.get(profile["typedescr"]) or row.get(profile["worktype"]) or "").strip() or None,
                "description": (row.get(profile["description"]) or "").strip() or None,
                "address": address or None,
                "trades": trades,
                "is_new_dwelling": new_dwelling, "is_demo": is_demo, "is_solar": is_solar,
            })
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    out.sort(key=lambda p: p["issued_date"], reverse=True)
    return out[:config.PERMIT_MAX_PER_TOWN]
