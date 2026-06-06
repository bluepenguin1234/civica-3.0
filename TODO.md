# Civica Towns — TODO

*Created 2026-06-05.*

## Next up

### 1. Add New England towns (MCDs) — biggest coverage gap
In the six New England states (MA, CT, RI, VT, NH, ME) the real municipalities are
**Minor Civil Divisions (MCDs / "towns")**, not Census *incorporated places*. The current
universe is incorporated places only, so well-known New England towns are missing
(e.g. **Danvers, MA** — pop ~29,211, exists in Census as a SUMLEV 061 town, excluded).
This is ~1,500–2,000 real towns absent from the map, search, and rankings.

To add them, the pipeline needs three new wirings (the incorporated-places path doesn't
cover MCDs):
- **County mapping** via the MCD-part records (`SUMLEV==071`) instead of `157`.
- **Town income** via a ZCTA→**county-subdivision** crosswalk (the current ZIP→place file
  doesn't include MCDs).
- **Coordinates** from the Census **county-subdivisions** gazetteer (not the places gazetteer).
- Decide scope: New England only (cleanest), or all MCD states (also NJ/PA/NY/WI/MI...).
- Flag MCD towns in the UI so the universe stays honest.

## Smaller follow-ups
- **+ Compare button** on each town report (compare.html already works via its own search).
- **Map marker clustering** so dense metros collapse into count bubbles until you zoom.
- **Landing-page verdict/score filter** for browsing.
- ~~Schools dimension~~ **DONE** — added as dim 5 (EDFacts proficiency, within-state; town-resolved share 51%→58%).
- Optional: boundary-free basemap on the map if county/state lines are distracting.
- Optional: real OG image per page (currently one static `og_image.png`).

## Done (for reference)
- Town model built: 10,267 incorporated places ≥ 1,000 pop, 5 dimensions (incl. Schools), ~58% town-resolved.
- Full site converted to towns: landing search, zoomable Leaflet town map, leaderboard,
  compare, per-state pages, methodology.
- Visual town reports: radar, glance tiles, percentile bars, location mini-map, county peers.
