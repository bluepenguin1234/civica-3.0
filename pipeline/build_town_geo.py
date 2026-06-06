"""
build_town_geo.py — town coordinates for the interactive map.
Downloads the Census Gazetteer 'places' national file (lat/long centroid per place),
joins it to town_scores.csv on the 7-digit place FIPS, and writes a compact
output/towns_geo.json for the Leaflet map: [{f,n,s,c,sc,l,lat,lon}, ...].
"""
import os, sys, io, json, zipfile
sys.stdout.reconfigure(encoding='utf-8')
import requests
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA = os.path.join(ROOT, 'civica_data', 'gazetteer')
os.makedirs(DATA, exist_ok=True)
SCORES = os.path.join(BASE, 'town_scores.csv')
OUT = os.path.join(ROOT, 'docs', 'output', 'towns_geo.json')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Civica/2.0'}

# Census Gazetteer place files (try newest vintages first).
CANDIDATES = [
    ('2024', 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip'),
    ('2023', 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip'),
    ('2021', 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2021_Gazetteer/2021_Gaz_place_national.zip'),
]


def fetch_gaz():
    local = os.path.join(DATA, 'gaz_place.txt')
    if os.path.exists(local) and os.path.getsize(local) > 500_000:
        print("  using cached gazetteer")
        return local
    for yr, url in CANDIDATES:
        try:
            print(f"  downloading {yr} gazetteer places...")
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = [n for n in z.namelist() if n.lower().endswith('.txt')][0]
            with z.open(name) as f, open(local, 'wb') as o:
                o.write(f.read())
            print(f"    saved {os.path.getsize(local)/1e6:.1f} MB ({name})")
            return local
        except Exception as e:
            print(f"    {yr} failed: {e}")
    raise SystemExit("Could not download a Census Gazetteer place file.")


def main():
    gaz = fetch_gaz()
    # Tab-delimited; some rows have trailing spaces in column names.
    g = pd.read_csv(gaz, sep='\t', dtype={'GEOID': str}, encoding='latin1')
    g.columns = [c.strip() for c in g.columns]
    g['fips'] = g['GEOID'].str.zfill(7)
    g['lat'] = pd.to_numeric(g['INTPTLAT'], errors='coerce')
    g['lon'] = pd.to_numeric(g['INTPTLONG'], errors='coerce')
    geo = g.set_index('fips')[['lat', 'lon']]

    df = pd.read_csv(SCORES, dtype={'fips': str})
    df['fips'] = df['fips'].str.zfill(7)
    df = df.join(geo, on='fips')
    matched = df['lat'].notna().sum()
    print(f"  matched coordinates: {matched:,}/{len(df):,} ({matched/len(df)*100:.1f}%)")

    df = df.dropna(subset=['lat', 'lon'])
    records = [{
        'f': r['fips'], 'n': r['place_name'], 's': r['state_abbr'],
        'c': r['county_name'], 'sc': round(float(r['civica_score']), 1),
        'l': r['market_label'], 'lat': round(float(r['lat']), 4),
        'lon': round(float(r['lon']), 4),
    } for _, r in df.iterrows()]
    json.dump(records, open(OUT, 'w', encoding='utf-8'), separators=(',', ':'))
    print(f"  wrote {len(records):,} towns -> {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB)")


if __name__ == '__main__':
    main()
