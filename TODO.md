# Civica Towns — TODO

*Created 2026-06-05.*

## Next up

### 1. Remaining coverage gaps (deliberate exclusions for now)
- **Township-style municipalities outside New England** (~9,164 ≥1,000 in NJ/NY/PA/WI/MI/OH…).
  Messier than New England: many *overlap* incorporated places (double-count risk) and aren't
  the primary government. Needs a per-state overlap/dedup rule before adding.
- **CDPs (unincorporated communities)** — ~thousands, but not in the annual sub-county
  estimates file at all; needs decennial/ACS data with a different vintage and no growth signal.
- **Incorporated places under 1,000 pop** (~9,212) — in the data, filtered out; data gets too
  sparse below 1,000 to score honestly.

## Smaller follow-ups
- **+ Compare button** on each town report (compare.html already works via its own search).
- **Map marker clustering** so dense metros collapse into count bubbles until you zoom.
- **Landing-page verdict/score filter** for browsing.
- ~~Schools dimension~~ **DONE** — added as dim 5 (EDFacts proficiency, within-state; town-resolved share 51%→58%).
- Optional: boundary-free basemap on the map if county/state lines are distracting.
- **Per-page OG share images** — PROTOTYPE DONE (`pipeline/build_og_images.py`; 4 sample
  cards in `docs/output/og/`, uncommitted). To finish: (1) swap Segoe UI/Consolas → real
  Poppins/Inconsolata TTFs; (2) decide all-10k (~850 MB) vs top-N, and render WebP to halve
  size; (3) wire absolute `og:image` URL to the real host, then per-town `<meta>` in
  `town_generator.py`.

## Done (for reference)
- ~~New England towns (MCDs)~~ **DONE** — added 1,039 NE governing towns (SUMLEV 061,
  FUNCSTAT 'A'; 10-digit GEOID, is_mcd flag). Income via ZCTA→cousub crosswalk (86% direct),
  coords via county-subdivisions gazetteer (100%), crime matched for ~60%, schools for ~59%;
  rest use county fallback (flagged). Universe 10,267 → 11,306.
- Town model built: 11,306 towns ≥ 1,000 pop, 5 dimensions (incl. Schools), ~58% town-resolved.
- Full site converted to towns: landing search, zoomable Leaflet town map, leaderboard,
  compare, per-state pages, methodology.
- Visual town reports: radar, glance tiles, percentile bars, location mini-map, county peers.
