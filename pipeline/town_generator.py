#!/usr/bin/env python3
"""
town_generator.py — Civica Town-Level Model
Reads town_scores.csv -> one HTML page per town in output/towns/{place_fips}.html,
plus output/town_index.json (front-page search) and output/towns/_progress.json (ledger).

Design system (colors, fonts, cards, score ring, dimension bars) is reused verbatim
from town_template.html via extract_style(). The town report is intentionally
lean: one score ring, five dimension bars, a four-label verdict, an in-county rank line,
and the honest town/county data-coverage chip. No Zillow / price-to-rent / breakeven UI.

Usage:
  python town_generator.py              # generate every town in every state (one pass)
  python town_generator.py --state TX   # generate one state (loop / resume fallback)
"""

import os
import sys
import json
import argparse

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)            # repo root
SITE = os.path.join(ROOT, 'docs')       # the deployable website (GitHub Pages source)
TEMPLATE = os.path.join(BASE, 'town_template.html')
SCORES = os.path.join(BASE, 'town_scores.csv')
OUT_DIR = os.path.join(SITE, 'output', 'towns')
STATE_DIR = os.path.join(SITE, 'output', 'states')
OUT_INDEX = os.path.join(SITE, 'output', 'town_index.json')
PROGRESS = os.path.join(OUT_DIR, '_progress.json')
SITE_URL = 'https://civica.app'
TOWN_URL_BASE = f'{SITE_URL}/output/towns'
STATE_URL_BASE = f'{SITE_URL}/output/states'
GA4_ID = ''

DIM_MAX = {'dim1': 25, 'dim2': 24, 'dim3': 22, 'dim4': 15, 'dim5': 14}
DIM_NAMES = [
    ('dim1', 'Affordability', '🏠'),
    ('dim2', 'Economy', '💼'),
    ('dim3', 'Safety & Place', '🛡️'),
    ('dim4', 'Growth', '📈'),
    ('dim5', 'Schools', '🎓'),
]

# 4 labels -> existing vb pill styles (Strong Buy/Buy green/blue, Hold yellow, Caution red).
LABEL_PILL = {
    'Strong Buy': 'vb-accelerating',
    'Buy': 'vb-established',
    'Hold': 'vb-frontier',
    'Caution': 'vb-avoid',
}
LABEL_SUB = {
    'Strong Buy': 'Town fundamentals lead its county and the nation',
    'Buy': 'Solid fundamentals with room to run',
    'Hold': 'Balanced, middle-of-the-pack market',
    'Caution': 'Multiple fundamentals trail national peers',
}
LABEL_SIG = {
    'Strong Buy': ('sig-green', '✅'),
    'Buy': ('sig-green', '✅'),
    'Hold': ('sig-yellow', '👀'),
    'Caution': ('sig-red', '⚠️'),
}

STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
    'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire',
    'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina',
    'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania',
    'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee',
    'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia',
}


# ── Helpers ──────────────────────────────────────────────────────────────────────

def ring_offset(score):
    return f'{289.02 * (1 - score / 100):.2f}'


def money(v, fallback='N/A'):
    try:
        if pd.isna(v):
            return fallback
        return f'${v:,.0f}'
    except Exception:
        return fallback


def pctfmt(v, fallback='N/A', signed=False):
    try:
        if pd.isna(v):
            return fallback
        return f'{v:+.1f}%' if signed else f'{v:.1f}%'
    except Exception:
        return fallback


def extract_style(template_html):
    start = template_html.find('<style>')
    end = template_html.find('</style>') + len('</style>')
    return template_html[start:end] if start != -1 else ''


def rank_phrase(rank, total):
    if total <= 1:
        return 'the only scored town in the county'
    third = total / 3.0
    if rank <= max(1, third):
        return 'one of the strongest towns in its county'
    if rank <= 2 * third:
        return 'mid-pack among towns in its county'
    return 'below the stronger towns in its county'


# ── Page builders ────────────────────────────────────────────────────────────────

def build_head(row, place, state, county, score, label, fips, style):
    desc = (
        f"{place}, {state} scores {score:.0f}/100 on Civica's 5-dimension town housing model. "
        f"Signal: {label}. Ranks #{int(row['rank_in_county'])} of {int(row['towns_in_county'])} "
        f"towns in {county}. Town-level crime, income, and growth on 100% federal data."
    )
    url = f'{TOWN_URL_BASE}/{fips}.html'
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"{place}, {state} Housing Market Analysis", "description": desc, "url": url,
    }, separators=(',', ':'))
    crumb = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE_URL + "/"},
            {"@type": "ListItem", "position": 2, "name": county, "item": url},
            {"@type": "ListItem", "position": 3, "name": place, "item": url},
        ],
    }, separators=(',', ':'))
    ga = (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA4_ID}"></script>'
        f'<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}'
        f'gtag("js",new Date());gtag("config","{GA4_ID}");</script>'
    ) if GA4_ID else ''
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{place}, {state} Town Housing Score — Civica Research</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{place}, {state} Town Housing Score — Civica">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:type" content="article">
<meta property="og:image" content="{SITE_URL}/og_image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE_URL}/og_image.png">
<link rel="canonical" href="{url}">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 30 30'><rect width='30' height='30' rx='7' fill='%230d2d52'/><rect x='6' y='16' width='4' height='9' rx='1.5' fill='white' opacity='.65'/><rect x='13' y='8' width='4' height='17' rx='1.5' fill='white'/><rect x='20' y='12' width='4' height='13' rx='1.5' fill='white' opacity='.8'/></svg>">
<script type="application/ld+json">{ld}</script>
<script type="application/ld+json">{crumb}</script>
{ga}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700;800&family=Roboto:wght@400;500;700&family=Inconsolata:wght@500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
{style}
<style>
.dc-chip {{ display:inline-flex; flex-wrap:wrap; gap:6px; align-items:center; font-size:11px; color:rgba(255,255,255,.6); background:rgba(0,0,0,.2); border:1px solid rgba(255,255,255,.12); border-radius:100px; padding:6px 14px; margin-top:14px; }}
.dc-chip b {{ color:#4ade80; font-weight:700; }}
.dc-chip i {{ color:#fbbf24; font-style:normal; font-weight:700; }}
.rank-line {{ font-size:14px; color:#fff; font-weight:600; margin-top:16px; }}
.rank-line span {{ color:#4ade80; }}
.howto {{ background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:0 18px; margin-top:16px; }}
.howto summary {{ cursor:pointer; font-weight:700; font-size:14px; color:#0d2d52; padding:16px 0; list-style:none; }}
.howto summary::-webkit-details-marker {{ display:none; }}
.howto summary::before {{ content:'▸ '; color:#1a7ff0; }}
.howto[open] summary::before {{ content:'▾ '; }}
.howto-body {{ font-size:13px; line-height:1.6; color:#475569; padding:0 0 18px; }}
.schools-soon {{ opacity:.7; }}
.tcap {{ font-size:11px; color:#94a3b8; margin-top:6px; }}
/* glance stat tiles */
.glance {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.gtile {{ background:#fff; border:1px solid #eef1f5; border-radius:14px; padding:14px 15px; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
.gt-top {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }}
.gt-icon {{ font-size:17px; }}
.gt-tag {{ font-size:10px; font-weight:800; letter-spacing:.02em; padding:3px 8px; border-radius:100px; }}
.gt-val {{ font-size:23px; font-weight:900; color:#0d2d52; line-height:1; }}
.gt-lbl {{ font-size:11px; color:#6e6e73; margin:5px 0 9px; }}
.gt-bar {{ height:6px; background:#eef2f7; border-radius:100px; overflow:hidden; }}
.gt-fill {{ height:100%; border-radius:100px; }}
/* dimension percentile rows */
.dimrow {{ display:flex; align-items:center; gap:12px; margin-bottom:13px; }}
.dimrow .dn {{ width:130px; flex-shrink:0; font-size:13px; font-weight:700; color:#0d2d52; }}
.dimrow .dt {{ flex:1; position:relative; height:14px; background:#eef2f7; border-radius:100px; }}
.dimrow .df {{ position:absolute; left:0; top:0; bottom:0; border-radius:100px; }}
.dimrow .dmed {{ position:absolute; left:50%; top:-3px; bottom:-3px; width:2px; background:#cbd5e1; }}
.dimrow .dv {{ width:118px; flex-shrink:0; text-align:right; font-size:12px; font-weight:700; color:#0d2d52; }}
.dimrow .dv small {{ color:#94a3b8; font-weight:600; }}
.radar-wrap {{ display:flex; gap:20px; align-items:center; flex-wrap:wrap; }}
.radar-wrap svg {{ flex-shrink:0; }}
.radar-side {{ flex:1; min-width:240px; }}
/* position / distribution bars */
.posbar {{ margin-bottom:16px; }}
.posbar .pl {{ display:flex; justify-content:space-between; font-size:12px; margin-bottom:6px; }}
.posbar .pl b {{ color:#0d2d52; }}
.postrack {{ position:relative; height:12px; border-radius:100px; background:linear-gradient(90deg,#dc2626,#f59e0b,#0b57c2,#16a34a); }}
.posmark {{ position:absolute; top:-4px; width:6px; height:20px; border-radius:3px; background:#0d2d52; box-shadow:0 0 0 2px #fff; transform:translateX(-3px); }}
.posends {{ display:flex; justify-content:space-between; font-size:10px; color:#94a3b8; margin-top:5px; }}
/* location map */
#locmap {{ height:240px; border-radius:12px; overflow:hidden; z-index:0; }}
/* peers */
.peer {{ display:flex; align-items:center; gap:12px; padding:9px 10px; border-radius:10px; text-decoration:none; }}
.peer:hover {{ background:#f6f8fb; }}
.peer.me {{ background:#eef6ff; border:1px solid #cfe3ff; }}
.peer-rk {{ width:26px; font-size:12px; font-weight:800; color:#c7c7cc; text-align:center; flex-shrink:0; }}
.peer-nm {{ flex:1; font-size:13px; font-weight:600; color:#0d2d52; }}
.peer-sc {{ width:30px; height:30px; border-radius:50%; color:#fff; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; flex-shrink:0; }}
@media (max-width:640px) {{ .glance {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>'''


def build_nav():
    return '''<nav class="nav">
  <a class="logo" href="../../index.html">
    <svg width="28" height="28" viewBox="0 0 30 30" fill="none">
      <rect width="30" height="30" rx="7" fill="#0d2d52"/>
      <rect x="6" y="16" width="4" height="9" rx="1.5" fill="white" opacity="0.65"/>
      <rect x="13" y="8" width="4" height="17" rx="1.5" fill="white"/>
      <rect x="20" y="12" width="4" height="13" rx="1.5" fill="white" opacity="0.8"/>
    </svg>
    <span class="logo-text">civi<em>ca</em></span>
  </a>
  <div class="nav-right">
    <span class="nav-tag" style="margin-right:8px;">Town Report</span>
    <a class="nav-back" href="../../index.html">← All Towns</a>
  </div>
</nav>'''


def build_hero(row, place, state, county):
    score = row['civica_score']
    pop = int(row['POPESTIMATE2025'])
    offset = ring_offset(score)
    label = row['market_label']
    pill = LABEL_PILL.get(label, 'vb-frontier')
    top_pct = max(1, round(row['national_rank'] / row['_n_national'] * 100))
    rk, tot = int(row['rank_in_county']), int(row['towns_in_county'])
    mcd = int(row.get('is_mcd', 0) or 0)
    mcd_tag = (' &nbsp;·&nbsp; <span style="font-size:11px;font-weight:700;color:#5aa8ff;'
               'border:1px solid rgba(255,255,255,.28);border-radius:100px;padding:2px 9px;" '
               'title="Governed as a county subdivision (Minor Civil Division), not a Census incorporated place.">'
               'Town government (MCD)</span>') if mcd else ''

    return f'''<div class="hero">
  <div class="hero-id">
    <div class="hero-eyebrow">Town Report · 2026</div>
    <h1>{place}, {state}</h1>
    <div class="hero-sub">{county} · Pop. {pop:,}{mcd_tag}</div>
    <div class="rank-line">Ranks <span>#{rk} of {tot}</span> towns in {county} — {rank_phrase(rk, tot)}</div>
    <div class="dc-chip">Town-level: <b>crime · income · growth · schools</b> &nbsp;·&nbsp; County-level: <i>economy · appreciation · climate</i></div>
  </div>
  <div class="hero-score">
    <div class="score-hero">
      <div class="sh-ring">
        <svg viewBox="0 0 110 110">
          <circle cx="55" cy="55" r="46" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="9"/>
          <circle cx="55" cy="55" r="46" fill="none" stroke="#5aa8ff" stroke-width="9"
            stroke-dasharray="289.02" stroke-dashoffset="{offset}" stroke-linecap="round"/>
        </svg>
        <div class="sh-num">{score:.0f}</div>
        <div class="sh-denom">/100</div>
      </div>
      <div class="sh-grade">Top {top_pct}% · #{int(row['national_rank']):,} of {int(row['_n_national']):,}</div>
    </div>
    <div class="verdict-badge">
      <div class="vb-label">Verdict</div>
      <div class="vb-pill {pill}">{label}</div>
      <div class="vb-score">Civica Score {score:.1f}</div>
    </div>
  </div>
</div>'''


DIM_PALETTE = ['#1a7ff0', '#16a34a', '#8b5cf6', '#f59e0b', '#0d9488']


def pctl_tag(p):
    """Return (label, color) describing a national percentile 0-100."""
    if p >= 75:
        return (f'TOP {max(1, round(100 - p))}%', '#16a34a')   # green  (Strong Buy)
    if p >= 50:
        return ('ABOVE AVG', '#0b57c2')                        # blue   (Buy)
    if p >= 25:
        return ('BELOW AVG', '#b45309')                        # amber  (Hold)
    return (f'BOTTOM {max(1, round(p))}%', '#b91c1c')          # red    (Caution)


def tag_bg(color):
    return {'#16a34a': '#dcfce7', '#0b57c2': '#dbeafe',
            '#b45309': '#fef3c7', '#b91c1c': '#fee2e2'}.get(color, '#eef2f7')


def build_radar(vals, labels):
    """N-axis radar (pentagon for 5 dims), first axis at top, going clockwise."""
    import math
    cx = cy = 100.0
    R = 74.0
    n = len(vals)
    vals = [max(0.0, min(100.0, v)) for v in vals]
    ang = [-math.pi / 2 + i * 2 * math.pi / n for i in range(n)]
    pts = [(cx + R * vals[i] / 100 * math.cos(ang[i]),
            cy + R * vals[i] / 100 * math.sin(ang[i])) for i in range(n)]
    poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    rings = ''
    for frac in (0.25, 0.5, 0.75, 1.0):
        rp = ' '.join(f'{cx+R*frac*math.cos(a):.1f},{cy+R*frac*math.sin(a):.1f}' for a in ang)
        rings += f'<polygon points="{rp}" fill="none" stroke="#e5eaf1" stroke-width="1"/>'
    axes = ''.join(f'<line x1="{cx}" y1="{cy}" x2="{cx+R*math.cos(a):.1f}" y2="{cy+R*math.sin(a):.1f}" stroke="#e5eaf1"/>' for a in ang)
    labs = ''
    for i, lab in enumerate(labels):
        lx, ly = cx + (R + 13) * math.cos(ang[i]), cy + (R + 13) * math.sin(ang[i])
        anch = 'middle' if abs(math.cos(ang[i])) < 0.3 else ('start' if math.cos(ang[i]) > 0 else 'end')
        labs += f'<text x="{lx:.1f}" y="{ly+3:.1f}" text-anchor="{anch}" font-size="9" fill="#6e6e73" font-weight="700">{lab}</text>'
    dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#0d2d52"/>' for x, y in pts)
    return (f'<svg width="244" height="200" viewBox="-22 0 244 200">{rings}{axes}'
            f'<polygon points="{poly}" fill="rgba(26,127,240,.20)" stroke="#1a7ff0" stroke-width="2"/>'
            f'{dots}{labs}</svg>')


def build_glance(row):
    rb = row['rent_burden'] * 100
    g5 = row['town_growth_5yr'] * 100
    tiles = [
        ('🎯', f"{row['civica_score']:.0f}", 'Civica Score (0–100)', row['pctl_score']),
        ('💵', money(row['town_income']), 'Town income / return', row['pctl_income']),
        ('🏠', f'{rb:.0f}%', 'Rent burden', row['pctl_rentburden']),
        ('🛡️', f"{row['violent_per100k']:.0f}", 'Violent crime / 100k', row['pctl_crime']),
        ('📈', f'{g5:+.1f}%', 'Population growth, 5yr', row['pctl_growth']),
        ('🎓', f"{row['school_score']:.0f}", 'School proficiency (state %ile)', row['school_score']),
    ]
    cells = ''
    for icon, val, lbl, p in tiles:
        tag, col = pctl_tag(p)
        cells += f'''
      <div class="gtile">
        <div class="gt-top"><span class="gt-icon">{icon}</span><span class="gt-tag" style="color:{col};background:{tag_bg(col)};">{tag}</span></div>
        <div class="gt-val">{val}</div>
        <div class="gt-lbl">{lbl}</div>
        <div class="gt-bar"><div class="gt-fill" style="width:{max(3,p):.0f}%;background:{col};"></div></div>
      </div>'''
    return f'''<div class="card">
    <div class="card-title" style="margin-bottom:8px;"><span class="ct-icon">⚡</span> At a Glance</div>
    <div style="font-size:13px;color:var(--subtext);line-height:1.6;margin-bottom:18px;">The headline numbers for this town. The tag and bar on each show how it ranks against all 12,192 US towns &mdash; a fuller, greener bar means it ranks higher. (Schools are ranked within their own state.)</div>
    <div class="glance">{cells}
    </div>
  </div>'''


def build_dimension_card(row):
    radar = build_radar([row[c] / DIM_MAX[c] * 100 for c, _, _ in DIM_NAMES],
                        ['Afford.', 'Econ.', 'Safety', 'Growth', 'Schools'])
    rows = ''
    pctl_keys = ['pctl_dim1', 'pctl_dim2', 'pctl_dim3', 'pctl_dim4', 'pctl_dim5']
    for (col, name, icon), color, pk in zip(DIM_NAMES, DIM_PALETTE, pctl_keys):
        val = row[col] / DIM_MAX[col] * 100
        p = row[pk]
        tag, tcol = pctl_tag(p)
        rows += f'''
      <div class="dimrow">
        <div class="dn">{icon} {name}</div>
        <div class="dt"><div class="dmed"></div><div class="df" style="width:{val:.0f}%;background:{color};"></div></div>
        <div class="dv">{val:.0f}<small>/100</small> · <span style="color:{tcol};">{tag}</span></div>
      </div>'''
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">📊</span> Five-Dimension Breakdown</div>
    <div class="radar-wrap">
      {radar}
      <div class="radar-side">{rows}
      </div>
    </div>
    <div class="tcap">Dimension caps: Affordability 25 · Economy 24 · Safety &amp; Place 22 · Growth 15 · Schools 14 pts.
      The dashed line marks the national median. Town-resolved metrics (crime, income, growth, scale, schools)
      drive ~58% of the score; the rest is inherited from {row['county_name']}.</div>
  </div>'''


def build_verdict_card(row, place):
    label = row['market_label']
    sig_cls, icon = LABEL_SIG.get(label, ('sig-yellow', '👀'))
    dims = {name: row[col] / DIM_MAX[col] * 100 for col, name, _ in DIM_NAMES}
    top = max(dims, key=dims.get)
    bot = min(dims, key=dims.get)
    verdict = {
        'Strong Buy': f'{place} is a <strong>Strong Buy</strong>. Its strongest dimension is '
                      f'{top} ({dims[top]:.0f}/100), and town-level fundamentals lead both its '
                      f'county and most of the nation.',
        'Buy': f'{place} is a <strong>Buy</strong>. {top} ({dims[top]:.0f}/100) anchors solid '
               f'fundamentals; watch {bot} ({dims[bot]:.0f}/100) as the softer dimension.',
        'Hold': f'{place} is a <strong>Hold</strong> — a balanced, middle-of-the-pack market. '
                f'{top} ({dims[top]:.0f}/100) is the bright spot; {bot} ({dims[bot]:.0f}/100) lags.',
        'Caution': f'{place} warrants <strong>Caution</strong>. {bot} ({dims[bot]:.0f}/100) and '
                   f'other fundamentals trail national peers; even {top} ({dims[top]:.0f}/100) is '
                   f'only middling.',
    }.get(label, '')
    return f'''<div class="signal {sig_cls}" style="margin:0 0 16px;">
    <div class="icon">{icon}</div>
    <div class="body" style="font-size:14px;"><strong>Civica Verdict:</strong> {verdict}</div>
  </div>'''


def build_fundamentals_card(row):
    crime_note = (' <span class="tcap" style="display:inline;">(county/RUCC-tier rate — no '
                  'municipal agency matched)</span>') if int(row['crime_imputed']) == 1 else ''
    inc_note = (' <span class="tcap" style="display:inline;">(county fallback — no ZIP '
                'allocation)</span>') if int(row['income_imputed']) == 1 else ''
    sch_note = (' <span class="tcap" style="display:inline;">(county/RUCC-tier estimate)</span>'
                ) if int(row['schools_imputed']) == 1 else ''
    rb = row['rent_burden'] * 100
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">📋</span> The Numbers</div>
    <div class="tcap" style="margin:-10px 0 16px;">The underlying federal figures behind the score.</div>
    <div class="numgrid">
      <div class="sb"><div class="sb-val">{money(row['town_income'])}</div><div class="sb-lbl">Town income / return (IRS ZIP){inc_note}</div></div>
      <div class="sb"><div class="sb-val">{rb:.0f}%</div><div class="sb-lbl">Rent burden (2BR FMR ÷ income)</div></div>
      <div class="sb"><div class="sb-val">{row['hpi_3yr_avg']:+.1f}%</div><div class="sb-lbl">3yr appreciation (FHFA)</div></div>
      <div class="sb"><div class="sb-val">{money(row['avg_annual_wage'])}</div><div class="sb-lbl">Avg annual wage (BLS QCEW)</div></div>
      <div class="sb"><div class="sb-val">{row['school_score']:.0f}</div><div class="sb-lbl">School proficiency, {row['state_abbr']} %ile (EDFacts){sch_note}</div></div>
      <div class="sb"><div class="sb-val">{row['violent_per100k']:.0f}</div><div class="sb-lbl">Violent crime / 100k (NIBRS){crime_note}</div></div>
      <div class="sb"><div class="sb-val">{row['property_per100k']:.0f}</div><div class="sb-lbl">Property crime / 100k (NIBRS)</div></div>
      <div class="sb"><div class="sb-val">{row['town_growth_5yr']*100:+.1f}%</div><div class="sb-lbl">Town pop growth, 5yr (Census)</div></div>
      <div class="sb"><div class="sb-val">{row['town_income_growth']*100:+.1f}%</div><div class="sb-lbl">Town income growth (IRS ZIP)</div></div>
      <div class="sb"><div class="sb-val">{row['RNETMIG2023']:+.1f}</div><div class="sb-lbl">County net migration / 1k (Census)</div></div>
    </div>
  </div>'''


def build_position_card(row):
    n = int(row['_n_national'])
    nrank = int(row['national_rank'])
    p_nat = row['pctl_score']
    top_nat = max(1, round(100 - p_nat))
    rk, tot = int(row['rank_in_county']), int(row['towns_in_county'])
    p_cty = 100.0 if tot <= 1 else (tot - rk) / (tot - 1) * 100
    cty_line = ('the only scored town in its county' if tot <= 1
                else f'ahead of {tot - rk} of {tot - 1} peer town' + ('s' if (tot - 1) != 1 else ''))
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">🧭</span> How {str(row['place_name'])} Compares</div>
    <div class="posbar">
      <div class="pl"><span>Among all US towns</span><span><b>#{nrank:,}</b> of {n:,} · top {top_nat}%</span></div>
      <div class="postrack"><div class="posmark" style="left:{p_nat:.0f}%;"></div></div>
      <div class="posends"><span>Caution</span><span>National median</span><span>Strong Buy</span></div>
    </div>
    <div class="posbar" style="margin-bottom:0;">
      <div class="pl"><span>Within {str(row['county_name'])}</span><span><b>#{rk}</b> of {tot} — {cty_line}</span></div>
      <div class="postrack"><div class="posmark" style="left:{p_cty:.0f}%;"></div></div>
      <div class="posends"><span>Lowest in county</span><span></span><span>Highest in county</span></div>
    </div>
  </div>'''


def build_location_card(row, lat, lon):
    if lat is None or lon is None:
        return ''
    col = {'Strong Buy': '#16a34a', 'Buy': '#0b57c2',
           'Hold': '#f59e0b', 'Caution': '#dc2626'}.get(row['market_label'], '#0b57c2')
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">📍</span> Where It Is</div>
    <div id="locmap" data-lat="{lat}" data-lon="{lon}" data-col="{col}"></div>
    <div class="tcap">{str(row['place_name'])}, {str(row['state_abbr'])} · {row['POPESTIMATE2025']:,} residents.
      Scroll to zoom; drag to pan.</div>
  </div>'''


def build_peers_card(sib, fips, place):
    if sib is None or len(sib) <= 1:
        return ''
    sib = sib.sort_values('rank_in_county').head(8)
    rows = ''
    for _, r in sib.iterrows():
        f = str(r['fips']).zfill(7)
        me = ' me' if f == fips else ''
        col = {'Strong Buy': '#16a34a', 'Buy': '#0b57c2',
               'Hold': '#f59e0b', 'Caution': '#dc2626'}.get(r['market_label'], '#9ca3af')
        href = f'{f}.html'
        rows += (f'<a class="peer{me}" href="{href}">'
                 f'<span class="peer-rk">#{int(r["rank_in_county"])}</span>'
                 f'<span class="peer-nm">{r["place_name"]}</span>'
                 f'<span class="peer-sc" style="background:{col};">{r["civica_score"]:.0f}</span></a>')
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">🏘️</span> Towns in the Same County</div>
    {rows}
    <div class="tcap">Ranked by town-resolved fundamentals within the county. {place} is highlighted.</div>
  </div>'''


def build_schools_card(row):
    p = row['pctl_schools']
    tag, col = pctl_tag(p)
    ss = row['school_score']
    dim5 = row['dim5'] / DIM_MAX['dim5'] * 100
    imp = int(row['schools_imputed']) if 'schools_imputed' in row else 0
    note = (' <span class="tcap" style="display:inline;">(county/RUCC-tier estimate — too few '
            'schools matched here)</span>') if imp == 1 else ''
    return f'''<div class="card">
    <div class="card-title"><span class="ct-icon">🎓</span> Schools</div>
    <div class="g3">
      <div class="sb"><div class="sb-val">{ss:.0f}</div><div class="sb-lbl">Proficiency percentile within {row['state_abbr']}{note}</div></div>
      <div class="sb"><div class="sb-val">{dim5:.0f}<span style="font-size:13px;color:#94a3b8;">/100</span></div><div class="sb-lbl">Schools dimension score</div></div>
      <div class="sb"><div class="sb-val" style="color:{col};">{tag}</div><div class="sb-lbl">vs. all US towns</div></div>
    </div>
    <div class="tcap">Public-school math + reading proficiency (US Dept. of Education EDFacts, SY2017–18,
      accessed via the Urban Institute mirror), ranked <strong>within the state</strong> because state
      tests aren't comparable across states, then enrollment-weighted to the town. Schools are 14 of
      100 points — one of five dimensions, not the whole score.</div>
  </div>'''


def build_howto(row):
    return f'''<details class="howto">
    <summary>How we scored this</summary>
    <div class="howto-body">
      The Civica Score blends five dimensions into a single 0–100 number, percentile-ranked
      across {int(row['_n_national']):,} US towns. Roughly <strong>58% is town-resolved</strong>
      — crime (FBI NIBRS, mapped from reporting agencies to places), income (IRS SOI ZIP-code AGI
      allocated to places), population growth/scale (Census sub-county estimates), and schools
      (US Dept. of Ed EDFacts proficiency, ranked within state). The other
      <strong>~42% is inherited from {row['county_name']}</strong> — wages, sector mix, home-price
      appreciation, climate risk, migration, and permits — because those genuinely operate at a
      regional scale and faking them per-town would be dishonest.
      <br><br>
      <strong>Honest caveats:</strong> town income is a ZIP→place address-share approximation, not
      a survey median. Crime is mapped via agency name; county sheriffs cover unincorporated area,
      and towns with no matched reporting agency inherit the county/RUCC-tier rate (flagged, never
      penalized). There is no home-value level — affordability is rent-vs-income plus appreciation
      quality. The universe is incorporated places ≥ 1,000 people (CDPs excluded for now).
      Every input is now a <strong>federal</strong> source.
    </div>
  </details>'''


FOOTER = '''<div class="footer">
  100% federal data: FBI NIBRS 2024 · IRS SOI (ZIP AGI + migration) · Census Population Estimates
  (sub-county) · BLS QCEW · BEA · FHFA HPI · HUD FMR FY2026 · Census CBP/BPS · FEMA NFIP ·
  NOAA Storm Events · USFS Wildfire · USDA RUCC.<br>
  ~42% of each town's score is county-inherited (wages, appreciation, climate, migration, permits) —
  regional by nature. Town income is a ZIP→place approximation; crime→town mapping is approximate.
  Civica scores are informational only — not financial, investment, or real estate advice.<br><br>
  <strong>civi<em style="font-style:normal;color:#1a7ff0;">ca</em></strong> — research-grade town intelligence for homebuyers
</div>'''


LOC_SCRIPT = '''<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){
  var el=document.getElementById('locmap');
  if(!el||typeof L==='undefined')return;
  var lat=parseFloat(el.dataset.lat),lon=parseFloat(el.dataset.lon),col=el.dataset.col;
  var m=L.map('locmap',{zoomControl:true,scrollWheelZoom:false,attributionControl:false}).setView([lat,lon],11);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',{subdomains:'abcd',maxZoom:17}).addTo(m);
  L.circleMarker([lat,lon],{radius:9,color:'#fff',weight:2,fillColor:col,fillOpacity:.9}).addTo(m);
})();
</script>'''


def generate_page(row, style, geo, siblings):
    place = str(row['place_name'])
    state = str(row['state_abbr'])
    county = str(row['county_name'])
    fips = str(row['fips']).zfill(7)
    score = row['civica_score']
    label = row['market_label']
    lat, lon = geo.get(fips, (None, None))
    return f'''{build_head(row, place, state, county, score, label, fips, style)}
<body>
{build_nav()}
<div class="page">
  {build_hero(row, place, state, county)}
  {build_verdict_card(row, place)}
  {build_glance(row)}
  {build_dimension_card(row)}
  {build_position_card(row)}
  {build_fundamentals_card(row)}
  {build_location_card(row, lat, lon)}
  {build_peers_card(siblings, fips, place)}
  {build_howto(row)}
  {build_schools_card(row)}
  <div style="text-align:center;margin:4px 0 8px;">
    <a href="../../compare.html?c={fips}" style="display:inline-block;padding:12px 28px;background:#0b57c2;color:#fff;border-radius:10px;font-weight:700;text-decoration:none;font-size:14px;">⚖️ Compare {place} with another town →</a>
  </div>
  {FOOTER}
</div>
{LOC_SCRIPT}
</body>
</html>'''


LABEL_LB = {'Strong Buy': 'lb-sbuy', 'Buy': 'lb-buy', 'Hold': 'lb-watch', 'Caution': 'lb-caut'}


def build_state_page(state, towns):
    """Ranked list of every scored town in one state (output/states/{XX}.html)."""
    name = STATE_NAMES.get(state, state)
    towns = towns.sort_values('civica_score', ascending=False).reset_index(drop=True)
    n = len(towns)
    scores = towns['civica_score'].tolist()
    median = scores[len(scores) // 2]
    top = towns.iloc[0]
    rows = ''
    for i, r in towns.iterrows():
        fips = str(r['fips']).zfill(7)
        sc = r['civica_score']
        sc_bg = '#16a34a' if sc >= 62 else ('#0b57c2' if sc >= 52 else ('#f59e0b' if sc >= 44 else '#dc2626'))
        g = r['town_growth_5yr'] * 100
        gcol = '#16a34a' if g >= 0 else '#dc2626'
        rows += f'''<tr onclick="window.location='../towns/{fips}.html'" style="cursor:pointer;">
          <td><span style="font-size:13px;font-weight:700;color:#c7c7cc;">{i+1}</span></td>
          <td><div style="font-size:14px;font-weight:700;color:#0d2d52;">{r['place_name']}</div>
              <div style="font-size:11px;color:#98989d;">{r['county_name']} · #{int(r['rank_in_county'])} in county</div></td>
          <td><div style="display:flex;align-items:center;gap:8px;">
              <div style="width:32px;height:32px;border-radius:50%;background:{sc_bg};color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800;">{sc:.0f}</div>
              <span style="font-size:14px;font-weight:800;color:#0d2d52;">{sc:.1f}</span></div></td>
          <td><span class="lbadge {LABEL_LB.get(r['market_label'], 'lb-watch')}">{r['market_label']}</span></td>
          <td style="text-align:right;font-weight:600;">{money(r['town_income'])}</td>
          <td style="text-align:right;font-weight:600;color:{gcol};">{g:+.1f}%</td>
          <td class="col-crime" style="text-align:right;font-weight:600;">{r['violent_per100k']:.0f}</td>
        </tr>'''
    desc = (f'All {n} scored towns in {name}, ranked by Civica Score. Best towns to buy a '
            f'home in {name} — town-level crime, income, and growth on 100% federal data.')
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Best Towns to Buy a Home in {name} — Civica Research</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="Best Towns in {name} — Civica"><meta property="og:description" content="{desc}">
<meta property="og:image" content="{SITE_URL}/og_image.png">
<link rel="canonical" href="{STATE_URL_BASE}/{state}.html">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 30 30'><rect width='30' height='30' rx='7' fill='%230d2d52'/><rect x='6' y='16' width='4' height='9' rx='1.5' fill='white' opacity='.65'/><rect x='13' y='8' width='4' height='17' rx='1.5' fill='white'/><rect x='20' y='12' width='4' height='13' rx='1.5' fill='white' opacity='.8'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700;800&family=Roboto:wght@400;500;700&family=Inconsolata:wght@500;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Roboto',-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#ffffff;color:#111827;}}
h1,h2{{font-family:'Poppins',sans-serif;letter-spacing:-.01em;}}
.nav{{background:rgba(255,255,255,.9);backdrop-filter:blur(20px);border-bottom:1px solid rgba(0,0,0,.08);height:56px;padding:0 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:300;}}
.logo{{display:flex;align-items:center;gap:9px;text-decoration:none;}}
.logo-text{{font-size:18px;font-weight:800;color:#0d2d52;}}.logo-text em{{font-style:normal;color:#1a7ff0;}}
.nav a.back{{font-size:13px;color:#6e6e73;text-decoration:none;font-weight:500;}}
.hero{{background:linear-gradient(160deg,#060f1e,#091f3a 45%,#0d2d52);padding:44px 20px 52px;}}
.hero-in{{max-width:960px;margin:0 auto;}}
.hero h1{{font-size:36px;font-weight:900;color:#fff;margin:8px 0 16px;}}
.eyebrow{{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.4);}}
.stat-row{{display:flex;gap:24px;flex-wrap:wrap;}}
.stat-v{{font-size:26px;font-weight:900;color:#fff;}}.stat-l{{font-size:11px;color:rgba(255,255,255,.5);}}
.page{{max-width:960px;margin:0 auto;padding:24px 16px 48px;}}
.card{{background:#fff;border-radius:16px;border:1px solid rgba(0,0,0,.08);padding:18px;}}
.card-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#98989d;margin-bottom:14px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{text-align:left;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#98989d;padding:0 12px 12px;border-bottom:2px solid rgba(0,0,0,.08);white-space:nowrap;}}
th.r{{text-align:right;}}
td{{padding:11px 12px;border-bottom:1px solid rgba(0,0,0,.06);}}
tbody tr:hover td{{background:#f5f5f7;}}
.lbadge{{font-size:10px;font-weight:700;padding:3px 9px;border-radius:100px;white-space:nowrap;}}
.lb-sbuy{{background:#dcfce7;color:#15803d;}}.lb-buy{{background:#dbeafe;color:#1d4ed8;}}
.lb-watch{{background:#fef3c7;color:#92400e;}}.lb-caut{{background:#fee2e2;color:#b91c1c;}}
.footer{{font-size:11px;color:#98989d;text-align:center;padding:24px 16px;line-height:1.7;}}
@media(max-width:640px){{.hero h1{{font-size:26px;}}th.col-crime,td.col-crime{{display:none;}}}}
</style></head><body>
<nav class="nav">
  <a class="logo" href="../../index.html">
    <svg width="28" height="28" viewBox="0 0 30 30" fill="none"><rect width="30" height="30" rx="7" fill="#0d2d52"/><rect x="6" y="16" width="4" height="9" rx="1.5" fill="white" opacity="0.65"/><rect x="13" y="8" width="4" height="17" rx="1.5" fill="white"/><rect x="20" y="12" width="4" height="13" rx="1.5" fill="white" opacity="0.8"/></svg>
    <span class="logo-text">civi<em>ca</em></span>
  </a>
  <a class="back" href="../../index.html">← All Towns</a>
</nav>
<div class="hero"><div class="hero-in">
  <div class="eyebrow">State Town Report · 2026</div>
  <h1>{name}</h1>
  <div class="stat-row">
    <div><div class="stat-v">{n}</div><div class="stat-l">Towns Scored</div></div>
    <div><div class="stat-v" style="color:#1a7ff0;">{median:.1f}</div><div class="stat-l">Median Score</div></div>
    <div><div class="stat-v" style="color:#4ade80;">{top['civica_score']:.1f}</div><div class="stat-l">Top Town Score</div></div>
    <div><div class="stat-v" style="font-size:16px;">{top['place_name']}</div><div class="stat-l">Top Ranked Town</div></div>
  </div>
</div></div>
<div class="page"><div class="card">
  <div class="card-title">📊 All {name} Towns — Ranked by Civica Score</div>
  <div style="overflow-x:auto;"><table>
    <thead><tr><th style="width:36px;">#</th><th>Town</th><th>Score</th><th>Label</th>
      <th class="r">Town Income</th><th class="r">Growth 5yr</th><th class="r col-crime">Violent/100k</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</div>
<div style="margin-top:12px;text-align:center;">
  <a href="../../leaderboard.html" style="display:inline-block;padding:12px 28px;background:#1a7ff0;color:#fff;border-radius:10px;font-weight:700;text-decoration:none;font-size:14px;">National Town Leaderboard →</a>
</div>
<div class="footer">100% federal data · Civica scores are informational only, not financial or real estate advice. &copy; 2026 Civica.</div>
</div></body></html>'''


# ── Index + progress ledger ──────────────────────────────────────────────────────

def load_index():
    if os.path.exists(OUT_INDEX):
        try:
            return {r['fips']: r for r in json.load(open(OUT_INDEX, encoding='utf-8'))}
        except Exception:
            return {}
    return {}


def write_index(index_map):
    records = sorted(index_map.values(), key=lambda x: x['score'], reverse=True)
    json.dump(records, open(OUT_INDEX, 'w', encoding='utf-8'), separators=(',', ':'))
    return len(records)


def load_progress(all_states):
    if os.path.exists(PROGRESS):
        try:
            p = json.load(open(PROGRESS, encoding='utf-8'))
            p.setdefault('done', [])
            p['all'] = all_states
            return p
        except Exception:
            pass
    return {'done': [], 'all': all_states}


def write_progress(p):
    json.dump(p, open(PROGRESS, 'w', encoding='utf-8'), indent=2)


def write_sitemap(index_map):
    """Regenerate sitemap.xml from town URLs + the core static pages."""
    static = ['', 'map.html', 'leaderboard.html', 'compare.html', 'methodology.html',
              'disclaimer.html', 'privacy.html', 'terms.html']
    urls = [f'  <url><loc>{SITE_URL}/{p}</loc></url>' for p in static]
    states = sorted({rec['state'] for rec in index_map.values()})
    for st in states:
        urls.append(f'  <url><loc>{STATE_URL_BASE}/{st}.html</loc></url>')
    for fips in sorted(index_map):
        urls.append(f'  <url><loc>{TOWN_URL_BASE}/{fips}.html</loc></url>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + '\n'.join(urls) + '\n</urlset>')
    with open(os.path.join(SITE, 'sitemap.xml'), 'w', encoding='utf-8') as f:
        f.write(xml)
    return len(urls)


# ── Main ─────────────────────────────────────────────────────────────────────────

def generate_state(state_df, style, index_map, geo, sib_cols):
    """Generate every town page for one state; update index_map in place. Returns count."""
    sib_by_county = {cf: g[sib_cols] for cf, g in state_df.groupby('primary_county_fips')}
    for _, row in state_df.iterrows():
        fips = str(row['fips']).zfill(7)
        siblings = sib_by_county.get(row['primary_county_fips'])
        html = generate_page(row, style, geo, siblings)
        with open(os.path.join(OUT_DIR, f'{fips}.html'), 'w', encoding='utf-8') as f:
            f.write(html)
        index_map[fips] = {
            'fips': fips, 'name': str(row['place_name']), 'state': str(row['state_abbr']),
            'county': str(row['county_name']),
            'county_fips': str(row['primary_county_fips']).zfill(5),
            'score': round(float(row['civica_score']), 2),
            'label': str(row['market_label']),
            'rank': int(row['national_rank']),
            'rank_in_county': int(row['rank_in_county']),
            'towns_in_county': int(row['towns_in_county']),
            'pop': int(row['POPESTIMATE2025']),
            'dim1': round(float(row['dim1']), 2), 'dim2': round(float(row['dim2']), 2),
            'dim3': round(float(row['dim3']), 2), 'dim4': round(float(row['dim4']), 2),
            'dim5': round(float(row['dim5']), 2),
            'town_income': round(float(row['town_income']), 0),
            'rent_burden': round(float(row['rent_burden']), 4),
            'hpi_3yr_avg': round(float(row['hpi_3yr_avg']), 2),
            'avg_annual_wage': round(float(row['avg_annual_wage']), 0),
            'violent_per100k': round(float(row['violent_per100k']), 1),
            'town_growth_5yr': round(float(row['town_growth_5yr']), 4),
        }
    return len(state_df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', help='2-letter state to generate (default: all states)')
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    df = pd.read_csv(SCORES, dtype={'fips': str, 'primary_county_fips': str})
    df['fips'] = df['fips'].str.zfill(7)
    df['_n_national'] = len(df)

    # National percentiles (0-100) for context bars; lower-is-better metrics inverted.
    df['pctl_score'] = df['civica_score'].rank(pct=True) * 100
    for i in (1, 2, 3, 4, 5):
        df[f'pctl_dim{i}'] = df[f'dim{i}'].rank(pct=True) * 100
    df['pctl_income'] = df['town_income'].rank(pct=True) * 100
    df['pctl_wage'] = df['avg_annual_wage'].rank(pct=True) * 100
    df['pctl_growth'] = df['town_growth_5yr'].rank(pct=True) * 100
    df['pctl_rentburden'] = (1 - df['rent_burden'].rank(pct=True)) * 100
    df['pctl_crime'] = (1 - df['violent_per100k'].rank(pct=True)) * 100
    df['pctl_appr'] = (1 - (df['hpi_3yr_avg'] - 5).abs().rank(pct=True)) * 100
    df['pctl_schools'] = df['school_score'].rank(pct=True) * 100

    # Town coordinates for the per-page location map (if built).
    geo = {}
    geo_path = os.path.join(SITE, 'output', 'towns_geo.json')
    if os.path.exists(geo_path):
        for t in json.load(open(geo_path, encoding='utf-8')):
            geo[t['f']] = (t['lat'], t['lon'])

    sib_cols = ['fips', 'place_name', 'civica_score', 'market_label', 'rank_in_county']

    with open(TEMPLATE, encoding='utf-8') as f:
        style = extract_style(f.read())

    all_states = sorted(df['state_abbr'].dropna().unique().tolist())
    index_map = load_index()
    progress = load_progress(all_states)

    targets = [args.state.upper()] if args.state else all_states

    for st in targets:
        sdf = df[df['state_abbr'] == st]
        if sdf.empty:
            print(f'  {st}: no towns, skipping')
            continue
        n = generate_state(sdf, style, index_map, geo, sib_cols)
        with open(os.path.join(STATE_DIR, f'{st}.html'), 'w', encoding='utf-8') as f:
            f.write(build_state_page(st, sdf))
        if st not in progress['done']:
            progress['done'].append(st)
        write_progress(progress)
        write_index(index_map)
        print(f'  {st} ({STATE_NAMES.get(st, st)}): {n} towns -> output/towns/ + state page')

    total = write_index(index_map)
    remaining = [s for s in all_states if s not in progress['done']]
    # Regenerate the sitemap once everything is built (idempotent).
    if not remaining:
        n_urls = write_sitemap(index_map)
        print(f'  sitemap.xml regenerated: {n_urls:,} URLs')
    print(f'\nDone. town_index.json: {total:,} towns. '
          f'States done: {len(progress["done"])}/{len(all_states)}. '
          f'Remaining: {len(remaining)}')


if __name__ == '__main__':
    main()
