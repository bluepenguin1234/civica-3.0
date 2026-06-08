# Town profile — "Save as PDF" export

*Design doc · 2026-06-08*

## Goal

Let a homebuyer export a clean, branded **printable one-pager** of a town report
(to save as PDF or print, e.g. to share with a partner or agent). The site is a
**static** GitHub Pages site with no backend, so the feature is **100% client-side**.

Note: `TODO.md` lists "PDF/CSV export" under the paid Civica Pro tier. This ships
the export as a **free** feature (print-based, zero infrastructure); a gated Pro
variant can come later if desired.

## Approach (chosen)

**Print stylesheet + a "Save as PDF" button that calls `window.print()`.** Reuses
the existing profile page; an `@media print` block hides what doesn't belong on
paper, compacts the cards, and reveals a print-only header/footer. No JS library,
no second layout to maintain, always in sync with the live page. (Rejected: a
dedicated hidden one-pager layout — drifts from the live page; a client PDF lib
like jsPDF/html2pdf — adds a dependency to every page for fiddly fidelity.)

## What prints vs. what's hidden

**Target: a single Letter page.** Kept (in order): print-only header → score/verdict
**hero** → **At a Glance** → **5-dimension breakdown + radar** → **How it Compares**
→ **The Numbers** → print-only footer.

**Hidden in print (`display:none`):** the sticky **nav**, the **verdict callout**
(`.signal` — its Buy/score is already in the hero), the interactive **Leaflet map
card** (`.loc-card`), the **"Towns in the Same County"** peers card (`.peers-card`),
the **"Compare" CTA**, the **"How we scored this"** expander, the on-screen site
**footer**, every card intro/caption (`.tcap`, `.print-hide`, `.dc-chip`,
`.hero-eyebrow`), and the **"Save as PDF" button** itself.

## Fitting one page

Two levers, applied in the `@media print` block:
1. **Vertical compaction** — reduced card/hero padding & margins, smaller hero,
   shrunk radar (`max-height:108px`), and **The Numbers rows collapsed to a single
   line** (the `US typical:` benchmark moved inline beside the bar via
   `.nbar-wrap{display:flex}` instead of stacking below). This brings Boston from
   ~1350px to ~1184px of content.
2. **`zoom:0.78` on `.page`** (centered with `margin:0 auto`) as the finisher —
   scales the whole page down uniformly to fit one Letter page with ~17px+ of
   headroom for long town names that wrap an extra hero line. Centered, the shrink
   reads as normal ~1in document margins (a heavy zoom would look like a narrow
   column, which is why compaction does most of the work first). Chromium (our PDF
   generator + Chrome/Edge/Safari, and Firefox ≥126) honor `zoom` in print;
   browsers that don't degrade gracefully to ~1.2 pages rather than breaking.

## Components / changes (`pipeline/town_generator.py`)

1. **`build_hero`** — add a screen-only `⬇ Save as PDF` button, absolutely
   positioned top-right inside the hero, `onclick="window.print()"`,
   `class="pdf-btn"`.
2. **`build_location_card`** — add a `loc-card` class to the card so print CSS can
   hide the whole card (not just `#locmap`).
3. **`generate_page`** — add a `compare-cta` class to the Compare CTA wrapper;
   insert a `print-only` **header** element (logo SVG + "Town Report" + page URL +
   "Generated <date>") at the top of `.page`, and a `print-only` **footer**
   (federal-sources line + "informational only — not financial/real-estate advice"
   + `civica.app`) at the bottom.
4. **`build_head`** inline `<style>`** — add:
   - `.pdf-btn` styling (small, translucent on the dark hero) + `.hero{position:relative}`.
   - `.print-only{display:none}` (shown only in the print block).
   - An **`@media print`** block:
     ```
     @page { margin: 14mm 12mm; }
     * { -webkit-print-color-adjust:exact !important; print-color-adjust:exact !important; }
     html, body { background:#fff !important; }
     .nav, .loc-card, .compare-cta, .howto, .footer, .pdf-btn { display:none !important; }
     .print-only { display:block !important; }
     .page { max-width:none; margin:0; padding:0; }
     .card, .signal, .hero { box-shadow:none !important; break-inside:avoid; }
     .hero { border-radius:0; }
     ```
   `print-color-adjust:exact` is what makes the navy hero, dimension bars, and the
   comparison dots actually render in print instead of being stripped to white.
5. **Date** — bake the build date into the print header via Python
   `datetime.date.today()` (`import datetime`), formatted `Mon D, YYYY`. Reflects
   when the report was generated; zero JS.

No change to scoring, the data pipeline, or `town_template.html` (all print CSS
lives in the per-page `build_head` block, beside the other page-structural CSS).

## Edge cases

- **Multi-page break:** `break-inside:avoid` keeps any single card from splitting
  across a page boundary.
- **MCD towns** (10-digit FIPS): URL in the print header uses the same
  `{fips}.html` the page already lives at — works for both 7- and 10-digit.
- **Ink:** the dark hero uses toner; acceptable (the user can pick grayscale in
  their own print dialog). Kept for brand fidelity per the approved mockup.

## Verification ("done")

1. Regenerate MA (`town_generator.py --state MA`); open Boston, click **Save as
   PDF**, confirm header/footer present; nav/map/CTA/howto/verdict/peers hidden;
   colors render.
2. Drive it headless: Playwright `page.pdf()` → confirm a **single-page** PDF with
   exactly the five sections, across several towns including the longest-named and
   a 10-digit MCD town.
3. Full rebuild + `validate_town.py` → **ALL GREEN**.
4. `git`: commit/push only on explicit user confirmation (per CLAUDE.md).
