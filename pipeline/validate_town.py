#!/usr/bin/env python3
"""
validate_town.py — the gate for the town-level build (TOWN_HANDOFF.md §13a).

Runs two kinds of checks:
  • SCORES   — always, against town_scores.csv (+ town_scores_meta.json).
  • HTML     — against any files already generated in output/towns/*.html.

A red check means STOP and fix — do not weaken a check to make a run pass.
Exit code 0 = all green; 1 = at least one failure.
"""

import os
import sys
import json
import glob
import re

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(ROOT, 'town_scores.csv')
META = os.path.join(ROOT, 'town_scores_meta.json')
TOWN_HTML_DIR = os.path.join(os.path.dirname(ROOT), 'docs', 'output', 'towns')

fails = []
passes = []


def check(cond, msg):
    if cond:
        passes.append(msg)
        print(f"  [ OK ] {msg}")
    else:
        fails.append(msg)
        print(f"  [FAIL] {msg}")


def validate_scores():
    print("\n=== SCORES (town_scores.csv) ===")
    if not os.path.exists(SCORES):
        check(False, "town_scores.csv exists")
        return None
    df = pd.read_csv(SCORES, dtype={'fips': str, 'primary_county_fips': str})

    n = len(df)
    print(f"  town count = {n:,}")
    # Universe = incorporated places (>=1,000 pop) + New England governing towns (MCDs).
    check(9000 <= n <= 21000, f"town count in expected band [9000, 21000] (got {n:,})")

    s = df['civica_score']
    print(f"  score mean={s.mean():.2f} std={s.std():.2f} "
          f"min={s.min():.2f} max={s.max():.2f}")
    check(45 <= s.mean() <= 55, f"score mean ~50 (got {s.mean():.2f})")
    check(4 <= s.std() <= 12, f"score std in [4,12] (got {s.std():.2f})")
    check(s.min() >= 0 and s.max() <= 100, "scores within [0,100]")

    # fips are 7-digit place codes, or 10-digit state+county+cousub for New England MCD towns
    bad_fips = df['fips'][~df['fips'].str.match(r'^\d{7}$|^\d{10}$')]
    check(len(bad_fips) == 0, f"all fips are 7- or 10-digit strings ({len(bad_fips)} bad)")

    # no nulls in the headline columns
    check(df['civica_score'].notna().all(), "no null civica_score")
    check(df['market_label'].notna().all(), "no null market_label")

    # towns within the same county must NOT be identical (proves T metrics vary them)
    multi = df.groupby('primary_county_fips').filter(lambda g: len(g) >= 2)
    if len(multi):
        var = multi.groupby('primary_county_fips')['civica_score'].var()
        share_varying = (var > 1e-6).mean()
        print(f"  multi-town counties with score variance>0: {share_varying*100:.1f}%")
        check(share_varying > 0.90,
              f">90% of multi-town counties have intra-county score variance (got {share_varying*100:.1f}%)")
    else:
        check(False, "found multi-town counties to test intra-county variance")

    # all 4 labels fire with non-trivial counts
    counts = df['market_label'].value_counts()
    print("  label counts:", counts.to_dict())
    expected = {'Strong Buy', 'Buy', 'Hold', 'Caution'}
    check(set(counts.index) == expected,
          f"exactly the 4 labels present (got {set(counts.index)})")
    if set(counts.index) == expected:
        check((counts >= max(10, int(0.01 * n))).all(),
              "every label fires with a non-trivial count (>=1% of towns)")

    # town-resolved share ~51% — read what the engine actually computed
    if os.path.exists(META):
        meta = json.load(open(META))
        tshare = meta.get('town_resolved_share')
        print(f"  town_resolved_share (from engine) = {tshare}")
        check(tshare is not None and 0.45 <= tshare <= 0.66,
              f"town-resolved share in [0.45, 0.66] (got {tshare})")
    else:
        check(False, "town_scores_meta.json exists (engine must emit town_resolved_share)")

    # no Zillow-derived columns should survive
    zillow_cols = [c for c in df.columns if c.lower() in (
        'median_home_value', 'inventory', 'pr_ratio', 'price_income',
        'breakeven_yrs', 'monthly_piti', 'home_appreciation_total_3yr', 'zhvi_imputed')]
    check(len(zillow_cols) == 0, f"no Zillow-derived columns in CSV (found {zillow_cols})")

    return df


def validate_html(df):
    print("\n=== HTML (output/towns/*.html) ===")
    files = glob.glob(os.path.join(TOWN_HTML_DIR, '*.html'))
    if not files:
        print("  (no town HTML generated yet — skipping HTML checks)")
        return
    print(f"  {len(files):,} town pages found; sampling up to 40")
    import random
    sample = files if len(files) <= 40 else random.sample(files, 40)

    bad_zillow = bad_token = bad_bars = bad_rank = bad_dash = 0
    for fp in sample:
        html = open(fp, encoding='utf-8').read()
        low = html.lower()
        if 'zillow' in low or 'price-to-rent' in low or 'price/rent' in low:
            bad_zillow += 1
        # leftover unreplaced tokens like {score} {place_name}
        # (exclude Leaflet tile-URL tokens {s}{z}{x}{y}{r}, which are legitimate)
        leftover = [m for m in re.findall(r'\{[a-zA-Z_][a-zA-Z0-9_]*\}', html)
                    if m not in ('{s}', '{z}', '{x}', '{y}', '{r}')]
        if leftover:
            bad_token += 1
        # 5 dimension rows present (markup uses class="dimrow"; excludes the CSS rule)
        if html.count('class="dimrow"') != 5:
            bad_bars += 1
        # rank-in-county line present
        if 'Ranks #' not in html and 'ranks #' not in low:
            bad_rank += 1
        # template default dashoffset left untouched
        if 'stroke-dashoffset: 0;' in low or 'stroke-dashoffset:0;' in low:
            bad_dash += 1

    check(bad_zillow == 0, f"no Zillow/price-to-rent strings in town HTML ({bad_zillow} bad)")
    check(bad_token == 0, f"no leftover {{token}} braces in town HTML ({bad_token} bad)")
    check(bad_bars == 0, f"4 dimension bars present in town HTML ({bad_bars} bad)")
    check(bad_rank == 0, f"rank-in-county line present in town HTML ({bad_rank} bad)")
    check(bad_dash == 0, f"score ring dashoffset rendered, not template default ({bad_dash} bad)")


def main():
    print("=" * 64)
    print("  validate_town.py")
    print("=" * 64)
    df = validate_scores()
    if df is not None:
        validate_html(df)

    print("\n" + "=" * 64)
    print(f"  {len(passes)} passed, {len(fails)} failed")
    print("=" * 64)
    if fails:
        print("RED:")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("ALL GREEN")


if __name__ == '__main__':
    main()
