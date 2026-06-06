# Civica Towns — TODO

*Created 2026-06-05.*

## Next up

### 1. Remaining coverage gaps (deliberate exclusions for now)
- **Township-style municipalities outside New England** — **6,681** governing townships
  (SUMLEV 061, FUNCSTAT 'A', ≥1,000 pop) in PA/OH/MI/NY/IN/IL/WI/NJ…. Income + coords are
  already covered by our national crosswalk/gazetteer, BUT they **cannot be deduped with the
  data we have** (scoped 2026-06-06). New England worked because cities are flagged 'F';
  outside NE, townships *and* the incorporated villages inside them are both 'A', and no
  column separates them — tested `PLACE`/`CONCIT` (all 00000), `PRIMGEO_FLAG` (4,008/0 split
  but doesn't track overlap), name+county twin (catches only 32%), and a ZCTA land-area bridge
  (broken — ZCTAs cluster on places, so it smears coverage onto rural townships). **Correct
  dedup needs TIGER/Line shapefiles (places + county subdivisions) + a geopandas spatial-overlap
  step** — a new dataset + geospatial code the pipeline doesn't have. Real project, not a quick
  add. Until then, adding them would double-count villages-inside-townships.
- **CDPs (unincorporated communities)** — ~thousands, but not in the annual sub-county
  estimates file at all; needs decennial/ACS data with a different vintage and no growth signal.
- **Incorporated places under 1,000 pop** (~9,212) — in the data, filtered out; data gets too
  sparse below 1,000 to score honestly.

## Monetization / go-to-market (the next big push — see in-depth brief 2026-06-06)

**Golden rule:** never monetize the scores themselves. The moat is "100% federal data,
scores can't be bought, no ads, no agents." Monetize *adjacent* (leads, tools, data), keep
the scores visibly incorruptible.

### Phase 0 — make it real & measurable (do first; mostly Claude can build)
- [ ] **Custom domain** (e.g. civica.app / getcivica.com) — *user buys*; Claude wires DNS +
  Pages + per-page OG absolute URLs. Unblocks credibility, SEO, sharing, OG. **#1 priority.**
- [ ] **Analytics** (GA4 or Plausible) — *user creates account → gives ID*; Claude installs.
- [ ] **Google Search Console** + submit sitemap (11,365 URLs ready) — *user verifies*.
- [ ] **Email capture** on every town page (Formspree/ConvertKit) — Claude builds. Only owned channel.
- [ ] **Per-page OG images** — prototype done (`build_og_images.py`); finish once domain exists.

### Phase 1 — traffic (1–3 mo)
- Programmatic SEO is the asset: 11,306 buyer-intent pages ("is [town] a good place to buy").
  Internal linking, indexing, Reddit (r/RealEstate, r/personalfinance, city subs), "best towns
  2026" listicles, leaderboard as link-bait.

### Phase 2 — first revenue (low-risk, no score compromise)
- **Lead-gen / affiliate** on high-traffic town pages: mortgage pre-approval, **home insurance**
  (natural tie to the climate-risk dimension), moving quotes, home warranty. *User signs up for
  programs (needs business identity); Claude integrates.*

### Phase 3 — recurring revenue (needs a backend — site is currently 100% static)
- **Civica Pro** ($9–19/mo): watchlists, score-change alerts, unlimited compare, PDF/CSV export.
  Requires Supabase (auth/data) + Stripe (payments) + a small serverless layer. Real build.

### Phase 4 — scale (B2B, highest $/customer)
- **Data licensing / API**: sell the federal composite to relocation firms, proptech, mortgage
  lenders, **insurers**, employer relocation.

**Blocked on user (identity/accounts/$):** buy domain, create GA4+Search Console, later Stripe +
affiliate signups. **Everything else Claude can build.**

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
