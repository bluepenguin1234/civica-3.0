"""
Civica Town-Level Data Downloader
=================================
Fetches the THREE new datasets required for the town-level restructure (Level B).
Everything else (BEA, QCEW, FHFA, CBP, BPS, IRS migration, NFIP, NOAA, USFS, RUCC,
county population, NIBRS) is already on disk and is reused — Zillow is dropped.

  1. Census sub-est        → town population + town→county crosswalk + town growth
  2. IRS SOI ZIP AGI (x2)  → town income level + town income growth
  3. Census ZCTA→Place rel → allocate ZIP AGI to places

Run (locally, where civica_data/ lives):  python download_town_data.py

NOTE: This must run from a machine/network that can reach census.gov and irs.gov.
The Claude-Code-on-the-web container blocks those hosts (host_not_allowed), so run
this in a LOCAL Claude Code session or a normal terminal. URLs are best-known
patterns as of mid-2026; the script falls back across vintages/years and prints
exactly what it saved so you can confirm.
"""

import os
import sys
from pathlib import Path

import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE = Path(__file__).parent.parent / 'civica_data'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    )
}


def section(t):
    print(f"\n{'='*64}\n  {t}\n{'='*64}")


def _looks_real(path, min_bytes=5_000):
    p = Path(path)
    if not p.exists() or p.stat().st_size < min_bytes:
        return False
    with open(p, 'rb') as f:
        head = f.read(1)
    return head not in (b'<', b'{')  # reject HTML error pages / JSON denials


def download(url, dest, label, force=False):
    """Download url→dest. Returns True on success. Skips if a real file exists."""
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not force and _looks_real(p):
        print(f"  Already present: {p.name}")
        return True
    print(f"  Trying: {label}\n    {url}")
    try:
        r = requests.get(url, stream=True, timeout=300, headers=HEADERS)
        r.raise_for_status()
        with open(p, 'wb') as f:
            for chunk in r.iter_content(65_536):
                if chunk:
                    f.write(chunk)
        if _looks_real(p):
            print(f"    Saved {p.name} ({p.stat().st_size/1e6:.1f} MB)")
            return True
        print("    Got a non-data response (HTML/empty); discarding.")
        p.unlink(missing_ok=True)
        return False
    except Exception as e:
        print(f"    Failed: {e}")
        p.unlink(missing_ok=True)
        return False


def try_urls(urls, dest, label):
    """Attempt several candidate URLs; stop at first success."""
    for u in urls:
        if download(u, dest, label):
            return True
    return False


# ── 1. Census sub-est (town population) ──────────────────────────────────────────
def get_subest():
    section("1/3  Census sub-est — town population + crosswalk")
    dest = BASE / 'census_population' / 'sub-est.csv'
    base = 'https://www2.census.gov/programs-surveys/popest/datasets'
    # Try newest vintage first, then fall back one year.
    candidates = [
        f'{base}/2020-2025/cities/totals/sub-est2025.csv',
        f'{base}/2020-2024/cities/totals/sub-est2024.csv',
        f'{base}/2020-2023/cities/totals/sub-est2023_all.csv',
    ]
    ok = try_urls(candidates, dest, 'Census sub-est (subcounty population)')
    if not ok:
        print("  !! sub-est is REQUIRED — without it there is no town universe.")
    return ok


# ── 2. IRS SOI ZIP AGI (town income, two years for growth) ───────────────────────
def get_irs_zip():
    section("2/3  IRS SOI ZIP AGI — town income (need 2 years for growth)")
    folder = BASE / 'irs_zip'
    got = []
    # IRS publishes YYzpallagi.csv; grab the two most recent that exist.
    # As of mid-2026 the likely-available years are 2022 and 2021.
    for yy in ['22', '21', '20']:
        dest = folder / f'{yy}zpallagi.csv'
        url = f'https://www.irs.gov/pub/irs-soi/{yy}zpallagi.csv'
        if download(url, dest, f'IRS ZIP AGI 20{yy}'):
            got.append(yy)
        if len(got) == 2:
            break
    if len(got) == 0:
        print("  !! No IRS ZIP file fetched — town income unavailable.")
    elif len(got) == 1:
        print(f"  Only one year ({got[0]}) — income LEVEL works, income GROWTH will "
              "fall back to county BEA growth (see TOWN_HANDOFF.md §5).")
    else:
        print(f"  Got two years: {got} — income level + growth both available.")
    return len(got) >= 1


# ── 3. Census ZCTA→Place relationship (crosswalk) ────────────────────────────────
def get_crosswalk():
    section("3/3  Census ZCTA→Place relationship file (crosswalk)")
    dest = BASE / 'crosswalks' / 'zcta_place_rel_2020.txt'
    base = 'https://www2.census.gov/geo/docs/maps-data/data/rel2020'
    candidates = [
        f'{base}/zcta520/tab20_zcta520_place20_natl.txt',
        f'{base}/zcta20/tab20_zcta20_place20_natl.txt',
    ]
    ok = try_urls(candidates, dest, 'ZCTA→Place 2020 relationship')
    if not ok:
        print("  !! Crosswalk missing — ZIP AGI can't be allocated to places.\n"
              "     Find it via: census.gov → Geographies → Relationship Files → "
              "2020 → ZCTA to Place.")
    return ok


def main():
    print("Civica Town Data Downloader")
    print("Target:", BASE.resolve())
    BASE.mkdir(parents=True, exist_ok=True)

    results = {
        'sub-est (REQUIRED)': get_subest(),
        'IRS ZIP AGI':        get_irs_zip(),
        'ZCTA→Place crosswalk': get_crosswalk(),
    }

    section("Summary")
    for name, ok in results.items():
        print(f"  [{'OK ' if ok else 'XX '}] {name}")
    if not all(results.values()):
        print("\nSome downloads failed. If a host is blocked (host_not_allowed), run "
              "this in a LOCAL session. If a URL 404s, the vintage/year may have moved "
              "— check the source site and update the candidate URLs above.")
        sys.exit(1)
    print("\nAll three datasets present. Next: run town_scoring_engine.py "
          "(see TOWN_HANDOFF.md).")


if __name__ == '__main__':
    main()
