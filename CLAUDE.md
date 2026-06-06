# CLAUDE.md — Civica Towns

Guide for Claude Code working in this repo. Keep it accurate; keep it short.

## What this is

A static site that scores **10,267 US towns** (incorporated places ≥ 1,000 pop) on a single
**0–100 Civica Score** from **5 dimensions**, built entirely from **federal data**. Each town
is ranked inside its county. See `README.md` for the public overview and `methodology.html`
for the full model writeup.

## The model (don't change without reason)

- **Dimensions / caps:** Affordability 25 · Economy 24 · Safety & Place 22 · Growth 15 · Schools 14 = 100.
- **~58% town-resolved** (crime, income, growth, town scale, schools); **~42% county-inherited**
  (wages, appreciation, climate risk, migration, permits). The exact T/C point split lives in
  `POINTS` in `town_scoring_engine.py` and is asserted by `validate_town.py`.
- **Verdicts:** Strong Buy ≥ 62 · Buy ≥ 52 · Hold ≥ 44 · Caution < 44 (≈ 15 / 27 / 28 / 31 %).
- Percentile normalization fixes the distribution at mean ≈ 50 / std ≈ 11 every run, so the
  fixed thresholds are stable. Score range ≈ 17–85.

## Pipeline (order matters)

All build scripts live in **`pipeline/`** and resolve paths via `__file__`, so they run from
anywhere (`cd pipeline && python <script>.py`). They read `civica_data/` (at the repo root) and
write the website into **`docs/`** (pages, `output/`, `sitemap.xml`); `town_scores.csv`,
`town_scores_meta.json`, and `town_template.html` sit in `pipeline/` with the code.

**Layout:** repo root is bare — `README.md`, `CLAUDE.md`, `TODO.md`, and a launcher
`index.html` (redirects to `docs/index.html`). The deployable website is **`docs/`** (GitHub
Pages source = `main` `/docs`). Build code is **`pipeline/`**. Raw data is the gitignored
`civica_data/` (~7 GB real folder inside the repo).

| Step | Script | Output | Needs `civica_data/`? |
|---|---|---|:--:|
| 1 | `download_town_data.py` | Census sub-est, IRS ZIP AGI ×2, ZCTA→place crosswalk | writes into it |
| 2 | `town_scoring_engine.py` | `town_scores.csv` + `town_scores_meta.json` | **yes** |
| 3 | `build_town_geo.py` | `output/towns_geo.json` (town lat/lon, Census Gazetteer) | no* |
| 4 | `town_generator.py` | `output/towns/`, `output/states/`, `town_index.json`, `sitemap.xml` | **no** |
| 5 | `validate_town.py` | the gate — must print ALL GREEN | no |

\* `build_town_geo.py` downloads the gazetteer over the network but doesn't read `civica_data/`.

- `county_loaders.py` — the federal county-dataset loaders the engine reuses (BEA, QCEW, FHFA,
  CBP, BPS, IRS migration, NFIP, NOAA, USFS, RUCC, HUD FMR) + `pct`/`pct_inv` helpers.
- `town_generator.py` reads the design system from `town_template.html` (the `<style>` block),
  so page generation needs only `town_scores.csv` + `town_template.html` + `towns_geo.json`.
  Run `python town_generator.py --state TX` to rebuild one state.

## Repo conventions

- **No county model here.** This repo is town-only. Don't reintroduce Zillow, home-value
  levels, price-to-rent, breakeven, or the old 6-dimension / 8-label county system.
- **`civica_data/` is gitignored** — a ~7 GB **real folder inside the repo** (no longer a
  junction; the data was moved in). `town_scores.csv` *is* committed so the site can be
  regenerated without it. To *re-score* (run the engine / add towns), `civica_data/` must be
  present. County datasets are (re)fetched by `civica_data_downloader_v5.py`/`v4`; the 3 town
  datasets by `download_town_data.py`; schools by `download_schools_data.py` (NCES EDGE +
  EDFacts via the Urban Institute mirror — ED.gov removed the direct files); property-tax fields
  are already in the IRS ZIP files; the 5.8 GB FBI NIBRS file is a manual download.
- **Static site:** lives in `docs/` (GitHub Pages source = `/docs`, `.nojekyll` included).
  Town pages are at `docs/output/towns/<7-digit place FIPS>.html`.
- **Design system** is shared: `town_template.html` for generated town pages; the other pages
  carry their own inline `<style>` but use the same palette (navy `#0d2d52`, blue `#1a7ff0`)
  and the `civi`+blue-`ca` logo.
- After any change that touches scoring or page generation, **run `validate_town.py`** and
  don't weaken its checks to make a run pass.

## Honest caveats (must stay visible in the UI)

Town income is a ZIP→place approximation, not a survey median. Crime is mapped from agency
name to place; county sheriffs cover unincorporated area and stay in the county pool; towns
with no matched agency inherit the county/RUCC-tier rate (flagged, never penalized). Schools use
EDFacts proficiency ranked within state (state tests aren't nationally comparable). ~42% of each
score is county-inherited. No home-value level exists. Universe = incorporated places
≥ 1,000 pop (New England MCD-towns and CDPs excluded — see `TODO.md`).

## Next steps

See `TODO.md`. The headline item is **adding New England towns (MCDs)** — the largest current
coverage gap (e.g. Danvers, MA).
