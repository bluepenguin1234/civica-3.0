# "The Numbers" redesign — vs-typical comparison rows

*Design doc · 2026-06-06*

## Problem

On the town report, the **"The Numbers"** card (`build_fundamentals_card` in
`pipeline/town_generator.py`) has two defects:

1. **Lopsided layout.** It uses CSS `column-count:2` over four uneven sections
   (Money 4 rows, Growth 3, Safety 2, Schools 1). The flow dumps all of "Money"
   into the left column, leaving dead space, while Growth/Safety/Schools stack
   tall on the right. The columns never balance.
2. **No judgment.** Every figure is a bare number (`142`, `32`, `+0.5%`) with no
   benchmark. A reader can't tell that Boston's `32` proficiency is *below* the
   typical town, or whether `142` property crime is high or low. The card above
   it ("At a Glance") already shows percentile tags/bars, so today "The Numbers"
   adds raw figures with zero context — the weakest card on the page.

## Approach (chosen: "Direction A")

Replace the `column-count` flow with a **single balanced column of grouped rows**
where each metric is shown **against the typical US town**. This leans into
Civica's actual moat — every town is ranked against all 12,192 — and turns the
card into the *show-our-work / evidence* layer beneath the "At a Glance" summary.

Two alternatives were mocked and rejected: a comparison **table** (Boston |
Typical | ▲▼) felt heavier and coupled the arrow to value-direction confusingly;
a **minimal** balance-only fix was too close to the current look to be worth it.

## Layout

Each row is a 3-column grid:

```
[ metric label + sub-label ]   [ town value ]   [ comparison bar ]
                                                  US typical: <median>
```

- **Label** (`13.5px`, ink) + muted inline sub (`per tax return`, `5-year`, …).
- **Town value** — Inconsolata, bold, navy, right-aligned. Keeps the existing
  `county est.` chip for imputed metrics (crime / income / schools).
- **Comparison bar** — a thin track (`~7px`, rounded) with:
  - a **center tick** = the national **median** for that metric;
  - a **colored dot** at the town's national-percentile position;
  - small caption beneath: `US typical: <median value>`.
- Grouped under the existing four subheads: 💵 Money & affordability · 📈 Growth ·
  🛡️ Safety · 🎓 Schools.
- **Responsive:** below `560px`, the bar moves to full width under the value
  (rows become two lines), matching the existing mobile collapse.

## Dot position and color (the honest part)

- **Dot position = the town's national percentile of the metric's _raw value_.**
  The dot literally means "where this town sits among all US towns," so it always
  agrees with the printed value and the median tick (a median town sits dead
  center). It deliberately does **not** use the scoring percentiles, which embed
  judgments (e.g. "~5% appreciation is healthiest") that would make the dot
  disagree with the value.
- **Color** via the existing `pctl_tag` thresholds: green (top) · blue (above
  avg) · amber (below) · red (bottom). One consistent rule across the card:
  **green/right = good for a buyer, red/left = bad.** Lower-is-better metrics
  (rent burden, violent crime, property crime) are inverted so "good" stays green
  and to the right — consistent with "At a Glance."
- **Home-price growth is the one exception** — faster isn't simply "better"
  (good for owners, bad for affordability; the model treats ~5% as healthiest).
  It gets a **muted/neutral dot** positioned by raw magnitude, plus a tiny
  `context` label so it doesn't read as missing data. No green/red verdict.
- **No "better/worse" words** on the rows — the color + position already carry
  the verdict; adding text to 10 rows hurts scannability.

## Metrics shown (10) and their percentile source

| Section | Metric | Field | Better dir | National pctl |
|---|---|---|---|---|
| Money | Typical income | `town_income` | up | `pctl_income` ✓ |
| Money | Rent burden | `rent_burden` | down | `pctl_rentburden` ✓ (inv) |
| Money | Average wage | `avg_annual_wage` | up | `pctl_wage` ✓ |
| Money | Home-price growth (3yr) | `hpi_3yr_avg` | neutral | raw-magnitude rank (new), muted |
| Growth | Population growth (5yr) | `town_growth_5yr` | up | `pctl_growth` ✓ |
| Growth | Income growth | `town_income_growth` | up | **`pctl_incgrowth` (new)** |
| Growth | Net migration | `RNETMIG2023` | up | **`pctl_netmig` (new)** |
| Safety | Violent crime | `violent_per100k` | down | `pctl_crime` ✓ (inv) |
| Safety | Property crime | `property_per100k` | down | **`pctl_property` (new, inv)** |
| Schools | Proficiency | `school_score` | up | `pctl_schools` ✓ |

Three new percentile columns are trivial one-line `rank(pct=True)` additions next
to the existing block (`town_generator.py` ~L811–821). Home-price growth uses a
fresh raw-magnitude rank for its dot (not the existing "distance-from-5%"
`pctl_appr`, which is a scoring construct).

`school_score` is itself a within-state percentile; its "US typical" benchmark is
the cross-town median of `school_score` (~53), labeled `MA percentile` as today.

## Data plumbing

- Compute a `MEDIANS` dict (national median per displayed metric) **once** in
  `generate()` on the full dataframe, alongside the percentile block. Thread it
  through `generate_state(...)` → the per-town page builder →
  `build_fundamentals_card(row, medians)`.
- Add the 3 new `pctl_*` columns in the same percentile block.
- `build_fundamentals_card` rewritten to emit the grid rows; a small helper
  formats each metric's value + median for display (reuse `money()` for dollar
  fields, existing `%`/`+x.x%` formatting otherwise).
- New CSS in `town_template.html` replacing `.numwrap/.numsec/.numrow/.nl/.nv`
  with the row-grid + track/tick/dot classes. Reuse `--` tokens and `pctl_tag`
  colors — **no new palette colors**.

## Out of scope

- No change to scoring, dimensions, or `POINTS`.
- No change to "At a Glance," the radar, position, peers, or schools cards.
- No new metrics beyond the 10 already shown.

## Verification ("done")

1. `cd pipeline && python town_generator.py --state MA` — regenerate MA, open
   Boston (`2507000`) over the local HTTP server, screenshot the card, confirm
   balanced layout + correct dots/benchmarks.
2. Full rebuild: `python town_generator.py` then `python validate_town.py` —
   must print **ALL GREEN** (no scoring touched, so geo/score steps unneeded).
3. Spot-check 2–3 other towns (a Strong Buy, a Caution) for sane dots/colors.
4. `git push` only after explicit user confirmation (per CLAUDE.md).
