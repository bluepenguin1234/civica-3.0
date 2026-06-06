# Civica Towns

Research-grade housing intelligence for **every US town**. Civica scores **11,306
towns** (incorporated places, plus New England governing towns/MCDs, population ≥ 1,000) on a
single **0–100 Civica Score** built from five dimensions, using **100% federal government data** (no survey data). Each town is also
ranked inside its own county.

No ads. No agents. No proprietary feeds.

## The model

| Dimension | Pts | What it measures |
|---|--:|---|
| **Affordability** | 25 | Rent burden (HUD 2BR rent ÷ ZIP-allocated town income) + FHFA appreciation quality |
| **Economy** | 24 | BLS wages, sector quality, employment diversity (HHI), town income growth |
| **Safety & Place** | 22 | FBI NIBRS violent + property crime, town scale, amenity density, physical risk |
| **Growth** | 15 | Town population growth & momentum, county migration, in-mover income, permits |
| **Schools** | 14 | Public-school proficiency (US Dept. of Ed EDFacts), ranked within state |

**~58% of every score is town-resolved** (crime, income, growth, scale, schools); the other ~42% is
inherited from the parent county (wages, appreciation, climate, migration, permits), which
genuinely operate at a regional scale.

**Verdicts:** Strong Buy ≥ 62 · Buy ≥ 52 · Hold ≥ 44 · Caution < 44.

## Run it locally

The raw federal datasets (~7 GB) are **not** in the repo (`civica_data/` is gitignored). The
scoring engine (`town_scoring_engine.py`) needs them; everything else (page generation,
validation) does not. Two ways to provide them:

- **Use the existing copy (no re-download).** A Windows directory junction points
  `civica_data/` at the maintained copy in the original dev folder:
  ```
  mklink /J "C:\Users\Brian\Desktop\Civica_Towns\civica_data" "C:\Users\Brian\Desktop\Civica Harvard Model\civica_data"
  ```
- **Re-fetch from scratch.** `download_town_data.py` pulls the 3 town-specific datasets
  (Census sub-est, IRS ZIP AGI, ZCTA→place crosswalk); `civica_data_downloader_v5.py`
  (and `v4`) pull the county datasets (QCEW, BEA, FHFA, CBP, BPS, IRS migration, NFIP, NOAA,
  USFS, RUCC, HUD FMR, Census pop). NIBRS (the 5.8 GB FBI file) is a manual download.

All build scripts live in **`pipeline/`** and resolve paths via `__file__`, so they work from
any working directory. The built website is written into **`docs/`** (the GitHub Pages source).

```bash
cd pipeline
python download_town_data.py     # 3 new town datasets (Census sub-est, IRS ZIP AGI, ZCTA→place)
python town_scoring_engine.py    # -> town_scores.csv  (needs ../civica_data/)
python build_town_geo.py         # -> ../docs/output/towns_geo.json  (town coordinates, map)
python town_generator.py         # -> ../docs/output/{towns,states}/, town_index.json, sitemap.xml
python validate_town.py          # the gate — must be green
cd ../docs && python -m http.server 8080    # serve the site -> http://localhost:8080/
```

Or just open **`index.html`** at the repo root — it's a launcher that opens the site in `docs/`.
`pipeline/init.sh` chains download → score → validate. Page generation only needs
`pipeline/town_scores.csv` + `pipeline/town_template.html` + `docs/output/towns_geo.json`
(no `civica_data/`), so the site can be rebuilt anywhere.

## Layout

The repo root is intentionally bare — just the docs you read first and a launcher:

```
Civica_Towns/
├── README.md  CLAUDE.md  TODO.md      # start here
├── index.html                          # launcher -> opens docs/index.html
│
├── docs/                               # the website (GitHub Pages serves this folder)
│   ├── index.html  map.html  leaderboard.html  compare.html
│   ├── methodology.html  disclaimer.html  privacy.html  terms.html
│   ├── sitemap.xml  robots.txt  _headers  .nojekyll  og_image.png  LICENSE
│   └── output/
│       ├── towns/<place_fips>.html     # one report per town
│       ├── states/<XX>.html            # one ranked list per state
│       ├── town_index.json             # front-page + leaderboard search
│       └── towns_geo.json              # map dots
│
├── pipeline/                           # everything that builds the site
│   ├── county_loaders.py               # federal county dataset loaders (reused by the engine)
│   ├── town_scoring_engine.py          # builds town_scores.csv
│   ├── town_generator.py               # builds docs/ (pages + indexes + sitemap)
│   ├── build_town_geo.py               # town coordinates for the map
│   ├── download_town_data.py           # fetch the 3 town-specific datasets
│   ├── civica_data_downloader_v4/v5.py # fetch the county datasets
│   ├── validate_town.py                # quality gate
│   ├── init.sh   town_template.html    # setup chain · design system (CSS)
│   └── town_scores.csv  town_scores_meta.json   # scoring output (committed)
│
└── civica_data/                        # raw federal data (junction; gitignored, ~7 GB)
```

## Deploy

It's a static site. On GitHub: **Settings → Pages → Source: `main` branch, `/docs` folder.**
GitHub serves `docs/` at the domain root, so the absolute URLs in the pages resolve correctly
(`.nojekyll` is included). Or host the contents of `docs/` on any
static host. `town_scores.csv` is committed so the site can be regenerated without the 7 GB
of raw data.

## Honesty (kept visible in the product)

- Town income is a ZIP→place address-share approximation, not a survey median.
- Crime is mapped from reporting agencies to places; county sheriffs stay in the county pool,
  and towns with no matched agency inherit the county/RUCC-tier rate (flagged, not penalized).
- ~42% of each score is county-inherited.
- No home-value level — affordability is rent-vs-income + appreciation.
- Universe = incorporated places + New England governing towns (MCDs) ≥ 1,000 pop. Township-style units outside New England and CDPs are excluded
  for now (see `TODO.md`).

See `methodology.html` for the full writeup. Scores are informational only — not financial,
investment, or real estate advice.
