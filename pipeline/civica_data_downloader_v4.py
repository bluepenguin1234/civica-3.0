"""
Civica Data Downloader v4
=========================
Downloads the 5 datasets required by the Harvard Research Model
that were NOT covered by v3.

New in v4:
  FEMA NRI        — National Risk Index (EAL per capita, SVI) → Physical Risk dimension
  HUD FMR         — Fair Market Rents by county → Price-to-Rent ratio + rent trend
  BEA CAINC1      — Per capita personal income by county → P/I ratio + Economic Vitality
  USDA RUCC       — Rural-Urban Continuum Codes → peer comparison for crime scoring
  Census PopEst   — County population estimates → EAL per capita denominator

Previously downloaded by v3 (already on disk, not re-downloaded here):
  IRS SOI Migration, FHFA HPI, BLS QCEW, FBI NIBRS, EIA 861, EIA NG,
  Census STC, Census CBP, Census BPS, FEMA NFIP, NOAA Storm Events,
  USFS Wildfire, NCES F-33, NCES EDFacts, Zillow

V3 data lives at:
  C:\\Users\\Brian\\Desktop\\CIVICA revised -\\civica_data\\

V4 data saves to:
  C:\\Users\\Brian\\Desktop\\Civica Harvard Model\\civica_data\\

Requirements:
  pip install requests

Run: python civica_data_downloader_v4.py
"""

import sys
import re
import zipfile
import requests
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path.home() / "Desktop" / "Civica Harvard Model" / "civica_data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}

RESULTS = {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def create_folders():
    folders = [
        "fema_nri",
        "hud_fmr",
        "bea_income",
        "usda_rucc",
        "census_population",
        "_logs",
    ]
    for f in folders:
        (BASE_DIR / f).mkdir(parents=True, exist_ok=True)
    print(f"Output folder: {BASE_DIR}\n")


def section(title):
    print(f"\n{'='*62}")
    print(f"  {title}")
    print(f"{'='*62}")


def _is_real_file(path, min_bytes=5000):
    p = Path(path)
    if not p.exists() or p.stat().st_size < min_bytes:
        return False
    with open(p, 'rb') as f:
        header = f.read(4)
    if header[:1] in (b'<', b'{'):
        return False
    return True


def download_file(url, dest_path, label, expected_mb=None, force=False):
    p = Path(dest_path)
    if not force and p.exists() and p.stat().st_size > 1000:
        if _is_real_file(p):
            print(f"  Skipping (already exists): {p.name}")
            return True
        else:
            print(f"  Removing bad file and re-downloading: {p.name}")
            p.unlink()

    hint = f" (~{expected_mb}MB)" if expected_mb else ""
    print(f"  Downloading{hint}: {label}")
    print(f"  URL: {url}")
    try:
        r = requests.get(url, stream=True, timeout=180, headers=HEADERS)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = min(done / total * 100, 100.0)
                        print(f"\r    {pct:5.1f}%  {done/1048576:.1f}MB", end="", flush=True)
        print(f"\r    Done — {p.name} ({done/1048576:.1f}MB)        ")
        return True
    except requests.HTTPError as e:
        print(f"\r    HTTP {e.response.status_code} — {url}")
        return False
    except Exception as e:
        print(f"\r    ERROR: {e}")
        return False


def extract_zip(zip_path, dest_dir):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(dest_dir)
        print(f"    Extracted to {Path(dest_dir).name}/")
    except Exception as e:
        print(f"    Extract error: {e}")


def scrape_links(url, pattern):
    try:
        r = requests.get(url, timeout=30, headers=HEADERS)
        r.raise_for_status()
        return re.findall(pattern, r.text)
    except Exception as e:
        print(f"  Could not fetch {url}: {e}")
        return []


def run(key, fn):
    try:
        fn()
        RESULTS[key] = True
    except Exception as e:
        print(f"\n  FAILED ({key}): {e}")
        RESULTS[key] = False


# ─────────────────────────────────────────────
# 1. FEMA NATIONAL RISK INDEX
#    → Physical Risk dimension: EAL per capita, SVI, climate trajectory
#    → Single zip, direct download, ~150MB
# ─────────────────────────────────────────────

def download_fema_nri():
    section("FEMA NRI — National Risk Index (EAL, SVI, Risk Score by County)")
    dest = BASE_DIR / "fema_nri"

    # Check if already extracted
    existing_csv = list(dest.glob("NRI_Table_Counties*.csv"))
    if existing_csv:
        print(f"  Already exists: {existing_csv[0].name}")
        return

    zip_path = dest / "NRI_Table_Counties.zip"

    # Primary: FEMA direct download
    primary = "https://hazards.fema.gov/nri/Content/StaticDocuments/DataDownload//NRI_Table_Counties/NRI_Table_Counties.zip"
    # Fallback: ArcGIS hub
    fallback = "https://opendata.arcgis.com/datasets/efae40b87a6747dd88e03b11b9498fba_0.zip"

    for url in [primary, fallback]:
        if download_file(url, zip_path, "FEMA NRI County Table", expected_mb=150):
            if _is_real_file(zip_path, min_bytes=100000):
                extract_zip(zip_path, dest)
                return
            else:
                zip_path.unlink()

    print("  FEMA NRI manual download:")
    print("  1. Go to: https://hazards.fema.gov/nri/map")
    print("  2. Click 'Download Data' → 'Download NRI Data' → Counties")
    print(f"  3. Save zip to: {dest}")


# ─────────────────────────────────────────────
# 2. HUD FAIR MARKET RENTS
#    → Price-to-Rent ratio: FHFA HPI ÷ (FMR × 12)
#    → Rent trend metric in Housing Market Dynamics
#    → County-level estimates, annual Excel file
# ─────────────────────────────────────────────

def download_hud_fmr():
    section("HUD Fair Market Rents — County-Level Annual Estimates")
    dest = BASE_DIR / "hud_fmr"

    # Try FY2025 first, then FY2024, FY2023 as fallbacks
    # HUD publishes these as Excel files; county estimates are in the main FMR file
    attempts = [
        ("FY2025_FMRs.xlsx",          "https://www.huduser.gov/portal/datasets/fmr/fmr2025/FY2025_FMRs.xlsx"),
        ("FY2025_4050_FMRs.xlsx",     "https://www.huduser.gov/portal/datasets/fmr/fmr2025/FY2025_4050_FMRs.xlsx"),
        ("FY2024_FMRs_revised.xlsx",  "https://www.huduser.gov/portal/datasets/fmr/fmr2024/FY2024_FMRs_revised.xlsx"),
        ("FY2024_FMRs.xlsx",          "https://www.huduser.gov/portal/datasets/fmr/fmr2024/FY2024_FMRs.xlsx"),
        ("FY2024_4050_FMRs.xlsx",     "https://www.huduser.gov/portal/datasets/fmr/fmr2024/FY2024_4050_FMRs.xlsx"),
        ("FY2023_FMRs.xlsx",          "https://www.huduser.gov/portal/datasets/fmr/fmr2023/FY2023_FMRs.xlsx"),
    ]

    for fname, url in attempts:
        p = dest / fname
        if p.exists() and _is_real_file(p, min_bytes=50000):
            print(f"  Already exists: {fname}")
            return
        if download_file(url, p, f"HUD FMR {fname}", expected_mb=5):
            if _is_real_file(p, min_bytes=50000):
                print(f"  HUD FMR saved: {fname}")
                return
            else:
                p.unlink()

    # Scrape HUD page as last resort
    print("  Trying to scrape HUD FMR page...")
    links = scrape_links(
        "https://www.huduser.gov/portal/datasets/fmr.html",
        r'href="([^"]*FMR[^"]*\.(?:xlsx|xls|zip))"',
    )
    base = "https://www.huduser.gov"
    for href in links[:6]:
        url = href if href.startswith("http") else base + href
        fn = url.split("/")[-1].split("?")[0]
        p = dest / fn
        if download_file(url, p, f"HUD FMR: {fn}", expected_mb=5):
            if _is_real_file(p, min_bytes=50000):
                return
            p.unlink()

    print("  HUD FMR manual download:")
    print("  1. Go to: https://www.huduser.gov/portal/datasets/fmr.html")
    print("  2. Click the most recent FMR data file (Excel)")
    print(f"  3. Save to: {dest}")


# ─────────────────────────────────────────────
# 3. BEA LOCAL AREA PERSONAL INCOME (CAINC1)
#    → Price-to-Income ratio: FHFA HPI ÷ BEA per capita income
#    → Real wage growth baseline
#    → Economic Vitality dimension
#    → Direct zip download, no API key needed, ~50MB
# ─────────────────────────────────────────────

def download_bea_income():
    section("BEA CAINC1 — Per Capita Personal Income by County")
    dest = BASE_DIR / "bea_income"

    # Check for already-extracted file
    existing = list(dest.glob("CAINC1*.csv")) + list(dest.glob("*CAINC1*"))
    if existing:
        print(f"  Already exists: {existing[0].name}")
        return

    zip_path = dest / "CAINC1.zip"

    # BEA publishes direct zip downloads for all regional tables — no API key needed
    urls = [
        "https://apps.bea.gov/regional/zip/CAINC1.zip",
        "https://apps.bea.gov/regional/zip/CAINC4.zip",   # Supplement: income + employment
    ]

    for url in urls:
        fname = url.split("/")[-1]
        p = dest / fname
        if download_file(url, p, f"BEA {fname} (Per Capita Income)", expected_mb=50):
            if _is_real_file(p, min_bytes=10000):
                extract_zip(p, dest)
                # Also grab CAGDP2 (GDP by county) as a bonus for Economic Vitality
                if fname == "CAINC1.zip":
                    gdp_zip = dest / "CAGDP2.zip"
                    download_file(
                        "https://apps.bea.gov/regional/zip/CAGDP2.zip",
                        gdp_zip,
                        "BEA CAGDP2 (GDP by County)",
                        expected_mb=20,
                    )
                    if _is_real_file(gdp_zip, min_bytes=10000):
                        extract_zip(gdp_zip, dest)
                return
            else:
                p.unlink()

    print("  BEA manual download:")
    print("  1. Go to: https://apps.bea.gov/regional/downloadzip.htm")
    print("  2. Select 'CAINC1 — Per Capita Personal Income'")
    print(f"  3. Save zip to: {dest}")


# ─────────────────────────────────────────────
# 4. USDA RURAL-URBAN CONTINUUM CODES (RUCC)
#    → Quality of Place: compare crime rates within rural/urban peer tier
#    → Small Excel file, direct download
# ─────────────────────────────────────────────

def download_usda_rucc():
    section("USDA RUCC — Rural-Urban Continuum Codes by County")
    dest = BASE_DIR / "usda_rucc"

    # Try 2023 first, then 2013 as fallback
    attempts = [
        ("ruralurbancodes2023.xlsx", "https://ers.usda.gov/webdocs/DataFiles/53251/ruralurbancodes2023.xlsx"),
        ("ruralurbancodes2023.xls",  "https://ers.usda.gov/webdocs/DataFiles/53251/ruralurbancodes2023.xls"),
        ("ruralurbancodes2013.xls",  "https://ers.usda.gov/webdocs/DataFiles/53251/ruralurbancodes2013.xls"),
        ("ruralurbancodes2013.xlsx", "https://ers.usda.gov/webdocs/DataFiles/53251/ruralurbancodes2013.xlsx"),
    ]

    for fname, url in attempts:
        p = dest / fname
        if p.exists() and _is_real_file(p, min_bytes=10000):
            print(f"  Already exists: {fname}")
            return
        if download_file(url, p, f"USDA RUCC {fname}", expected_mb=1):
            if _is_real_file(p, min_bytes=10000):
                print(f"  USDA RUCC saved: {fname}")
                return
            else:
                p.unlink()

    # Scrape the USDA ERS page
    print("  Trying to scrape USDA ERS page...")
    links = scrape_links(
        "https://ers.usda.gov/data-products/rural-urban-continuum-codes/",
        r'href="([^"]*ruralurban[^"]*\.(?:xlsx|xls|csv|zip))"',
    )
    base = "https://ers.usda.gov"
    for href in links[:5]:
        url = href if href.startswith("http") else base + href
        fn = url.split("/")[-1].split("?")[0]
        p = dest / fn
        if download_file(url, p, f"USDA RUCC: {fn}", expected_mb=1):
            if _is_real_file(p, min_bytes=5000):
                return
            p.unlink()

    print("  USDA RUCC manual download:")
    print("  1. Go to: https://ers.usda.gov/data-products/rural-urban-continuum-codes/")
    print("  2. Download the county-level Excel file")
    print(f"  3. Save to: {dest}")


# ─────────────────────────────────────────────
# 5. CENSUS POPULATION ESTIMATES
#    → EAL per capita denominator (FEMA EAL ÷ population)
#    → Population Momentum dimension
#    → Direct CSV download, ~5MB
# ─────────────────────────────────────────────

def download_census_population():
    section("Census Population Estimates — County Totals 2020-2023")
    dest = BASE_DIR / "census_population"

    fname = "co-est2023-alldata.csv"
    p = dest / fname

    urls = [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/counties/totals/{fname}",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-2022/counties/totals/co-est2022-alldata.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv",
    ]

    for url in urls:
        fn = url.split("/")[-1]
        p = dest / fn
        if p.exists() and _is_real_file(p, min_bytes=1000):
            print(f"  Already exists: {fn}")
            return
        if download_file(url, p, f"Census PopEst: {fn}", expected_mb=5):
            if _is_real_file(p, min_bytes=1000):
                return
            p.unlink()

    print("  Census PopEst manual download:")
    print("  1. Go to: https://www.census.gov/programs-surveys/popest/data/tables.html")
    print("  2. Select 'County' → most recent year → download CSV")
    print(f"  3. Save to: {dest}")


# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────

def write_summary():
    section("DOWNLOAD SUMMARY — v4 Harvard Model Datasets")

    passed = [k for k, v in RESULTS.items() if v]
    failed = [k for k, v in RESULTS.items() if not v]

    print(f"\n  Passed  ({len(passed)}/{len(RESULTS)}):")
    for k in passed:
        print(f"    OK   {k}")
    if failed:
        print(f"\n  Still needed ({len(failed)}):")
        for k in failed:
            print(f"    !!   {k}")

    print(f"""
  Harvard Model Data Status
  ─────────────────────────────────────────────────────
  V3 data (already on disk):
    C:\\Users\\Brian\\Desktop\\CIVICA revised -\\civica_data\\
    Includes: IRS Migration, FHFA HPI, BLS QCEW, FBI NIBRS,
              EIA 861, EIA NG, Census STC, CBP, BPS,
              FEMA NFIP, NOAA Storm Events, USFS Wildfire

  V4 data (this run):
    {BASE_DIR}
    Includes: FEMA NRI, HUD FMR, BEA Income, USDA RUCC,
              Census Population Estimates
  ─────────────────────────────────────────────────────
  Next step: build scoring_engine.py to normalize all
  datasets into 0-100 scores for all 3,143 counties.
""")

    log_path = BASE_DIR / "_logs" / f"download_log_v4_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Civica Data Downloader v4\nRun: {datetime.now()}\n\n")
        for k, v in RESULTS.items():
            f.write(f"{'OK  ' if v else 'FAIL'} {k}\n")
    print(f"  Log: {log_path}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("\n" + "="*62)
    print("  CIVICA DATA DOWNLOADER v4 — Harvard Research Model")
    print(f"  Output: {BASE_DIR}")
    print("="*62)

    create_folders()

    run("fema_nri",           download_fema_nri)
    run("hud_fmr",            download_hud_fmr)
    run("bea_income",         download_bea_income)
    run("usda_rucc",          download_usda_rucc)
    run("census_population",  download_census_population)

    write_summary()
    print("\n" + "="*62 + "\n")


if __name__ == "__main__":
    main()
