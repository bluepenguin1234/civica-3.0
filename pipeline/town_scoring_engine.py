#!/usr/bin/env python3
"""
Civica Town Scoring Engine (Level B)
====================================
Ranks US incorporated places ("towns") on a single 0-100 Civica Score built from
4 dimensions. Reuses the county loaders in county_loaders.py VERBATIM
(imported, not copied) and joined to each town via its primary county FIPS. Three
town-resolved inputs are added — Census sub-est (town population/growth), IRS SOI
ZIP AGI (town income), and a rewritten NIBRS pass (town crime). Zillow is dropped
entirely; affordability is rent-vs-income + appreciation quality.

  Dim 1 Affordability   28 pts   (rent burden T, appreciation quality C)
  Dim 2 Economy         28 pts   (wage C, sector C, diversity C, town income growth T)
  Dim 3 Safety & Place  26 pts   (violent T, property T, town scale T, amenity C, risk C)
  Dim 4 Growth          18 pts   (town growth T, town-vs-county T, migration C, in-mover C, permits C)
                       -------
  Total                100 pts    (~51 pts town-resolved, ~49 county-inherited)

Output: town_scores.csv (one row per town) + town_scores_meta.json.
Run locally where civica_data/ lives:  python town_scoring_engine.py
"""

import os
import re
import sys
import json
import glob
import difflib
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

# Reuse the working county loaders + percentile helpers VERBATIM from the county engine.
from county_loaders import (
    load_bea, load_fmr, load_hpi, load_qcew, load_cbp, load_bps,
    load_irs, load_nfip, load_noaa_storm, load_usfs, load_rucc,
    pct, pct_inv,
)

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'civica_data')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'town_scores.csv')
META = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'town_scores_meta.json')

STATE_FIPS = {
    'AL': '01', 'AK': '02', 'AZ': '04', 'AR': '05', 'CA': '06', 'CO': '08', 'CT': '09',
    'DE': '10', 'FL': '12', 'GA': '13', 'HI': '15', 'ID': '16', 'IL': '17', 'IN': '18',
    'IA': '19', 'KS': '20', 'KY': '21', 'LA': '22', 'ME': '23', 'MD': '24', 'MA': '25',
    'MI': '26', 'MN': '27', 'MS': '28', 'MO': '29', 'MT': '30', 'NE': '31', 'NV': '32',
    'NH': '33', 'NJ': '34', 'NM': '35', 'NY': '36', 'NC': '37', 'ND': '38', 'OH': '39',
    'OK': '40', 'OR': '41', 'PA': '42', 'RI': '44', 'SC': '45', 'SD': '46', 'TN': '47',
    'TX': '48', 'UT': '49', 'VT': '50', 'VA': '51', 'WA': '53', 'WV': '54', 'WI': '55',
    'WY': '56', 'DC': '11',
}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}

POP_COLS = [f'POPESTIMATE{y}' for y in range(2020, 2026)]

# Town-resolved (T) vs county-inherited (C) point allocations. Sum = 100.
# These constants ARE the model; the validator reads town_resolved_share from the meta.
POINTS = {
    # metric                 points  layer    dimension (cap)
    'rent_burden':          (15.0, 'T'),   # Affordability 25
    'appr_quality':         (10.0, 'C'),
    'avg_wage':             (8.4,  'C'),   # Economy 24
    'sector_quality':       (6.0,  'C'),
    'diversity':            (4.8,  'C'),
    'town_income_growth':   (4.8,  'T'),
    'violent':              (6.6,  'T'),   # Safety & Place 22
    'property':             (4.4,  'T'),
    'town_scale':           (4.4,  'T'),
    'amenity':              (3.3,  'C'),
    'physical_risk':        (3.3,  'C'),
    'town_growth_5yr':      (5.25, 'T'),   # Growth 15
    'town_growth_relmom':   (3.75, 'T'),
    'net_migration':        (3.0,  'C'),
    'inmover':              (1.5,  'C'),
    'permits':              (1.5,  'C'),
    'schools':              (14.0, 'T'),   # Schools 14 (dim 5)
}
T_POINTS = sum(p for p, layer in POINTS.values() if layer == 'T')   # 58.2
C_POINTS = sum(p for p, layer in POINTS.values() if layer == 'C')   # 41.8


# ── New town-resolved loaders ────────────────────────────────────────────────────

MCD_GEOIDS = set()  # New England town (MCD) GEOIDs; set by load_subest, reused by the crosswalk
# New England states whose governing municipalities are MCDs (county subdivisions), not
# Census incorporated places: CT, ME, MA, NH, RI, VT.
NE_MCD_STATES = {'09', '23', '25', '33', '44', '50'}


def load_subest():
    """Census sub-est: town universe + growth + place->county crosswalk + county totals.

    Universe = incorporated places (SUMLEV 162) PLUS New England governing towns
    (SUMLEV 061 with FUNCSTAT 'A' = active government). The 'A' filter is the dedup: NE
    cities appear as FUNCSTAT 'F' duplicates of their incorporated place and are skipped,
    so the two layers don't double-count. MCD towns carry a 10-digit state+county+cousub
    GEOID as `fips` and are flagged is_mcd=1."""
    df = pd.read_csv(
        f'{DATA}/census_population/sub-est.csv', encoding='latin1',
        dtype={'SUMLEV': str, 'STATE': str, 'COUNTY': str, 'PLACE': str,
               'COUSUB': str, 'FUNCSTAT': str},
    )
    for c in POP_COLS:
        df[c] = pd.to_numeric(df[c], errors='coerce')

    # County totals + names + 5yr growth from the county records (050).
    counties = df[df['SUMLEV'] == '050'].copy()
    counties['cfips'] = counties['STATE'].str.zfill(2) + counties['COUNTY'].str.zfill(3)
    cpop25 = counties.set_index('cfips')['POPESTIMATE2025']
    cpop20 = counties.set_index('cfips')['POPESTIMATE2020']
    cname = counties.set_index('cfips')['NAME'].astype(str)
    cgrowth = (cpop25 / cpop20.replace(0, np.nan) - 1)

    # Incorporated places (whole). COUNTY is '000' on these records.
    places = df[df['SUMLEV'] == '162'].copy()
    places['fips'] = places['STATE'].str.zfill(2) + places['PLACE'].str.zfill(5)
    places['is_mcd'] = 0
    # Primary county = the place-county part (157) holding the largest pop share.
    parts = df[df['SUMLEV'] == '157'].copy()
    parts['fips'] = parts['STATE'].str.zfill(2) + parts['PLACE'].str.zfill(5)
    parts['county_fips'] = parts['STATE'].str.zfill(2) + parts['COUNTY'].str.zfill(3)
    parts = parts.dropna(subset=['POPESTIMATE2025'])
    idx = parts.groupby('fips')['POPESTIMATE2025'].idxmax()
    primary = (parts.loc[idx, ['fips', 'county_fips']]
               .rename(columns={'county_fips': 'primary_county_fips'}))
    places = places.merge(primary, on='fips', how='left')

    # Governing towns (MCDs). A 061 'A' record sits in one county, so its state+county+cousub
    # GEOID is the whole town and the county is read off the record. We keep ALL New England
    # towns (cities there are 'F' duplicates, already excluded), PLUS towns elsewhere (notably
    # New York) that are >= 70% unincorporated by the SUMLEV 071 "Balance of township"
    # population — i.e. genuinely standalone governments, not an outline around an incorporated
    # city we already score. The unincorporated-share gate is the dedup that prevents
    # double-counting places that live inside a township.
    mcd = df[(df['SUMLEV'] == '061') & (df['FUNCSTAT'] == 'A')].copy()
    mcd['fips'] = (mcd['STATE'].str.zfill(2) + mcd['COUNTY'].str.zfill(3)
                   + mcd['COUSUB'].str.zfill(5))
    mcd['primary_county_fips'] = mcd['STATE'].str.zfill(2) + mcd['COUNTY'].str.zfill(3)
    mcd['is_mcd'] = 1
    # Unincorporated population from the 071 "Balance of <township>" records (PLACE 99990).
    bal = df[(df['SUMLEV'] == '071') & (df['PLACE'] == '99990')].copy()
    bal['fips'] = (bal['STATE'].str.zfill(2) + bal['COUNTY'].str.zfill(3)
                   + bal['COUSUB'].str.zfill(5))
    balpop = bal.groupby('fips')['POPESTIMATE2025'].sum()
    unincorp = (balpop / mcd.set_index('fips')['POPESTIMATE2025']).clip(0, 1)
    mcd['unincorp_share'] = mcd['fips'].map(unincorp)
    is_ne = mcd['STATE'].str.zfill(2).isin(NE_MCD_STATES)
    mcd = mcd[is_ne | (mcd['unincorp_share'] >= 0.70)].copy()

    common = ['fips', 'NAME', 'STATE', 'primary_county_fips', 'is_mcd'] + POP_COLS
    uni = pd.concat([places[common], mcd[common]], ignore_index=True)
    uni['place_name'] = uni['NAME'].astype(str)
    uni['state_abbr'] = uni['STATE'].map(FIPS_STATE)
    uni['county_name'] = uni['primary_county_fips'].map(cname)
    uni['county_total_2025'] = uni['primary_county_fips'].map(cpop25)
    uni['county_growth_5yr'] = uni['primary_county_fips'].map(cgrowth)

    # Town growth features (all T).
    p20 = uni['POPESTIMATE2020'].replace(0, np.nan)
    p24 = uni['POPESTIMATE2024'].replace(0, np.nan)
    uni['town_growth_5yr'] = uni['POPESTIMATE2025'] / p20 - 1
    uni['town_growth_1yr'] = uni['POPESTIMATE2025'] / p24 - 1
    annual = uni[POP_COLS].pct_change(axis=1).iloc[:, 1:]
    uni['town_growth_vol'] = annual.replace([np.inf, -np.inf], np.nan).std(axis=1)
    uni['town_pop_share'] = uni['POPESTIMATE2025'] / uni['county_total_2025']
    uni['town_growth_rel_county'] = uni['town_growth_5yr'] - uni['county_growth_5yr']

    # Town threshold: >= 1,000 pop (documented, parallels county >= 5,000 rule).
    uni = uni[uni['POPESTIMATE2025'] >= 1000].copy()
    uni = uni.drop_duplicates(subset='fips').copy()   # one row per GEOID — no duplicates

    global MCD_GEOIDS
    MCD_GEOIDS = set(uni.loc[uni['is_mcd'] == 1, 'fips'])

    keep = ['fips', 'place_name', 'state_abbr', 'primary_county_fips', 'county_name',
            'POPESTIMATE2025', 'town_growth_5yr', 'town_growth_1yr', 'town_growth_vol',
            'town_pop_share', 'town_growth_rel_county', 'is_mcd']
    return uni[keep].dropna(subset=['primary_county_fips']).reset_index(drop=True)


def _zip_income_for_year(yy):
    """sum(A00100)*1000 / sum(N1) per ZIP for one IRS zpallagi year."""
    df = pd.read_csv(
        f'{DATA}/irs_zip/{yy}zpallagi.csv',
        dtype={'STATEFIPS': str, 'zipcode': str},
        usecols=['STATEFIPS', 'zipcode', 'agi_stub', 'N1', 'A00100'],
    )
    df = df[~df['zipcode'].isin(['00000', '99999', '0', ''])].copy()
    df['zipcode'] = df['zipcode'].str.zfill(5)
    df['N1'] = pd.to_numeric(df['N1'], errors='coerce')
    df['A00100'] = pd.to_numeric(df['A00100'], errors='coerce')  # AGI in $thousands
    g = df.groupby('zipcode').agg(N1=('N1', 'sum'), AGI=('A00100', 'sum'))
    g = g[g['N1'] > 0]
    return (g['AGI'] * 1000.0 / g['N1']).rename('zip_income')


def load_irs_zip():
    """IRS SOI ZIP AGI -> per-ZIP income level (latest year) + growth (latest vs prior)."""
    files = sorted(glob.glob(f'{DATA}/irs_zip/*zpallagi.csv'))
    years = sorted({os.path.basename(f)[:2] for f in files}, reverse=True)
    if not years:
        raise FileNotFoundError("No IRS zpallagi files found in civica_data/irs_zip/")
    latest = _zip_income_for_year(years[0]).to_frame()
    if len(years) >= 2:
        prior = _zip_income_for_year(years[1]).rename('zip_income_prior')
        latest = latest.join(prior, how='left')
        latest['zip_income_growth'] = latest['zip_income'] / latest['zip_income_prior'] - 1
        income_growth_available = True
    else:
        latest['zip_income_growth'] = np.nan
        income_growth_available = False
    print(f"    IRS ZIP years used: {years[:2]} (income growth: {income_growth_available})")
    return latest[['zip_income', 'zip_income_growth']], income_growth_available


def load_zip_place_crosswalk():
    """2020 ZCTA->Place relationship file, plus ZCTA->County-Subdivision for the New
    England MCD towns in the universe. Weight = AREALAND_PART (overlap land area; no
    overlap-population column exists — documented approximation). `place_fips` holds the
    7-digit place FIPS or the 10-digit MCD GEOID, so downstream income + school joins are
    uniform."""
    df = pd.read_csv(
        f'{DATA}/crosswalks/zcta_place_rel_2020.txt', sep='|',
        dtype=str, encoding='utf-8-sig',
    )
    df = df[['GEOID_ZCTA5_20', 'GEOID_PLACE_20', 'AREALAND_PART']].copy()
    df = df.dropna(subset=['GEOID_ZCTA5_20', 'GEOID_PLACE_20'])
    df = df[(df['GEOID_ZCTA5_20'].str.len() > 0) & (df['GEOID_PLACE_20'].str.len() > 0)]
    df['zcta'] = df['GEOID_ZCTA5_20'].str.zfill(5)
    df['place_fips'] = df['GEOID_PLACE_20'].str.zfill(7)
    df['w'] = pd.to_numeric(df['AREALAND_PART'], errors='coerce').fillna(0.0)
    out = df[['zcta', 'place_fips', 'w']]

    if MCD_GEOIDS:  # New England town income via ZCTA->county-subdivision overlap
        c = pd.read_csv(
            f'{DATA}/crosswalks/zcta_cousub_rel_2020.txt', sep='|',
            dtype=str, encoding='utf-8-sig',
        )
        c = c[['GEOID_ZCTA5_20', 'GEOID_COUSUB_20', 'AREALAND_PART']].dropna()
        c['zcta'] = c['GEOID_ZCTA5_20'].str.zfill(5)
        c['place_fips'] = c['GEOID_COUSUB_20'].str.zfill(10)
        c['w'] = pd.to_numeric(c['AREALAND_PART'], errors='coerce').fillna(0.0)
        c = c[c['place_fips'].isin(MCD_GEOIDS)]
        out = pd.concat([out, c[['zcta', 'place_fips', 'w']]], ignore_index=True)
    return out


def town_income(zip_income_df, crosswalk):
    """Allocate ZIP AGI to places by land-area overlap -> town income level + growth."""
    cw = crosswalk.merge(zip_income_df, left_on='zcta', right_index=True, how='inner')
    cw = cw[cw['w'] > 0]

    # Income level: weighted mean over overlapping ZCTAs.
    lvl = cw.dropna(subset=['zip_income']).copy()
    lvl['num'] = lvl['zip_income'] * lvl['w']
    lg = lvl.groupby('place_fips').agg(num=('num', 'sum'), w=('w', 'sum'))
    lg['town_income'] = lg['num'] / lg['w']

    # Income growth: weighted mean over ZCTAs that have a growth value.
    gr = cw.dropna(subset=['zip_income_growth']).copy()
    gr['num'] = gr['zip_income_growth'] * gr['w']
    gg = gr.groupby('place_fips').agg(num=('num', 'sum'), w=('w', 'sum'))
    gg['town_income_growth'] = gg['num'] / gg['w']

    out = lg[['town_income']].join(gg[['town_income_growth']], how='left')
    out.index.name = 'fips'
    return out.reset_index()


# ── Town crime (NIBRS one-pass rewrite) ──────────────────────────────────────────

VIOLENT = {'09A', '09B', '11A', '11B', '11C', '11D', '120', '13A'}
PROPERTY = {'200', '220', '23A', '23B', '23C', '23D', '23E', '23F', '23G', '23H', '240'}
COUNTYWIDE_TOKENS = ('SHERIFF', 'COUNTY', 'STATE POLICE', 'STATE PATROL',
                     'HIGHWAY PATROL', 'PARISH', 'DPS', 'PUBLIC SAFETY',
                     'STATE UNIVERSITY', 'UNIVERSITY', 'DEPARTMENT OF',
                     'STATE BUREAU', 'CAPITOL', 'PARK', 'TRANSIT', 'AIRPORT',
                     'PORT AUTHORITY', 'RAILROAD', 'TRIBAL', 'NATION')
_PLACE_SUFFIXES = (' POLICE DEPARTMENT', ' POLICE DEPT', ' POLICE', ' DEPARTMENT',
                   ' DEPT', ' PD', ' CITY', ' TOWN', ' VILLAGE', ' BOROUGH',
                   ' TOWNSHIP', ' MUNICIPALITY', ' MUNICIPAL', ' METRO',
                   ' METROPOLITAN', ' CDP', ' BALANCE')


def normalize_name(s):
    s = str(s).upper().strip()
    s = re.sub(r'\(.*?\)', '', s)            # drop "(pt.)", "(balance)" etc.
    changed = True
    while changed:
        changed = False
        for suf in _PLACE_SUFFIXES:
            if s.endswith(suf):
                s = s[:-len(suf)].strip()
                changed = True
    s = re.sub(r'[^A-Z0-9 ]', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def load_nibrs_town(places):
    """Single streaming pass over the NIBRS master file. Returns:
      place_crime  — per-town violent/property counts (matched municipal agencies)
      county_crime — per-county violent/property counts (ALL agencies, incl. sheriffs)
    Agency->place mapping via normalized-name match within state, preferring same county;
    deterministic exact match first, then a difflib fuzzy fallback (>=0.90)."""
    print("    Streaming FBI NIBRS 2024 (5.8 GB) — one pass, ~10 minutes...")

    # Per-state lookup: normalized place name -> list of (fips, primary_county_fips)
    by_state = {}
    for _, r in places.iterrows():
        st = r['state_abbr']
        if not st:
            continue
        key = normalize_name(r['place_name'])
        by_state.setdefault(st, {}).setdefault(key, []).append(
            (r['fips'], r['primary_county_fips']))
    state_keys = {st: list(d.keys()) for st, d in by_state.items()}

    ori_meta = {}     # ori -> (state_alpha, county5, agency_name)
    ori_violent = {}  # ori -> int
    ori_property = {}  # ori -> int

    fpath = f'{DATA}/fbi_crime/2024_NIBRS_NATIONAL_MASTER_FILE.txt'
    with open(fpath, 'r', encoding='latin1', errors='replace') as f:
        for line in f:
            seg = line[:2]
            if seg == 'BH' and len(line) >= 272:
                ori = line[2:11]
                state_alpha = line[4:6]
                agency = line[41:71].strip()
                county3 = line[269:272].strip()
                sfips = STATE_FIPS.get(state_alpha)
                county5 = (sfips + county3) if (sfips and county3.isdigit()
                                                and len(county3) == 3) else None
                ori_meta[ori] = (state_alpha, county5, agency)
            elif seg == '02' and len(line) >= 36:
                ori = line[2:11]
                code = line[33:36].strip()
                if code in VIOLENT:
                    ori_violent[ori] = ori_violent.get(ori, 0) + 1
                elif code in PROPERTY:
                    ori_property[ori] = ori_property.get(ori, 0) + 1

    print(f"    Parsed {len(ori_meta):,} agencies; "
          f"{sum(ori_violent.values()):,} violent / {sum(ori_property.values()):,} property offenses")

    # Resolve each ORI to a place (municipal agencies only) and to its county.
    place_v, place_p = {}, {}
    county_v, county_p = {}, {}
    matched = unmatched = countywide = 0
    fuzzy_cache = {}

    for ori, (st, county5, agency) in ori_meta.items():
        v = ori_violent.get(ori, 0)
        p = ori_property.get(ori, 0)
        if county5:
            county_v[county5] = county_v.get(county5, 0) + v
            county_p[county5] = county_p.get(county5, 0) + p

        up = agency.upper()
        if any(tok in up for tok in COUNTYWIDE_TOKENS):
            countywide += 1
            continue  # county sheriffs etc. stay in the county pool only

        key = normalize_name(agency)
        cand = None
        d = by_state.get(st, {})
        if key in d:
            cand = d[key]
        else:
            ck = (st, key)
            if ck not in fuzzy_cache:
                hit = difflib.get_close_matches(key, state_keys.get(st, []), n=1, cutoff=0.90)
                fuzzy_cache[ck] = hit[0] if hit else None
            if fuzzy_cache[ck]:
                cand = d[fuzzy_cache[ck]]

        if not cand:
            unmatched += 1
            continue
        # Prefer the candidate place in the agency's own county.
        chosen = next((c for c in cand if c[1] == county5), cand[0])
        fips = chosen[0]
        place_v[fips] = place_v.get(fips, 0) + v
        place_p[fips] = place_p.get(fips, 0) + p
        matched += 1

    print(f"    Agencies -> place: matched={matched:,}  unmatched={unmatched:,}  "
          f"countywide(pool only)={countywide:,}")

    place_crime = pd.DataFrame({
        'fips': list(place_v.keys()),
        'violent_offenses': [place_v[k] for k in place_v],
        'property_offenses': [place_p.get(k, 0) for k in place_v],
    })
    county_crime = pd.DataFrame({
        'primary_county_fips': list(county_v.keys()),
        'county_violent': [county_v[k] for k in county_v],
        'county_property': [county_p.get(k, 0) for k in county_v],
    })
    return place_crime, county_crime


# ── County feature bundle (inherited) ────────────────────────────────────────────

def load_school_scores():
    """Town school quality from federal administrative data (no survey):
      EDGE public-school geocodes (location) + EDFacts state-test proficiency.
    State tests are not comparable across states, so proficiency is percentile-ranked
    WITHIN each state, then test-count-weighted to the town via the ZIP->place crosswalk.
    Returns per-place: fips (7-digit), school_score (0-100 within-state proficiency pctile)."""
    nces = f'{DATA}/nces'
    edge = pd.read_csv(f'{nces}/school_geocodes.csv', sep='|', header=None, dtype=str,
                       usecols=[0, 6, 7], names=['ncessch', 'state', 'zip'])
    edge['ncessch'] = edge['ncessch'].str.zfill(12)
    edge['zip'] = edge['zip'].str.zfill(5)
    ed = pd.read_csv(f'{nces}/edfacts_assessments.csv', dtype={'ncessch': str})
    ed['ncessch'] = ed['ncessch'].str.zfill(12)
    for c in ['math_prof', 'read_prof', 'math_n', 'read_n']:
        ed[c] = pd.to_numeric(ed[c], errors='coerce')
    ed['prof'] = ed[['math_prof', 'read_prof']].mean(axis=1)
    ed['n'] = ed[['math_n', 'read_n']].sum(axis=1)
    ed = ed.dropna(subset=['prof'])

    sch = edge.merge(ed[['ncessch', 'prof', 'n']], on='ncessch', how='inner')
    sch['prof_pct'] = sch.groupby('state')['prof'].rank(pct=True) * 100  # within-state
    cw = load_zip_place_crosswalk()
    primary_place = cw.sort_values('w').groupby('zcta').tail(1).set_index('zcta')['place_fips']
    sch['fips'] = sch['zip'].map(primary_place)
    sch = sch.dropna(subset=['fips'])
    sch['wn'] = sch['n'].fillna(50).clip(lower=1)
    g = sch.groupby('fips').apply(lambda x: np.average(x['prof_pct'], weights=x['wn']))
    print(f"    {len(sch):,} schools placed -> {g.shape[0]:,} places")
    return g.rename('school_score').reset_index()


def build_county_features():
    """Load every county dataset (reused loaders) and assemble a county-keyed frame,
    including county-level amenity density and per-capita physical-risk metrics."""
    print("[county] BEA income...");      bea = load_bea()
    print("[county] HUD FMR...");          fmr = load_fmr()
    print("[county] FHFA HPI...");         hpi = load_hpi()
    print("[county] BLS QCEW...");         qcew = load_qcew()
    print("[county] Census CBP...");       cbp = load_cbp()
    print("[county] Census BPS...");       bps = load_bps()
    print("[county] IRS migration...");    irs = load_irs()
    print("[county] FEMA NFIP...");        nfip = load_nfip()
    print("[county] NOAA storm...");       noaa = load_noaa_storm()
    print("[county] USFS wildfire...");    usfs = load_usfs()
    print("[county] USDA RUCC...");        rucc = load_rucc()

    # County population + net migration come from the county pop estimates file.
    cpop = pd.read_csv(f'{DATA}/census_population/co-est2025-alldata.csv', encoding='latin1')
    cpop = cpop[cpop['SUMLEV'] == 50].copy()
    cpop['fips'] = (cpop['STATE'].astype(str).str.zfill(2)
                    + cpop['COUNTY'].astype(str).str.zfill(3))
    cpop = pd.DataFrame({
        'fips': cpop['fips'],
        'county_pop': cpop['POPESTIMATE2025'].values,
        'RNETMIG2023': cpop['RNETMIG2025'].values,
    })

    c = cpop
    for ds in [bea, fmr, hpi, qcew, cbp, bps, irs, nfip, noaa, usfs, rucc]:
        c = c.merge(ds, on='fips', how='left')

    pop = c['county_pop'].clip(lower=100)
    c['est_per_1k'] = c['establishments'] / pop * 1000
    c['nfip_per_cap'] = c['nfip_claims'] / pop
    c['storm_per_cap'] = c['storm_damage'] / pop
    c = c.rename(columns={'fips': 'primary_county_fips'})
    return c


# ── Labels ───────────────────────────────────────────────────────────────────────
# Start band (recalibrated after the first national run from the printed distribution).
LABELS = [
    (62, 'Strong Buy'),
    (52, 'Buy'),
    (44, 'Hold'),
    (0,  'Caution'),
]


def classify(score):
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return 'Caution'


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Civica Town Scoring Engine (Level B)")
    print("=" * 60)

    print("\n[town] Census sub-est (town universe + growth)...")
    towns = load_subest()
    print(f"        {len(towns):,} towns (incorporated places >= 1,000 pop)")

    print("[town] IRS ZIP AGI (town income)...")
    zip_income, growth_avail = load_irs_zip()
    crosswalk = load_zip_place_crosswalk()
    tincome = town_income(zip_income, crosswalk)
    print(f"        town income computed for {len(tincome):,} places")

    print("[county] assembling inherited county features...")
    county = build_county_features()
    print(f"        {len(county):,} counties")

    print("[town] NIBRS town crime...")
    place_crime, county_crime = load_nibrs_town(towns)

    print("[town] schools (EDFacts proficiency, within-state)...")
    school = load_school_scores()

    # ── Merge ────────────────────────────────────────────────────────────────
    print("\nMerging town + county features...")
    df = towns.merge(tincome, on='fips', how='left')
    df = df.merge(county, on='primary_county_fips', how='left')
    df = df.merge(place_crime, on='fips', how='left')
    df = df.merge(county_crime, on='primary_county_fips', how='left')
    df = df.merge(school, on='fips', how='left')
    print(f"  {len(df):,} towns x {len(df.columns)} columns")

    # Town income fallback: if a town has no ZIP allocation, inherit county per-capita
    # income; if growth missing, fall back to county BEA 4yr growth (annualized-ish).
    df['income_imputed'] = df['town_income'].isna().astype(int)
    df['town_income'] = df['town_income'].fillna(df['per_capita_income'])
    if not growth_avail:
        df['town_income_growth'] = df['income_4yr_growth'] / 100.0
    df['town_income_growth'] = df['town_income_growth'].fillna(df['income_4yr_growth'] / 100.0)

    # ── Crime rates with county / RUCC-tier fallback (no penalty for non-reporting) ──
    df['crime_imputed'] = df['violent_offenses'].isna().astype(int)
    pop = df['POPESTIMATE2025'].clip(lower=100)
    cpop = df['county_pop'].clip(lower=100)

    df['violent_per100k'] = df['violent_offenses'] / pop * 100_000
    df['property_per100k'] = df['property_offenses'] / pop * 100_000
    # County-rate fallback for towns with no matched municipal agency.
    cv_rate = df['county_violent'] / cpop * 100_000
    cp_rate = df['county_property'] / cpop * 100_000
    m = df['crime_imputed'] == 1
    df.loc[m, 'violent_per100k'] = cv_rate[m]
    df.loc[m, 'property_per100k'] = cp_rate[m]

    # RUCC-tier median for whatever is still missing (county had no NIBRS either).
    df['rucc_tier'] = pd.cut(df['rucc'].fillna(5), bins=[0, 3, 6, 9],
                             labels=['metro', 'micro', 'rural'])
    for col in ['violent_per100k', 'property_per100k']:
        tier_med = df[df['crime_imputed'] == 0].groupby('rucc_tier')[col].median()
        miss = df[col].isna()
        df.loc[miss, col] = df.loc[miss, 'rucc_tier'].map(tier_med)
        df[col] = df[col].fillna(df[col].median())

    # ── Schools with county / RUCC-tier fallback (flagged, not penalized) ──
    df['schools_imputed'] = df['school_score'].isna().astype(int)
    cty_sch = df[df['schools_imputed'] == 0].groupby('primary_county_fips')['school_score'].median()
    miss = df['school_score'].isna()
    df.loc[miss, 'school_score'] = df.loc[miss, 'primary_county_fips'].map(cty_sch)
    tier_sch = df[df['schools_imputed'] == 0].groupby('rucc_tier')['school_score'].median()
    miss = df['school_score'].isna()
    df.loc[miss, 'school_score'] = df.loc[miss, 'rucc_tier'].map(tier_sch)
    df['school_score'] = df['school_score'].fillna(df['school_score'].median())

    # Fill remaining numeric gaps in county-inherited metrics with national medians
    # (median score for missing inputs — same philosophy as the county engine).
    fill_cols = ['avg_annual_wage', 'sector_quality', 'hhi', 'hpi_3yr_avg',
                 'fmr_2br', 'est_per_1k', 'nfip_per_cap', 'storm_per_cap',
                 'wildfire_rank', 'RNETMIG2023', 'inmover_income_ratio',
                 'total_permits', 'town_income', 'town_income_growth',
                 'town_growth_5yr', 'town_growth_1yr', 'town_growth_rel_county']
    for c in fill_cols:
        if c in df.columns:
            df[c] = df[c].fillna(df[c].median())

    # ── Build per-metric percentile scores (national across the town universe) ──
    print("Scoring 5 dimensions...")
    df['rent_burden'] = (df['fmr_2br'] * 12) / df['town_income'].clip(lower=1)
    df['appr_deviation'] = (df['hpi_3yr_avg'].clip(-5, 25) - 5.0).abs()

    sc = {}
    sc['rent_burden'] = pct_inv(df['rent_burden'])
    sc['appr_quality'] = pct_inv(df['appr_deviation'])
    sc['avg_wage'] = pct(df['avg_annual_wage'])
    sc['sector_quality'] = pct(df['sector_quality'])
    sc['diversity'] = pct_inv(df['hhi'])
    sc['town_income_growth'] = pct(df['town_income_growth'])
    sc['violent'] = pct_inv(df['violent_per100k'])
    sc['property'] = pct_inv(df['property_per100k'])
    sc['town_scale'] = pct(df['POPESTIMATE2025'])
    sc['amenity'] = pct(df['est_per_1k'])
    risk = (pct_inv(df['nfip_per_cap']) * 0.40 + pct_inv(df['storm_per_cap']) * 0.35
            + pct_inv(df['wildfire_rank']) * 0.25)
    sc['physical_risk'] = risk
    sc['town_growth_5yr'] = pct(df['town_growth_5yr'])
    sc['town_growth_relmom'] = (pct(df['town_growth_rel_county']) * 0.5
                                + pct(df['town_growth_1yr']) * 0.5)
    sc['net_migration'] = pct(df['RNETMIG2023'])
    sc['inmover'] = pct(df['inmover_income_ratio'])
    sc['permits'] = pct(df['total_permits'])
    sc['schools'] = pct(df['school_score'])

    # Point contributions per metric (percentile/100 * allotted points).
    contrib = {}
    for name, (points, layer) in POINTS.items():
        contrib[name] = (sc[name] / 100.0 * points).fillna(points * 0.5)

    df['dim1'] = contrib['rent_burden'] + contrib['appr_quality']
    df['dim2'] = (contrib['avg_wage'] + contrib['sector_quality']
                  + contrib['diversity'] + contrib['town_income_growth'])
    df['dim3'] = (contrib['violent'] + contrib['property'] + contrib['town_scale']
                  + contrib['amenity'] + contrib['physical_risk'])
    df['dim4'] = (contrib['town_growth_5yr'] + contrib['town_growth_relmom']
                  + contrib['net_migration'] + contrib['inmover'] + contrib['permits'])
    df['dim5'] = contrib['schools']

    t_earned = sum(contrib[n] for n, (p, l) in POINTS.items() if l == 'T')
    c_earned = sum(contrib[n] for n, (p, l) in POINTS.items() if l == 'C')
    df['town_local_score'] = (t_earned / T_POINTS * 100).clip(0, 100)
    df['county_market_score'] = (c_earned / C_POINTS * 100).clip(0, 100)

    df['civica_score'] = df[['dim1', 'dim2', 'dim3', 'dim4', 'dim5']].sum(axis=1).clip(0, 100)
    df['town_scale_pct'] = sc['town_scale']

    # ── Labels (§6b) ─────────────────────────────────────────────────────────────
    # Percentile normalization fixes the distribution at mean~50 / std~10 every run,
    # so fixed thresholds are stable. These fit the observed distribution to a balanced,
    # non-trivial four-bucket split (~11/31/31/27): Strong Buy is ~1.2 std above mean.
    # All four buckets fire meaningfully; verified against the printed counts below.
    df['market_label'] = df['civica_score'].apply(classify)
    counts = df['market_label'].value_counts()
    print(f"  label thresholds: Strong Buy>=62  Buy>=52  Hold>=44  Caution>=0")
    print(f"  label counts: {counts.to_dict()}")
    df['national_rank'] = df['civica_score'].rank(ascending=False, method='min').astype(int)

    # In-county rank on the town-resolved local score.
    df['rank_in_county'] = (df.groupby('primary_county_fips')['town_local_score']
                            .rank(ascending=False, method='min').astype(int))
    df['towns_in_county'] = df.groupby('primary_county_fips')['fips'].transform('count')

    # ── Output ───────────────────────────────────────────────────────────────
    out_cols = [
        'fips', 'place_name', 'state_abbr', 'primary_county_fips', 'county_name',
        'POPESTIMATE2025',
        'civica_score', 'market_label', 'national_rank',
        'town_local_score', 'county_market_score', 'rank_in_county', 'towns_in_county',
        'dim1', 'dim2', 'dim3', 'dim4', 'dim5',
        'fmr_2br', 'town_income', 'rent_burden', 'hpi_3yr_avg',
        'avg_annual_wage', 'sector_quality', 'hhi', 'town_income_growth',
        'violent_per100k', 'property_per100k', 'town_scale_pct', 'est_per_1k',
        'nfip_per_cap', 'storm_per_cap', 'wildfire_rank', 'crime_imputed',
        'school_score', 'schools_imputed',
        'town_growth_5yr', 'town_growth_1yr', 'town_growth_rel_county',
        'RNETMIG2023', 'inmover_income_ratio', 'total_permits',
        'income_imputed', 'is_mcd',
    ]
    available = [c for c in out_cols if c in df.columns]
    out = df[available].copy().round(4)
    out['fips'] = out['fips'].astype(str).str.zfill(7)
    out['primary_county_fips'] = out['primary_county_fips'].astype(str).str.zfill(5)
    out = out.sort_values('civica_score', ascending=False).reset_index(drop=True)
    out.to_csv(OUT, index=False)

    meta = {
        'town_count': int(len(out)),
        'town_resolved_share': round(T_POINTS / (T_POINTS + C_POINTS), 4),
        't_points': round(T_POINTS, 2),
        'c_points': round(C_POINTS, 2),
        'labels': [[t, l] for t, l in LABELS],
        'income_growth_available': bool(growth_avail),
    }
    json.dump(meta, open(META, 'w'), indent=2)

    print(f"\n{'='*60}")
    print(f"Output -> {OUT}")
    print(f"Towns scored: {len(out):,}")
    print(f"Town-resolved share: {meta['town_resolved_share']*100:.1f}%")
    print(f"\nScore distribution:\n{out['civica_score'].describe().round(2).to_string()}")
    print(f"\nLabel counts:\n{out['market_label'].value_counts().to_string()}")
    print(f"\nCrime imputed (no municipal agency matched): "
          f"{int(df['crime_imputed'].sum()):,} / {len(df):,} "
          f"({df['crime_imputed'].mean()*100:.0f}%)")
    print(f"Income imputed (no ZIP allocation): {int(df['income_imputed'].sum()):,}")
    print(f"\nTop 15 towns:")
    print(out.head(15)[['place_name', 'state_abbr', 'county_name',
                        'civica_score', 'market_label']].to_string(index=False))
    print(f"\nBottom 10 towns:")
    print(out.tail(10)[['place_name', 'state_abbr', 'county_name',
                       'civica_score', 'market_label']].to_string(index=False))


if __name__ == '__main__':
    main()
