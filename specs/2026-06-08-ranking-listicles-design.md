# Category ranking pages + internal linking

*Design doc · 2026-06-08*

## Goal

Build static, crawlable **ranking listicles** (the Phase-1 programmatic-SEO asset) so the
site captures high-intent long-tail searches like "safest towns in Texas 2026" and
"most affordable towns to buy," and so link equity flows down into the 12k town pages.

## What's generated (`docs/output/rankings/`)

Four thematic categories, each ranked by its model dimension:

| Category | Dir | Sort by | Headline column |
|---|---|---|---|
| Safest | `safest/` | `dim3` (Safety & Place) | Violent /100k |
| Best School | `best-schools/` | `dim5` (Schools) | School %ile |
| Most Affordable | `most-affordable/` | `dim1` (Affordability) | Rent burden |
| Fastest-Growing | `fastest-growing/` | `dim4` (Growth) | Growth 5yr |

For each: a **national Top 100** (`<cat>/index.html`) + a **per-state Top 25**
(`<cat>/<ST>.html`), plus a **hub** (`rankings/index.html`). ≈ 4 × (1 + 51) + 1 = **209 pages**.

**No duplication with existing pages:** *Overall* rankings are deliberately **not**
regenerated here — the national overall is the (now server-rendered) `leaderboard.html`,
and per-state overall already lives at `output/states/<ST>.html`. The hub links to both.

## Page design & SEO

- Reuses the existing state-page table design via a shared `LIST_PAGE_CSS` constant
  (score badge, verdict badge, category metric column), with real `<a>` town links.
- A chip nav switches between categories at the same scope + the Overall list.
- Each page: unique `<title>` / meta description / canonical (live host) / H1, an honest
  one-line metric definition, and **`ItemList` JSON-LD** (rich-result eligible).
- The hub is one card per category with a National button + all 51 state links.

## Internal linking (the other half)

- **Town pages** gain a **"Featured in Rankings"** card: a link up to the town's state
  *Best Towns* page, plus a chip for any state ranking it lands in (top-25), e.g.
  `🛡️ #3 Safest in MA`. Driven by `srank_<cat>` columns = per-state rank of each dimension,
  computed once in `main()`.
- A **"Rankings" nav link** added to town pages, `index.html`, and `leaderboard.html`.
- All 209 URLs added to `sitemap.xml` (now also includes `agents.html`).

## Honesty guardrails

Ranks use the model dimensions (consistent with the score). Towns whose crime/schools are
county-estimated carry a `· est.` marker in the relevant lists so an inherited value can't
masquerade as a verified town stat. "Most Affordable" notes it's rent-vs-income +
appreciation quality (no home-price level exists in the model).

## Components (`pipeline/town_generator.py`)

`RANK_CATS` config · `build_ranking_page(cat, state, towns, root)` · `build_rankings_hub(states)`
· `build_rankings_chips(row)` · `generate_rankings(df)` (called in `main()` after the state
loop) · `_rank_nav` / `_rank_jsonld` helpers · `write_sitemap(index_map, extra_urls)`.

## Verification ("done")

1. Full build → 209 ranking pages; `validate_town.py` **ALL GREEN**.
2. National page = 100 town links + ItemList JSON-LD + correct canonical; state page = 25.
3. Town page shows real "Featured in" chips; the one-page **PDF export still excludes** the
   new card (`.rankings-card` hidden in print) → stays 1 page.
4. Hub + a national + a state page render on-brand (verified via screenshots).
5. `git`: commit/push only on explicit user confirmation.
