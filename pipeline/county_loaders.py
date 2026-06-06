#!/usr/bin/env python3
"""
county_loaders.py — county-level federal dataset loaders for the Civica town model.
These are reused verbatim by town_scoring_engine.py and joined to each town via its
primary county FIPS. Percentile helpers (pct/pct_inv) rank metrics nationally.
Every loader returns a DataFrame keyed on 5-digit county `fips`.
"""
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'civica_data')


def to_fips5(state, county):
    try:
        return str(int(state)).zfill(2) + str(int(county)).zfill(3)
    except (ValueError, TypeError):
        return None

def pct(s):
    """National percentile rank 0-100: higher raw value = higher score."""
    return s.rank(pct=True, na_option='keep') * 100

def pct_inv(s):
    """National percentile rank 0-100: lower raw value = higher score."""
    return (1 - s.rank(pct=True, na_option='keep')) * 100

def parse_damage(v):
    """Convert NOAA storm damage strings ('5.00K', '1.50M') to float dollars."""
    if pd.isna(v) or str(v).strip() == '':
        return 0.0
    v = str(v).strip().upper()
    if v.endswith('K'):  return float(v[:-1]) * 1_000
    if v.endswith('M'):  return float(v[:-1]) * 1_000_000
    if v.endswith('B'):  return float(v[:-1]) * 1_000_000_000
    try:
        return float(v)
    except ValueError:
        return 0.0

def load_bea():
    """BEA CAINC1: per capita personal income (LineCode=3), 2024 latest."""
    df = pd.read_csv(
        f'{DATA}/bea_income/CAINC1__ALL_AREAS_1969_2024.csv',
        encoding='latin1'
    )
    df = df[df['LineCode'] == 3].copy()
    df['fips'] = df['GeoFIPS'].str.strip().str.strip('"').str.strip().str.zfill(5)
    df = df[~df['fips'].str.endswith('000')]  # drop state/national totals

    year_cols = sorted([c for c in df.columns if c.isdigit()])
    latest = year_cols[-1]
    prior  = str(int(latest) - 4)
    if prior not in year_cols:
        prior = year_cols[-5]

    df['per_capita_income']  = pd.to_numeric(df[latest], errors='coerce')
    df['income_prior']       = pd.to_numeric(df[prior],  errors='coerce')
    # Nominal 4-year growth — compare to cumulative CPI for real gains
    df['income_4yr_growth']  = (df['per_capita_income'] / df['income_prior'] - 1) * 100
    return df[['fips', 'per_capita_income', 'income_4yr_growth']].dropna(subset=['fips'])

def load_fmr():
    """HUD FY2026 Fair Market Rents: 2BR as median rent proxy."""
    df = pd.read_excel(
        f'{DATA}/hud_fmr/FY26_FMRs_revised.xlsx',
        engine='calamine'
    )
    # HUD fips format: state_int + county_3digit + "99999" suffix
    # e.g. Autauga AL = 100199999 → 100199999 // 100000 = 1001 → "01001"
    df['fips'] = (df['fips'].astype(float).astype(int) // 100000).astype(str).str.zfill(5)
    df['fmr_2br'] = pd.to_numeric(df['fmr_2'], errors='coerce')
    return df.groupby('fips')['fmr_2br'].mean().reset_index()

def load_hpi():
    """FHFA HPI: 3-year average annual appreciation + latest annual change."""
    df = pd.read_excel(f'{DATA}/fhfa_hpi/hpi_at_county.xlsx', header=5)
    df.columns = ['state', 'county', 'fips', 'year', 'annual_chg', 'hpi', 'hpi90', 'hpi2000']
    df = df.dropna(subset=['fips'])
    df['fips']      = df['fips'].astype(float).astype(int).astype(str).str.zfill(5)
    df['year']      = pd.to_numeric(df['year'],      errors='coerce')
    df['annual_chg'] = pd.to_numeric(df['annual_chg'], errors='coerce')

    max_yr  = df['year'].max()
    recent  = df[df['year'] >= max_yr - 2]
    avg3    = recent.groupby('fips')['annual_chg'].mean().reset_index()
    avg3.columns = ['fips', 'hpi_3yr_avg']

    latest_yr = df[df['year'] == max_yr][['fips', 'annual_chg']].rename(
        columns={'annual_chg': 'hpi_latest'}
    )
    return avg3.merge(latest_yr, on='fips', how='left')

def load_qcew():
    """BLS QCEW wages, employment size, sector quality score, HHI. Uses 2024 if available, falls back to 2023."""
    qcew_2024 = f'{DATA}/bls_qcew/2024.annual.singlefile.csv'
    qcew_2023 = f'{DATA}/bls_qcew/2023.annual.singlefile.csv'
    qcew_path = qcew_2024 if os.path.exists(qcew_2024) else qcew_2023
    print(f"    Streaming BLS QCEW ({os.path.basename(qcew_path)})...")
    totals, sectors = [], []

    for chunk in pd.read_csv(
        qcew_path,
        chunksize=500_000,
        dtype={'area_fips': str, 'industry_code': str}
    ):
        county = chunk[
            chunk['area_fips'].str.len().eq(5) &
            ~chunk['area_fips'].str.endswith('000')
        ]
        private = county[county['own_code'] == 5]

        # own_code=5 + industry_code='10' → total private sector per county
        totals.append(
            private[private['industry_code'] == '10']
            [['area_fips', 'annual_avg_emplvl', 'avg_annual_pay']].copy()
        )
        # own_code=5 + 2-digit NAICS (excluding '10') → private by supersector
        sectors.append(
            private[
                private['industry_code'].str.len().eq(2) &
                (private['industry_code'] != '10')
            ][['area_fips', 'industry_code', 'annual_avg_emplvl']].copy()
        )

    total_df = pd.concat(totals, ignore_index=True).rename(columns={'area_fips': 'fips'})
    total_df['annual_avg_emplvl'] = pd.to_numeric(total_df['annual_avg_emplvl'], errors='coerce')
    total_df['avg_annual_pay']    = pd.to_numeric(total_df['avg_annual_pay'],    errors='coerce')
    agg = total_df.groupby('fips').agg(
        private_employment=('annual_avg_emplvl', 'sum'),
        avg_annual_wage=('avg_annual_pay', 'mean')
    ).reset_index()

    sec_df = pd.concat(sectors, ignore_index=True).rename(columns={'area_fips': 'fips'})
    sec_df['annual_avg_emplvl'] = pd.to_numeric(sec_df['annual_avg_emplvl'], errors='coerce').fillna(0)
    sec_df['naics2'] = sec_df['industry_code'].astype(str).str[:2]

    # Sector quality weights — ratios reflect BLS OEWS median wages by supersector
    # relative to the all-private median. Professional/Finance consistently 30-40%
    # above private median; Retail/Manufacturing 20-40% below. See METHODOLOGY.md §14.6.
    WEIGHTS = {
        '54': 1.30,  # Professional, Scientific, Technical (OEWS: ~40% above median)
        '52': 1.30,  # Finance & Insurance (OEWS: ~35% above median)
        '62': 1.00,  # Healthcare & Social Assistance (near median)
        '61': 1.00,  # Educational Services (near median)
        '23': 0.80,  # Construction (cyclical; ~10% above median but volatile)
        '44': 0.60,  # Retail Trade (OEWS: ~25% below median)
        '45': 0.60,  # Retail Trade
        '31': 0.60,  # Manufacturing (secular US employment decline; ~10% below median)
        '32': 0.60,
        '33': 0.60,
    }
    sec_df['weight'] = sec_df['naics2'].map(WEIGHTS).fillna(1.00)

    def _sector_quality(g):
        tot = g['annual_avg_emplvl'].sum()
        return (g['annual_avg_emplvl'] * g['weight']).sum() / tot if tot > 0 else np.nan

    def _hhi(g):
        tot = g['annual_avg_emplvl'].sum()
        if tot == 0:
            return np.nan
        return ((g['annual_avg_emplvl'] / tot) ** 2).sum() * 10_000  # 0–10,000 scale

    sq  = sec_df.groupby('fips').apply(_sector_quality).reset_index(name='sector_quality')
    hhi = sec_df.groupby('fips').apply(_hhi).reset_index(name='hhi')
    return agg.merge(sq, on='fips', how='left').merge(hhi, on='fips', how='left')

def load_cbp():
    """Census CBP 2022: total establishments per county (amenity density proxy)."""
    print("    Reading Census CBP (106 MB)...")
    df = pd.read_csv(f'{DATA}/census_cbp/cbp23co.txt', dtype=str, low_memory=False)
    df['fips'] = df['fipstate'].str.zfill(2) + df['fipscty'].str.zfill(3)
    total = df[df['naics'] == '------'][['fips', 'est']].copy()
    total['establishments'] = pd.to_numeric(total['est'], errors='coerce')
    return total.groupby('fips')['establishments'].sum().reset_index()

def load_bps():
    """Census Building Permits Survey 2022: total new housing units permitted."""
    # BPS county file: 2-row header + 1 blank line, then data
    # Cols: 0=year, 1=state_fips, 2=county_fips, 3=region, 4=division, 5=name
    #        6=1unit_bldg, 7=1unit_units, 8=1unit_val
    #        9=2unit_bldg, 10=2unit_units, ...  15=5+unit_bldg, 16=5+unit_units
    df = pd.read_csv(
        f'{DATA}/census_bps/co2025a.txt',
        skiprows=3, header=None, dtype=str
    )
    df = df.dropna(subset=[1])  # drop blank rows
    df['fips'] = df[1].str.strip().str.zfill(2) + df[2].str.strip().str.zfill(3)
    for col in [7, 10, 13, 16]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    df['total_permits'] = df[7] + df[10] + df[13] + df[16]
    return df.groupby('fips')['total_permits'].sum().reset_index()

def load_irs():
    """IRS Migration 2022-2023: in-mover income quality (inflow AGI / outflow AGI)."""
    def _clean(df, dest_state_col, dest_county_col, origin_state_col):
        df = df.copy()
        df[origin_state_col] = pd.to_numeric(df[origin_state_col], errors='coerce')
        df = df[~df[origin_state_col].isin([96, 97, 98, 99])]  # drop totals/special codes
        df['fips'] = (df[dest_state_col].astype(str).str.zfill(2) +
                      df[dest_county_col].astype(str).str.zfill(3))
        df['n1']  = pd.to_numeric(df['n1'],  errors='coerce')
        df['agi'] = pd.to_numeric(df['agi'], errors='coerce')
        return df

    inf = pd.read_csv(f'{DATA}/irs_migration/countyinflow2223.csv',  encoding='latin1')
    out = pd.read_csv(f'{DATA}/irs_migration/countyoutflow2223.csv', encoding='latin1')

    inf = _clean(inf, 'y2_statefips', 'y2_countyfips', 'y1_statefips')  # into destination (y2)
    out = _clean(out, 'y1_statefips', 'y1_countyfips', 'y2_statefips')  # out of origin (y1)

    inf_g = inf.groupby('fips').agg(in_hh=('n1','sum'), in_agi=('agi','sum')).reset_index()
    out_g = out.groupby('fips').agg(out_hh=('n1','sum'), out_agi=('agi','sum')).reset_index()

    m = inf_g.merge(out_g, on='fips', how='outer')
    # Require ≥10 households on each side to produce a reliable average AGI
    m['in_avg_agi']  = np.where(m['in_hh']  >= 10, m['in_agi']  / m['in_hh'],  np.nan)
    m['out_avg_agi'] = np.where(m['out_hh'] >= 10, m['out_agi'] / m['out_hh'], np.nan)
    # Ratio > 1 means higher-income people moving in than leaving (positive demand signal)
    m['inmover_income_ratio'] = np.where(
        (m['in_hh'] >= 10) & (m['out_hh'] >= 10),
        m['in_avg_agi'] / m['out_avg_agi'],
        np.nan
    )
    return m[['fips', 'inmover_income_ratio', 'in_hh', 'out_hh']]

def load_nfip():
    """FEMA NFIP: flood insurance claims paid, 2015-2024 (10-year window)."""
    df = pd.read_csv(
        f'{DATA}/fema_nfip/fema_nfip_claims.csv',
        dtype={'countyCode': str},
        low_memory=False
    )
    df['fips'] = df['countyCode'].str.zfill(5)
    df['paid'] = (
        pd.to_numeric(df['amountPaidOnBuildingClaim'], errors='coerce').fillna(0) +
        pd.to_numeric(df['amountPaidOnContentsClaim'], errors='coerce').fillna(0)
    )
    if 'yearOfLoss' in df.columns:
        df['yr'] = pd.to_numeric(df['yearOfLoss'], errors='coerce')
        df = df[df['yr'].between(2015, 2024)]  # enforce documented 10-year window
    return df.groupby('fips')['paid'].sum().reset_index().rename(columns={'paid': 'nfip_claims'})

def load_noaa_storm():
    """NOAA Storm Events 2019-2023: property damage by county."""
    print("    Reading NOAA Storm Events (5 files, ~323 MB)...")
    parts = []
    storm_dir = f'{DATA}/noaa_storm_events'
    for fn in sorted(os.listdir(storm_dir)):
        if not fn.endswith('.csv'):
            continue
        # Only include files for years in the documented 5-year window (2020-2024)
        # NOAA filenames follow pattern: StormEvents_details-ftp_v1.0_d{YYYY}_*.csv
        if not any(f'_d{y}_' in fn for y in range(2020, 2025)):
            continue
        df = pd.read_csv(
            f'{storm_dir}/{fn}',
            dtype={'STATE_FIPS': str, 'CZ_FIPS': str},
            usecols=['STATE_FIPS', 'CZ_FIPS', 'CZ_TYPE', 'DAMAGE_PROPERTY'],
            low_memory=False
        )
        df = df[df['CZ_TYPE'] == 'C'].copy()  # county zones only (not forecast zones)
        df['fips']   = df['STATE_FIPS'].str.zfill(2) + df['CZ_FIPS'].str.zfill(3)
        df['damage'] = df['DAMAGE_PROPERTY'].apply(parse_damage)
        parts.append(df[['fips', 'damage']])

    combined = pd.concat(parts, ignore_index=True)
    return combined.groupby('fips')['damage'].sum().reset_index().rename(
        columns={'damage': 'storm_damage'}
    )

def load_usfs():
    """USFS Wildfire Risk: national rank by county (0=safest, 1=highest risk)."""
    df = pd.read_excel(
        f'{DATA}/usfs_wildfire/wrc_download_20260415.xlsx',
        sheet_name='Counties'
    )
    df['fips'] = df['GEOID'].astype(str).str.zfill(5)
    return df[['fips', 'RISK_NATIONAL_RANK']].rename(columns={'RISK_NATIONAL_RANK': 'wildfire_rank'})

def load_rucc():
    """USDA Rural-Urban Continuum Codes 2023: 1=largest metro, 9=most rural."""
    df = pd.read_excel(f'{DATA}/usda_rucc/ruralurbancodes2023.xlsx')
    df['fips'] = df['FIPS'].astype(str).str.zfill(5)
    return df[['fips', 'RUCC_2023']].rename(columns={'RUCC_2023': 'rucc'})
