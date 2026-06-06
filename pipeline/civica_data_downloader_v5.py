"""
Civica Data Downloader v5
=========================
Updates 5 datasets to their newest available releases (as of May 2026).

  Census Population  2023 → 2025  (released Mar 2026)
  Census CBP         2022 → 2023  (released Jun 2025)
  Census BPS         2022 → 2025  (released May 2026)
  BLS QCEW           2023 → 2024  (released Sep 2025)
  NOAA Storm Events  2019-2023 → 2020-2024  (rolling 5-year window)

Run: python civica_data_downloader_v5.py
"""

import gzip
import os
import re
import shutil
import sys
import zipfile

import requests
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent / 'civica_data'
HEADERS  = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0 Safari/537.36'
    )
}
RESULTS = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def section(title):
    print(f"\n{'='*64}\n  {title}\n{'='*64}")


def _is_real_file(path, min_bytes=5_000):
    p = Path(path)
    if not p.exists() or p.stat().st_size < min_bytes:
        return False
    with open(p, 'rb') as f:
        hdr = f.read(4)
    return hdr[:1] not in (b'<', b'{')


def download(url, dest, label, expected_mb=None, force=False):
    p = Path(dest)
    if not force and _is_real_file(p):
        print(f"  Already exists: {p.name}")
        return True
    hint = f' (~{expected_mb} MB)' if expected_mb else ''
    print(f"  Downloading{hint}: {label}")
    print(f"  URL: {url}")
    try:
        r = requests.get(url, stream=True, timeout=300, headers=HEADERS)
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        done  = 0
        with open(p, 'wb') as f:
            for chunk in r.iter_content(65_536):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f'\r    {min(done/total*100,100):5.1f}%  {done/1_048_576:.1f} MB', end='', flush=True)
        print(f'\r    Done — {p.name} ({done/1_048_576:.1f} MB)        ')
        return _is_real_file(p, min_bytes=1_000)
    except requests.HTTPError as e:
        print(f'\r    HTTP {e.response.status_code} — {url}')
    except Exception as e:
        print(f'\r    ERROR: {e}')
    return False


def extract_zip(zip_path, dest_dir):
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(dest_dir)
        print(f'    Extracted → {Path(dest_dir).name}/')
        return True
    except Exception as e:
        print(f'    Extract error: {e}')
        return False


def ungzip(gz_path, out_path):
    try:
        with gzip.open(gz_path, 'rb') as f_in, open(out_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
        return True
    except Exception as e:
        print(f'    Gunzip error: {e}')
        return False


def scrape_links(url, pattern):
    try:
        r = requests.get(url, timeout=30, headers=HEADERS)
        r.raise_for_status()
        return re.findall(pattern, r.text)
    except Exception as e:
        print(f'  Could not fetch {url}: {e}')
        return []


def run(key, fn):
    try:
        fn()
        RESULTS[key] = True
    except Exception as e:
        print(f'\n  FAILED ({key}): {e}')
        import traceback; traceback.print_exc()
        RESULTS[key] = False


# ── 1. Census Population Estimates 2025 ───────────────────────────────────────

def update_census_population():
    section('Census Population Estimates 2025 (released Mar 2026)')
    dest = BASE_DIR / 'census_population'
    dest.mkdir(parents=True, exist_ok=True)

    fname = 'co-est2025-alldata.csv'
    p = dest / fname
    if _is_real_file(p):
        print(f'  Already exists: {fname}')
        return

    urls = [
        f'https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/{fname}',
        f'https://www2.census.gov/programs-surveys/popest/datasets/2021-2025/counties/totals/{fname}',
    ]
    for url in urls:
        if download(url, p, 'Census PopEst 2025', expected_mb=6):
            print('  Census Population 2025 ready.')
            return
        if p.exists(): p.unlink()

    print('  Manual download:')
    print('  1. https://www.census.gov/programs-surveys/popest/data/tables.html')
    print('  2. County → 2025 → co-est2025-alldata.csv')
    print(f'  3. Save to: {dest}')
    raise RuntimeError('Census Population 2025 download failed')


# ── 2. Census CBP 2023 ────────────────────────────────────────────────────────

def update_census_cbp():
    section('Census County Business Patterns 2023 (released Jun 2025)')
    dest = BASE_DIR / 'census_cbp'
    dest.mkdir(parents=True, exist_ok=True)

    txt_name = 'cbp23co.txt'
    txt_path = dest / txt_name
    if _is_real_file(txt_path, min_bytes=50_000):
        print(f'  Already exists: {txt_name}')
        return

    zip_path = dest / 'cbp23co.zip'
    urls = [
        'https://www.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip',
        'https://www2.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip',
        'https://www2.census.gov/programs-surveys/cbp/data/2023/cbp23co.zip',
    ]
    for url in urls:
        if download(url, zip_path, 'Census CBP 2023', expected_mb=110):
            if extract_zip(zip_path, dest):
                # The zip may extract as cbp23co.txt directly
                if _is_real_file(txt_path, min_bytes=50_000):
                    print('  CBP 2023 ready.')
                    return
                # Or find whatever txt it extracted
                txts = list(dest.glob('cbp23*.txt'))
                if txts:
                    txts[0].rename(txt_path)
                    print(f'  Renamed {txts[0].name} → {txt_name}')
                    return
        if zip_path.exists(): zip_path.unlink()

    print('  Manual download:')
    print('  1. https://www.census.gov/data/datasets/2023/econ/cbp/2023-cbp.html')
    print('  2. Download "Complete County File" (cbp23co.zip)')
    print(f'  3. Extract cbp23co.txt to: {dest}')
    raise RuntimeError('Census CBP 2023 download failed')


# ── 3. Census BPS 2025 ────────────────────────────────────────────────────────

def update_census_bps():
    section('Census Building Permits Survey 2025 (released May 14, 2026)')
    dest = BASE_DIR / 'census_bps'
    dest.mkdir(parents=True, exist_ok=True)

    fname = 'co2025a.txt'
    p = dest / fname
    if _is_real_file(p, min_bytes=10_000):
        print(f'  Already exists: {fname}')
        return

    urls = [
        f'https://www2.census.gov/econ/bps/County/{fname}',
        f'https://www2.census.gov/programs-surveys/bps/data/2025/county/{fname}',
        f'https://www2.census.gov/programs-surveys/bps/data/2025/{fname}',
    ]
    for url in urls:
        if download(url, p, 'Census BPS 2025', expected_mb=2):
            print('  BPS 2025 ready.')
            return
        if p.exists(): p.unlink()

    print('  Manual download:')
    print('  1. https://www.census.gov/construction/bps/county.html')
    print('  2. Download 2025 Annual County file (co2025a.txt)')
    print(f'  3. Save to: {dest}')
    raise RuntimeError('Census BPS 2025 download failed')


# ── 4. BLS QCEW 2024 ──────────────────────────────────────────────────────────

def update_bls_qcew():
    section('BLS QCEW 2024 Annual (released Sep 2025)')
    dest = BASE_DIR / 'bls_qcew'
    dest.mkdir(parents=True, exist_ok=True)

    csv_name = '2024.annual.singlefile.csv'
    csv_path = dest / csv_name
    if _is_real_file(csv_path, min_bytes=100_000_000):
        print(f'  Already exists: {csv_name}')
        return

    zip_path = dest / '2024.annual.singlefile.zip'
    urls = [
        'https://data.bls.gov/cew/data/files/2024/csv/2024.annual.singlefile.zip',
        'https://www.bls.gov/cew/data/files/2024/csv/2024.annual.singlefile.zip',
    ]
    for url in urls:
        if download(url, zip_path, 'BLS QCEW 2024 annual singlefile', expected_mb=700):
            print('  Extracting QCEW 2024 (large file — may take a few minutes)...')
            if extract_zip(zip_path, dest):
                if _is_real_file(csv_path, min_bytes=100_000_000):
                    print('  QCEW 2024 ready.')
                    return
                # Find whatever CSV extracted
                csvs = list(dest.glob('2024*.csv'))
                if csvs:
                    csvs[0].rename(csv_path)
                    print(f'  Renamed {csvs[0].name} → {csv_name}')
                    return
        if zip_path.exists(): zip_path.unlink()

    print('  Manual download:')
    print('  1. https://www.bls.gov/cew/downloadable-data-files.htm')
    print('  2. 2024 → Annual Averages → CSV Single File (All Industries)')
    print(f'  3. Extract 2024.annual.singlefile.csv to: {dest}')
    raise RuntimeError('BLS QCEW 2024 download failed')


# ── 5. NOAA Storm Events 2024 ─────────────────────────────────────────────────

def update_noaa_storm():
    section('NOAA Storm Events 2024 (rolling 5-year window: 2020-2024)')
    dest = BASE_DIR / 'noaa_storm_events'
    dest.mkdir(parents=True, exist_ok=True)

    # Check if 2024 details files already present
    existing_2024 = [f for f in os.listdir(dest) if '_d2024_' in f and 'details' in f]
    if existing_2024:
        print(f'  Already have {len(existing_2024)} 2024 file(s): {existing_2024[0]}')
        return

    # Scrape the NOAA bulk CSV directory for 2024 details files
    noaa_base = 'https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/'
    print(f'  Scraping NOAA file index: {noaa_base}')
    links = scrape_links(noaa_base, r'(StormEvents_details-ftp_v1\.0_d2024_[^"]+\.csv\.gz)')

    # Known 2024 file as fallback if scrape returns nothing
    if not links:
        links = ['StormEvents_details-ftp_v1.0_d2024_c20260421.csv.gz']
        print(f'  Using known 2024 filename: {links[0]}')

    print(f'  Found {len(links)} file(s) for 2024')
    base = noaa_base
    downloaded = 0
    for fn in links:
        gz_path  = dest / fn
        csv_path = dest / fn.replace('.csv.gz', '.csv')
        if _is_real_file(csv_path, min_bytes=1_000):
            print(f'  Already exists: {csv_path.name}')
            downloaded += 1
            continue
        url = base + fn
        if download(url, gz_path, fn, expected_mb=5):
            if ungzip(gz_path, csv_path):
                gz_path.unlink()
                downloaded += 1
            else:
                gz_path.unlink()

    if downloaded == 0:
        raise RuntimeError('No NOAA 2024 files downloaded')
    print(f'  NOAA Storm Events 2024 ready ({downloaded} file(s)).')


# ── Summary ────────────────────────────────────────────────────────────────────

def write_summary():
    section('DOWNLOAD SUMMARY — v5 Dataset Updates')
    passed = [k for k, v in RESULTS.items() if v]
    failed = [k for k, v in RESULTS.items() if not v]
    print(f'\n  Passed  ({len(passed)}/{len(RESULTS)}):')
    for k in passed: print(f'    OK   {k}')
    if failed:
        print(f'\n  Failed ({len(failed)}):')
        for k in failed: print(f'    !!   {k}')
    print()
    if not failed:
        print('  All datasets updated. Next step: run scoring_engine.py')
    else:
        print('  Some datasets need manual download — see instructions above.')


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print('\n' + '='*64)
    print('  CIVICA DATA DOWNLOADER v5 — Dataset Refresh (May 2026)')
    print(f'  Output: {BASE_DIR}')
    print('='*64)

    run('census_population_2025', update_census_population)
    run('census_cbp_2023',        update_census_cbp)
    run('census_bps_2025',        update_census_bps)
    run('bls_qcew_2024',          update_bls_qcew)
    run('noaa_storm_2024',        update_noaa_storm)

    write_summary()
    print('='*64 + '\n')


if __name__ == '__main__':
    main()
