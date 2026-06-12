# Civica Signals — Contractor Leads Upgrade (plan of attack)

**Date:** 2026-06-12 · **Status:** PLAN — nothing implemented
**Scope:** the Signals subsystem only (`signals/` + `docs/signals/`). Never touches `pipeline/`.

## 0. The problem, restated

The dashboard today is a *meeting-record news feed*: 88 events / 49 stories for one town
(Danvers), rendered chronologically. For a contractor trying to win work it fails in four ways:

1. **Thin items dominate.** ~50 of 88 events come from agenda line items whose summaries say
   "no specifics provided in the agenda." When minutes later add detail, the agenda event is
   not merged away — stories like Abiomed show 9 events, half of them agenda echoes.
2. **The contact promise is unfulfilled.** The schema was designed for contacts
   (`applicant`, `applicant_reps`, `job_contact`, `contacts_enriched`) but the feed has
   reps on only 14/88 events, job_contact on 5/88, and `contacts_enriched` is never
   populated — the enrichment stage was deferred (spec "Phase 2.4") and never built.
3. **Organized by what happened, not what can be won.** No bid/RFP source is crawled at
   all, approved-and-ready projects are buried in the same list as pole-replacement
   hearings, and `feed.json` already publishes an `upcoming` array the UI never uses.
4. **Filters use the regulator's taxonomy** (event types, procedural stages), not the
   contractor's (trade, actionability, has-a-contact).

Four product asks drive this plan: **(a)** easier/cleaner filters, **(b)** contact
intelligence (developer / owner / engineer / architect / GC + website / phone / LinkedIn),
**(c)** AI summaries written for contractors instead of raw hearing language, **(d)**
trade-specific filters (HVAC / electrical / roofing / sitework / solar / plumbing …).

Each maps to a phase below: (d)→A3+B, (c)→B, (b)→C, (a)→D.

---

## Phase A — Data foundation (extraction v2 + new sources)

Everything downstream depends on richer events. Re-extraction is cheap: `extract.py` is
idempotent per document (delete + reinsert) and runs on the operator's Claude subscription,
so the existing ~38 Danvers docs can simply be re-run after the prompt/schema change.

### A1. Extraction schema v2 (prompt + DB)

Add to the extraction prompt and `events` table (SQLite `ALTER TABLE ADD COLUMN` — no
migration framework needed):

| field | type | rule |
|---|---|---|
| `owner` | TEXT | property owner *as named in the document*, when distinct from applicant (agendas often list both) |
| `next_date` | TEXT (ISO) | continuation / next-hearing / bid-due date **explicitly stated** in the document. Today these sit unstructured inside summaries ("continued to June 23, 2026") — pure waste |
| `trades` | TEXT (JSON array) | controlled vocabulary, see A3 |
| `is_public_work` | INTEGER | 1 when the buyer is the town/district (appropriations, DPW work, municipal buildings) — drives the Opportunities split |

Keep the never-infer rule for all factual fields. `next_date` and `owner` must appear
verbatim in the document; `trades` is the one judgment field (below).

### A2. New crawl sources (the biggest single lever)

1. **Bids/RFP module.** CivicPlus sites expose a standard `/Bids.aspx` listing
   (Danvers has one). New `doc_type='bid'`, new `event_type='bid_rfp'`, with
   `next_date` = bid due date and `job_contact` = the purchasing contact (these pages
   *do* publish names, emails, phones — unlike minutes). This is the most direct
   "contractor gets a job" feature in the entire plan; nothing else comes close per
   unit of effort. Implementation: a `bids` capability on `CivicPlusAdapter` (separate
   discover method; HTML detail pages, not PDFs — extraction takes an HTML-to-text path,
   which `pdf_pages()` does not currently handle, so add a sidecar text path like OCR's).
2. **Agenda packets.** The CivicPlus regex currently matches only
   `ViewFile/(Agenda|Minutes)`. AgendaCenter rows also link packet variants
   (e.g. `ViewFile/Agenda/_<date>-<id>?html=true`'s attachments / `?packet=true`
   forms vary by site — verify against danversma.gov before coding). Packets contain
   the actual applications: engineer letterhead, applicant addresses/phones lawfully in
   the public record. This single change fixes most "no specifics in the agenda" cards
   **and** feeds Phase C with doc-sourced contacts. The `documents.doc_type` enum
   already reserves `packet`. Guard: packets can run hundreds of pages — raise the
   chunking ceiling test and add a per-doc page cap (skip-with-flag above it).
3. **Registry growth is Phase E**, but A2 must keep adapters town-agnostic — no
   Danvers-specific parsing.

### A3. Trade tagging (powering ask (d))

Controlled vocabulary (~15, one flat list, no hierarchy):

```
site_excavation · demolition · paving_asphalt · concrete_foundation · framing_carpentry
roofing · electrical · plumbing · hvac · masonry · drywall_finishes · landscaping
utilities · solar_energy · stormwater_septic
```

Prompt rules to keep the filter *useful* (the failure mode is tagging every building
project with every trade, making the filter a no-op):

- Tag only trades the **described scope** implies. A pole petition → `utilities,
  electrical`. A parking-lot expansion → `site_excavation, paving_asphalt, landscaping`.
- A full new building with no scope detail gets the **generic-building set**
  (`site_excavation, concrete_foundation, framing_carpentry, electrical, plumbing,
  hvac, roofing`) **only at stage ≥ approved** — pre-approval, tag only what's stated.
- Informational/plan/study items: no trades (they're not jobs yet).
- Story-level trades = union of member-event trades (computed in Phase B).

### A4. Agenda↔minutes merge (link stage)

Same board + same meeting date + same story → the agenda event is an *echo* of the
minutes event. Add `events.superseded_by` (TEXT, event_id). Link stage sets it when a
minutes-sourced event exists for the same (story, board, meeting_date); publish then
emits **one** card carrying the minutes summary plus *both* source links
(`sources: [{kind:'agenda',url},{kind:'minutes',url}]`). The agenda event stays in the
DB (audit trail, validator can count it) but leaves the feed. Expected effect on current
data: ~25 duplicate cards disappear and the Abiomed story drops from 9 cards to ~5
substantive ones.

**Validator additions (A):** every `bid_rfp` has `next_date`; `trades` values ⊆
vocabulary; no published event has a published superseding twin; `next_date` parses ISO;
re-extraction count sanity vs. previous run.

**Exit criteria:** re-extracted Danvers feed where (i) bids appear, (ii) ≥80% of
"continued to <date>" summaries carry a structured `next_date`, (iii) agenda echoes are
merged, (iv) every non-informational event has 1+ trades.

---

## Phase B — Story briefs (ask (c): AI summaries for contractors)

Event summaries stay strictly extractive — that honesty rule is load-bearing and matches
the repo's culture. The *story* level is where the contractor-friendly synthesis lives.

New stage `signals/synthesize/build_briefs.py`, run after `link_stories.py`, only for
stories whose `last_activity` changed (cheap, incremental). One Claude call per dirty
story, input = all member-event summaries + structured fields, output stored on
`project_stories.brief` (JSON):

```json
{
  "what":       "36-unit apartment building over ground-floor retail at 156-158 Maple St.",
  "status":     "Continued at Planning Board four times; next hearing Jun 23, 2026.",
  "whats_next": "Special permits + site plan decision pending.",
  "outlook":    "If approved this summer, construction start plausible spring 2027.",
  "trades":     ["site_excavation","concrete_foundation","framing_carpentry","electrical","plumbing","hvac","roofing","landscaping"],
  "est_value":  null,
  "next_date":  "2026-06-23",
  "generated_at": "..."
}
```

Honesty contract — the difference between `status` and `outlook`:

- `what` / `status` / `whats_next`: derived only from the events. No outside knowledge.
- `outlook` is the **only** field allowed to project (construction-start window, likely
  next step), must be phrased as a projection, and the UI renders it visually as one
  (muted/labeled "outlook — projection", existing tokens only). Dollar values are *never*
  invented: `est_value` is populated only from a stated `dollar_value`. The target ask
  ("Estimated value $12M. Likely construction start Spring 2027") is achievable exactly
  to the extent the record supports it — and labeled where it's inference.
- `methodology-signals.html` gets a section documenting the fact/projection split.

Rollups also computed here (no LLM needed): story `trades` union, story `next_date`
(min future member `next_date`), `has_contacts` flag.

**Validator additions (B):** every multi-event active story has a brief; `outlook` never
contains a dollar figure unless a member event stated one; brief `next_date` consistent
with member events.

---

## Phase C — Contact intelligence (ask (b))

### C1. Entity model (new tables, not more JSON blobs)

The current per-event JSON fields can't answer "who is the engineer on this project" or
"which firms are most active in Danvers" — the same firm appears as free text across
many events. Add:

```sql
entities (entity_id PK, kind person|firm|public_office, canonical_name, town_scope,
          website, phone, linkedin_url, enrich_source, enrich_confidence,
          last_verified, review_status)
event_entities (event_id FK, entity_id FK, role, source 'doc'|'enrich')
  -- role: developer | owner | engineer | architect | attorney | surveyor
  --       | gc | public_contact
```

`signals/enrich/resolve_entities.py` (deterministic, no LLM): normalize and dedupe names
from `applicant` / `owner` / `applicant_reps` / `job_contact` across all events
(rapidfuzz, same ≥85 token_set_ratio convention as link stage; ambiguous merges go to a
human file, same pattern as `ambiguous_links.txt`). This step alone — zero web calls —
yields the **Directory** view in Phase D.

On the GC role: be honest in the UI that planning records almost never name the GC —
it's selected after approval. The field exists for the minority of cases (design-build,
bid awards in Select Board minutes, packets) and for the future building-permit source.
Don't promise it; show it when present.

### C2. Enrichment (website / phone / LinkedIn)

`signals/enrich/enrich_entities.py`, batched, only for entities attached to **active,
non-informational** stories (don't burn lookups on pole petitions). Source ladder:

1. **MA Secretary of the Commonwealth corporate registry** — free, authoritative for
   LLC/Inc applicants: managers, registered agent, business address. Highest-value
   single source for "who is behind 'PMZ Realty Trust'".
2. **Firm website** — web search on canonical name + town/state; store the URL and any
   phone published on the firm's own site.
3. **Phone** — only from the firm's own site or the official registry. No
   data-broker/people-search sources for individuals (quality and ethics both fail).
4. **LinkedIn** — **store a constructed search URL**
   (`linkedin.com/search/results/all/?keywords=<name>+<firm>`), not scraped profile
   data. Scraping LinkedIn violates its ToS and would poison the product; a search
   link delivers ~90% of the user value at zero risk. Only store a direct profile URL
   if the firm's own site links it.

Mechanics: reuse the `claude -p` backend pattern from `extract.py` with web-search
enabled, returning a strict JSON envelope per entity; or plain HTTP for the SOC registry
(it has a stable search endpoint). Every enriched field carries `enrich_source`,
`enrich_confidence`, `last_verified`. **Enriched contacts go through the same review
gate as events** (`review_status`, extend `review/review.py`) — a wrong phone number is
worse than no phone number. Re-verify stale entities (>180 days) lazily.

### C3. Publish

Per published event/story, a `contacts` array assembled from `event_entities` + the
entity record: `{role, name, firm, website, phone, linkedin_url, source: 'public record'
| 'enriched', verified}`. The UI must visually distinguish doc-sourced from enriched
(provenance is the product's credibility).

**Open product question (needs a decision before C3 ships):** `feed.json` is public
static JSON behind a placeholder login. Enriched contacts are the paywall moat — publish
them into the public file and the moat is free to anyone reading the JSON. Options:
(a) accept for MVP (current "login" is already cosmetic), (b) split a gated
`contacts.json` now and have `signals.js` fetch it through the existing `checkAccess()`
seam (the architecture comment in `signals.js` anticipates exactly this). **Recommend
(b)** — it's ~1 day and keeps the seam honest.

**Validator additions (C):** no entity published without review_status approved; every
phone/website has an `enrich_source`; LinkedIn URLs are search-links or firm-site-sourced
profile links only; entity merge produced no cross-town merges.

---

## Phase D — Dashboard v2 (asks (a) + (d), and the reframe)

All inside `docs/signals/` (template + JS); clean.css tokens only, no new colors.

> **Design principle — radical simplicity (overrides every D-phase decision).**
> A roofer opens this on a phone between jobs. The screen must answer "is there
> work for me here?" in **one glance, with zero configuration**. Every control we
> add is a tax on that glance. Rules we hold ourselves to:
> - **One screen, one job.** Each view is a single scannable list, top-down,
>   newest/soonest first. No dashboards-of-dashboards, no dense grids, no charts.
> - **At most two controls visible at rest** (the view switch + trade chips).
>   Everything else hides behind one "Filters" button.
> - **A card is readable in ~2 seconds:** title, one-line what, a date, a contact.
>   Detail is a tap away, never crammed onto the card.
> - **Mobile-first, single column** at every width; tap targets ≥ 40px (already a
>   token convention here). If it isn't obvious without a tutorial, it's wrong.
> - **Plain words over jargon** in all labels ("Open jobs," not "RFP feed";
>   "Coming up," not "Pre-decision pipeline").

### D1. Views instead of one feed

A single row of **3 plain-labeled views** (segmented control, not a nav bar). Three,
not four — fewer is the point; the Directory is reached by tapping any contact name,
not a top-level tab.

- **Open jobs** (default, zero-config): open bids by due date · just-approved/permitted
  private projects (last 60 days) · funded public work (`dollar_value` +
  `is_public_work`). The "what can I win now" screen — this is what loads first.
- **Coming up**: active projects still pre-decision, soonest hearing (`next_date`)
  first — the window to get known before the GC is picked. Uses the already-published
  `upcoming` data.
- **Everything**: today's chronological feed, kept verbatim as the trust/audit view.

**Directory** is not a tab — tapping any firm/person name anywhere opens that entity's
page (their projects + contact card). Discoverable, never in the way.

### D2. Filters, easier and cleaner (ask (a))

The whole filter story is **two things at rest, the rest one tap away**:

- **Visible always:** the 3-view switch + a single horizontal scroll row of **trade
  chips** (ask (d)) — only trades present in the data, multi-select, tap to toggle.
  A roofer taps "Roofing" once and is done; no other interaction required.
- **Behind one "Filters" button** (a sheet/disclosure): free-text search, town select
  (auto-hidden while one town), date range, stage, event-type, and a "has contacts"
  toggle. These exist for power users and never crowd the first glance.
- A small **"clear"** appears only when a filter is active. Filter state stays
  URL-persisted (keep `readFilters`/`writeFilters`; add keys `view`, `trades`, `q`).

This is strictly fewer visible controls than today (today shows type chips + stage +
two date inputs + clear, all at once) — simpler, not just rearranged.

### D3. Card v2 (two-second scan, detail one tap away)

The card carries only what answers "is this for me?" — four lines, nothing more:

1. **Title** + stage badge + **next-date pill** ("Hearing Jun 23").
2. **One-line `what`** (Phase B brief) — the plain-language summary.
3. **Trade chips** (the matched trades, so the scan confirms the filter hit).
4. **One primary contact** (most relevant role, linkified) — or "View contacts" if
   several.

Everything else — `status`/`outlook`, numeric facts, full contact list with
provenance marks, the agenda+minutes sources, and the timeline — lives on the
**story detail page** (today's `#story/<id>` route, already built), reached by tapping
the card. The card never shows all of it at once. Event cards in **Everything** keep
today's extractive rendering. `outlook` is always rendered as a labeled projection
(per Phase B), only on the detail page.

### D4. Sequencing note

D1/D2 with *existing* data (views, search, filter cleanup) is shippable independently of
A–C and already improves the product; the full value needs A (trades, next_date, bids)
underneath. Build D1/D2 skeleton early, wire fields as phases land.

---

## Phase E — Reach (brief, mostly out of scope here)

- **Towns 2–5** from the 30-town registry (Beverly, Peabody, Salem, Marblehead next —
  CivicPlus-heavy, so the adapter amortizes). More towns multiplies leads more than any
  feature; for a contractor, breadth beats depth.
- **Weekly digest** (email): "new bids + new approvals + your trades, your towns."
  Contractors will not revisit a dashboard weekly; the digest is the retention product
  and the natural paywall unit. Needs the Phase C gating decision first.
- Set the real `CONTACT_EMAIL` in `signals/config.py` before any crawl expansion (it's
  still a TODO placeholder in the user agent).

---

## Order of execution & effort

| # | Work | Size | Depends on |
|---|---|---|---|
| 1 | A1 prompt+schema v2, re-extract Danvers | S–M | — |
| 2 | A4 agenda/minutes merge | S | A1 re-run |
| 3 | A2.1 Bids module crawl+extract | M | A1 |
| 4 | D1/D2 dashboard skeleton (views, search, cleaner filters) | M | — (parallel) |
| 5 | B story briefs + rollups | M | A1, A4 |
| 6 | A2.2 agenda packets | M | A1 (verify CivicPlus packet URLs first) |
| 7 | C1 entity resolution + Directory view | M | A1 |
| 8 | C2 enrichment + review gate, C3 publish + gating decision | L | C1 |
| 9 | D3 card v2 final wiring | S | B, C |
| 10 | E towns 2–5, digest | M+ | all stable |

Sizes: S ≤ half a day · M ≈ 1–2 days · L ≈ 3+ days. Steps 1–5 are the MVP that visibly
transforms the dashboard; 6–9 deliver the contact-intelligence promise; 10 scales it.

## Risks & honesty guardrails

- **Trade over-tagging** makes the marquee filter useless → the A3 stage-gated rules,
  and validator stat: median trades/event ≤ 4.
- **Wrong contacts** are worse than none → review gate on enriched data, provenance
  labels in UI, no people-search sources, LinkedIn as search-links.
- **Projection creep** → `outlook` is the only inferential field, visually segregated;
  methodology page documents the split; validator greps briefs for unlabeled `$` claims.
- **Packet size blowups** → page cap + skip-with-flag.
- **Public JSON vs. paid moat** → decide C3 option (recommend gated `contacts.json`)
  before enrichment ships.
- **Crawl politeness** at 5 new sources/towns → existing delay floor stays; real contact
  email before expansion.

## Explicitly out of scope

Building-permit feeds, GC tracking beyond what records state, paid data brokers, any
change to `pipeline/` or the town-score model, scraping LinkedIn profiles.
