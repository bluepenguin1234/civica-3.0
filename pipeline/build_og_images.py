#!/usr/bin/env python3
"""Generate per-town Open Graph share cards (1200x630 PNG) -> docs/output/og/<fips>.png.

  python build_og_images.py --top 2000       # the 2000 most-populous towns (production set)
  python build_og_images.py                   # PROTOTYPE: 1 town per verdict (highest pop)
  python build_og_images.py 4753460 3651000   # specific place FIPS
  python build_og_images.py --all             # every town (~1GB — avoid)

Fonts are the bundled brand TTFs in assets/fonts/ (Poppins display + Inconsolata score),
so output matches the site exactly and is reproducible off Windows. Writes a manifest
(_cards.json: the list of fips that have a card) so town_generator.py can point each
town's og:image at its card and fall back to the generic image otherwise.
"""
import os
import sys
import json
import argparse
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUTDIR = os.path.join(ROOT, 'docs', 'output', 'og')
CSV = os.path.join(HERE, 'town_scores.csv')
FONTS = os.path.join(HERE, 'assets', 'fonts')
MANIFEST = os.path.join(OUTDIR, '_cards.json')

W, H, S = 1200, 630, 2          # final size + supersample factor
NAVY = (13, 45, 82)
BLUE = (11, 87, 194)
MUTED = (91, 97, 112)
WHITE = (255, 255, 255)
VCOL = {'Strong Buy': (22, 163, 74), 'Buy': (11, 87, 194),
        'Hold': (245, 158, 11), 'Caution': (220, 38, 38)}

_FILES = {'reg': 'Poppins-Regular.ttf', 'semi': 'Poppins-SemiBold.ttf',
          'bold': 'Poppins-Bold.ttf', 'xbold': 'Poppins-ExtraBold.ttf'}
_cache = {}


def font(style, px):
    """Cached brand font. 'mono' = Inconsolata variable pinned to Bold (wdth100/wght700)."""
    key = (style, px)
    if key in _cache:
        return _cache[key]
    if style == 'mono':
        f = ImageFont.truetype(os.path.join(FONTS, 'Inconsolata.ttf'), px)
        try:
            f.set_variation_by_axes([100, 700])   # axis order: wdth, wght
        except Exception:
            pass
    else:
        f = ImageFont.truetype(os.path.join(FONTS, _FILES[style]), px)
    _cache[key] = f
    return f


def blend(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def render(row, out_path):
    img = Image.new('RGB', (W * S, H * S), WHITE)
    d = ImageDraw.Draw(img)
    P = lambda v: int(v * S)
    Fp = lambda style, size: font(style, int(size * S))

    place = str(row['place_name'])
    state = str(row['state_abbr'])
    county = str(row['county_name'])
    pop = int(row['POPESTIMATE2025'])
    score = float(row['civica_score'])
    label = str(row['market_label'])
    vcol = VCOL.get(label, BLUE)

    # ── bottom accent strip (verdict color) ──
    d.rectangle([0, P(618), P(W), P(H)], fill=vcol)

    # ── logo (favicon mark + wordmark) ──
    lx, ly, M, f = 80, 58, 46, 46 / 30.0
    d.rounded_rectangle([P(lx), P(ly), P(lx + M), P(ly + M)], radius=P(11), fill=NAVY)
    for bx, by, bw, bh, t in [(6, 16, 4, 9, .65), (13, 8, 4, 17, 1.0), (20, 12, 4, 13, .8)]:
        d.rounded_rectangle([P(lx + bx * f), P(ly + by * f), P(lx + (bx + bw) * f), P(ly + (by + bh) * f)],
                            radius=P(1.5), fill=blend(NAVY, WHITE, t))
    wf = Fp('bold', 30)
    wx, wy = P(lx + M + 14), P(ly + 9)
    d.text((wx, wy), 'civi', font=wf, fill=NAVY)
    d.text((wx + d.textlength('civi', font=wf), wy), 'ca', font=wf, fill=BLUE)

    # ── eyebrow ──
    d.text((P(80), P(196)), 'T O W N   R E P O R T', font=Fp('bold', 19), fill=MUTED)

    # ── town name (auto-fit to the text column) ──
    maxw = 740
    s = 92
    while s > 44:
        nf = Fp('xbold', s)
        if d.textlength(place, font=nf) <= P(maxw):
            break
        s -= 3
    d.text((P(80), P(232)), place, font=nf, fill=NAVY)
    bb = d.textbbox((P(80), P(232)), place, font=nf)
    suby = bb[3] / S + 16

    # ── sub line: county, state, pop ──
    d.text((P(80), P(suby)), f'{county}, {state}  ·  Pop. {pop:,}',
           font=Fp('reg', 29), fill=MUTED)

    # ── score disc + verdict label (right) ──
    cx, cy, R = 1000, 300, 150
    d.ellipse([P(cx - R), P(cy - R), P(cx + R), P(cy + R)], fill=vcol)
    d.text((P(cx), P(cy - 4)), f'{score:.0f}', font=Fp('mono', 150),
           fill=WHITE, anchor='mm')
    d.text((P(cx), P(cy + R + 46)), label, font=Fp('bold', 44), fill=vcol, anchor='mm')

    # ── footer ──
    d.text((P(80), P(566)), '100% federal government data  ·  no surveys, no listings, no ads',
           font=Fp('reg', 23), fill=MUTED)

    img.resize((W, H), Image.LANCZOS).save(out_path, 'PNG', optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, help='render the N most-populous towns (production set)')
    ap.add_argument('--all', action='store_true', help='render every town (~1GB — avoid)')
    ap.add_argument('fips', nargs='*', help='specific place FIPS to render')
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    df = pd.read_csv(CSV)
    df['fips'] = df['fips'].astype(str).str.zfill(7)

    if args.top:
        rows = [r for _, r in df.sort_values('POPESTIMATE2025', ascending=False).head(args.top).iterrows()]
        write_manifest = True
    elif args.all:
        rows = [r for _, r in df.iterrows()]
        write_manifest = True
    elif args.fips:
        rows = [df[df['fips'] == a.zfill(7)].iloc[0] for a in args.fips]
        write_manifest = False
    else:  # prototype: one town per verdict, highest population
        rows = []
        for v in ['Strong Buy', 'Buy', 'Hold', 'Caution']:
            sub = df[df['market_label'] == v].sort_values('POPESTIMATE2025', ascending=False)
            if len(sub):
                rows.append(sub.iloc[0])
        write_manifest = False

    done = []
    for i, r in enumerate(rows, 1):
        fips = str(r['fips']).zfill(7)
        render(r, os.path.join(OUTDIR, f'{fips}.png'))
        done.append(fips)
        if i % 200 == 0 or i == len(rows):
            print(f'  rendered {i}/{len(rows)}  (latest: {r["place_name"]}, {r["state_abbr"]})')

    if write_manifest:
        with open(MANIFEST, 'w', encoding='utf-8') as f:
            json.dump(sorted(done), f)
        print(f'manifest: {len(done)} fips -> output/og/_cards.json')
    print(f'Done. {len(done)} card(s) in docs/output/og/')


if __name__ == '__main__':
    main()
