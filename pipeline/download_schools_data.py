"""
download_schools_data.py — federal ADMINISTRATIVE school data for the Schools dimension.
No survey data. Two pieces:

  1. NCES EDGE public-school geocodes (nces.ed.gov)         -> civica_data/nces/school_geocodes.csv
       school NCESSCH + name + ZIP + county + lat/lon + place
  2. EDFacts state-test proficiency, all tested students    -> civica_data/nces/edfacts_assessments.csv
       (US Dept. of Education administrative data — NOT a survey, NOT NAEP)

NOTE ON SOURCE: ED.gov removed the direct EDFacts downloads in the 2025 reorg, so the
proficiency file is pulled from the Urban Institute Education Data Portal, which
redistributes the *unaltered* federal EDFacts file. Document it as: "US Dept. of Education
EDFacts state assessments (administrative), accessed via the Urban Institute mirror."

Run (locally):  python download_schools_data.py
"""
import os, sys, io, csv, time, zipfile
sys.stdout.reconfigure(encoding='utf-8')
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'civica_data', 'nces')
os.makedirs(DATA, exist_ok=True)
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Civica/2.0'}
URBAN = 'https://educationdata.urban.org/api/v1/schools/edfacts/assessments'
EDGE = 'https://nces.ed.gov/programs/edge/data'


def section(t): print(f"\n{'='*64}\n  {t}\n{'='*64}")


def _real(p, mn=20_000):
    return os.path.exists(p) and os.path.getsize(p) >= mn


def get_geocodes():
    section("1/2  NCES EDGE — public school locations")
    dest = os.path.join(DATA, 'school_geocodes.csv')
    if _real(dest):
        print(f"  already present: {os.path.basename(dest)}"); return True
    for url in [f'{EDGE}/EDGE_GEOCODE_PUBLICSCH_2223.zip',
                f'{EDGE}/EDGE_GEOCODE_PUBLICSCH_2122.zip',
                f'{EDGE}/EDGE_GEOCODE_PUBLICSCH_2021.zip']:
        try:
            print("  trying", url)
            r = requests.get(url, headers=H, timeout=300); r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = next(n for n in z.namelist() if n.lower().endswith(('.csv', '.txt')))
            with z.open(name) as s, open(dest, 'wb') as o:
                o.write(s.read())
            print(f"    saved ({os.path.getsize(dest)/1e6:.1f} MB)"); return True
        except Exception as e:
            print("    failed:", e)
    print("  !! EDGE geocodes failed — see https://nces.ed.gov/programs/edge/Geographic/SchoolLocations")
    return False


def _count(y):
    for _ in range(3):
        try:
            r = requests.get(f'{URBAN}/{y}/grade-99/', timeout=60, headers=H)
            if r.headers.get('content-type', '').startswith('application/json'):
                return r.json().get('count', 0)
        except Exception:
            pass
        time.sleep(2)
    return -1


def get_proficiency():
    section("2/2  EDFacts state-test proficiency (via Urban Institute mirror)")
    dest = os.path.join(DATA, 'edfacts_assessments.csv')
    if _real(dest):
        print(f"  already present: {os.path.basename(dest)}"); return True
    year = next((y for y in [2022, 2021, 2019, 2018, 2020] if _count(y) > 50000), None)
    if not year:
        print("  !! no EDFacts year available from the portal"); return False
    print(f"  using EDFacts year {year} (grade-99 = all grades)")
    url, rows, page = f'{URBAN}/{year}/grade-99/', [], 0
    while url:
        j = None
        for _ in range(4):
            try:
                j = requests.get(url, timeout=90, headers=H).json(); break
            except Exception:
                time.sleep(3)
        if not j:
            print("  !! fetch failed mid-page"); return False
        for r in j['results']:
            rows.append((str(r['ncessch']).zfill(12), r['fips'],
                         r.get('math_test_pct_prof_midpt'), r.get('math_test_num_valid'),
                         r.get('read_test_pct_prof_midpt'), r.get('read_test_num_valid')))
        page += 1; url = j.get('next')
        if page % 3 == 0:
            print(f"    ...{len(rows)} rows")
    with open(dest, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['ncessch', 'fips', 'math_prof', 'math_n', 'read_prof', 'read_n'])
        w.writerows(rows)
    print(f"  saved {len(rows)} schools (year {year}, {os.path.getsize(dest)/1e6:.1f} MB)")
    return True


def main():
    print("Civica Schools downloader\nTarget:", DATA)
    a, b = get_geocodes(), get_proficiency()
    section("Summary")
    print(f"  [{'OK ' if a else 'XX '}] school geocodes")
    print(f"  [{'OK ' if b else 'XX '}] EDFacts proficiency")
    if not (a and b):
        sys.exit(1)
    print("\nBoth present. Next: build the Schools loader + town join.")


if __name__ == '__main__':
    main()
