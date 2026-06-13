/* Civica Signals dashboard v2 — two audiences, one feed.
 *
 * ARCHITECTURE RULE: no event data is embedded in the page. Everything is
 * fetched from DATA_BASE_URL — the ONE place the data endpoint is configured.
 * checkAccess() is the auth seam (today: localStorage session set by
 * login.html; Phase 5B: the real Supabase session). Nothing else may
 * hardcode data paths or auth.
 */
"use strict";

const DATA_BASE_URL = "../output/signals";   // future: https://api.<host>/signals
const SESSION_KEY = "civica_signals_session";
const MODE_KEY = "civica_signals_mode";

/* ── Access seam ──────────────────────────────────────────────────────── */
function checkAccess() {
  try {
    const session = JSON.parse(localStorage.getItem(SESSION_KEY));
    if (session && session.tier) return { authorized: true, tier: session.tier };
  } catch (_) { /* corrupt session -> sign in again */ }
  return { authorized: false, tier: null };
}
function signOut() {
  localStorage.removeItem(SESSION_KEY);
  location.replace("login.html");
}

/* ── Vocabulary ───────────────────────────────────────────────────────── */
const TYPE_LABELS = {
  residential_project: "Residential", commercial_project: "Commercial",
  mixed_use_project: "Mixed-use", subdivision: "Subdivision",
  "40b_application": "40B", zoning_amendment: "Zoning change",
  variance_special_permit: "Variance / SP", tax_override_debt_exclusion: "Override",
  infrastructure_project: "Infrastructure", municipal_property: "Municipal property",
  master_plan_comp_plan: "Plans & studies", bid_rfp: "Bid / RFP",
  other_notable: "Other",
};
const TRADE_LABELS = {
  site_excavation: "Excavation", demolition: "Demolition",
  paving_asphalt: "Paving", concrete_foundation: "Concrete",
  framing_carpentry: "Framing", roofing: "Roofing", electrical: "Electrical",
  plumbing: "Plumbing", hvac: "HVAC", masonry: "Masonry",
  drywall_finishes: "Drywall & finishes", landscaping: "Landscaping",
  utilities: "Utilities", solar_energy: "Solar", stormwater_septic: "Stormwater & septic",
};
const STAGES = ["proposed", "hearing", "continued", "approved", "denied",
                "withdrawn", "permitted", "under_construction", "informational"];
const PRE_STAGES = new Set(["proposed", "hearing", "continued"]);
const WON_STAGES = new Set(["approved", "permitted"]);
const DEAD_STAGES = new Set(["denied", "withdrawn"]);
const HOUSING_TYPES = new Set(["residential_project", "mixed_use_project",
                               "subdivision", "40b_application"]);
const HOUSING_CATS = {  // realtor chip row (client-side view filters, not facts)
  apartments: "Apartments", condos: "Condos & townhomes",
  single_family: "Single-family", forty_b: "40B / affordable",
  zoning: "Zoning changes",
};
const VIEWS = {
  contractor: [["open", "Open jobs"], ["coming", "Coming up"], ["all", "Everything"]],
  realtor: [["new", "New housing"], ["works", "In the works"], ["all", "Everything"]],
};

let FEED = null;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US",
    { month: "short", day: "numeric", year: "numeric" });
};
const fmtNum = (n) => n == null ? null : Number(n).toLocaleString("en-US");
const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

/* ── URL-persisted state: mode, view, trades, types, q (+stage/from/to) ── */
function readState() {
  const p = new URLSearchParams(location.search);
  let mode = p.get("mode") || localStorage.getItem(MODE_KEY) || "";
  if (!["contractor", "realtor"].includes(mode)) mode = "";
  const views = VIEWS[mode || "contractor"].map((v) => v[0]);
  let view = p.get("view") || views[0];
  if (!views.includes(view)) view = views[0];
  return {
    mode, view,
    trades: (p.get("trades") || "").split(",").filter(Boolean),
    types: (p.get("types") || "").split(",").filter(Boolean),
    q: p.get("q") || "", stage: p.get("stage") || "",
    from: p.get("from") || "", to: p.get("to") || "",
  };
}
function writeState(s) {
  const p = new URLSearchParams();
  if (s.mode) p.set("mode", s.mode);
  if (s.view && s.view !== VIEWS[s.mode || "contractor"][0][0]) p.set("view", s.view);
  if (s.trades.length) p.set("trades", s.trades.join(","));
  if (s.types.length) p.set("types", s.types.join(","));
  for (const k of ["q", "stage", "from", "to"]) if (s[k]) p.set(k, s[k]);
  const qs = p.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
}
let STATE = null;

/* ── Feed lookups ─────────────────────────────────────────────────────── */
const storyById = (id) => FEED.stories.find((s) => s.story_id === id);
const fullEventById = (id) => FEED.events.find((e) => e.event_id === id);
function storyFullEvents(story) {
  return story.events.map((se) => fullEventById(se.event_id)).filter(Boolean);
}
function storyTrades(story) {
  const out = new Set();
  storyFullEvents(story).forEach((e) => (e.trades || []).forEach((t) => out.add(t)));
  return out;
}
function storyTenure(story) {
  for (const e of storyFullEvents(story).reverse())
    if (e.tenure === "rental" || e.tenure === "ownership") return e.tenure;
  return null;
}
function storyNextDate(story) {  // soonest stated future date among member events
  const today = todayISO();
  const ds = storyFullEvents(story).map((e) => e.next_date)
    .filter((d) => d && d >= today).sort();
  return ds[0] || null;
}
function storyTypes(story) {
  return new Set(story.events.map((se) => se.event_type));
}
function housingCatsOf(ev) {  // view filter heuristics — labeled as filters, not facts
  const cats = new Set();
  const txt = `${ev.project_name || ""} ${ev.summary || ""}`.toLowerCase();
  if (ev.event_type === "zoning_amendment") cats.add("zoning");
  if (ev.event_type === "40b_application" || /\b40b\b|affordable/.test(txt)) cats.add("forty_b");
  if (!HOUSING_TYPES.has(ev.event_type)) return cats;
  if (ev.tenure === "rental" || /apartment/.test(txt)) cats.add("apartments");
  if (ev.tenure === "ownership" || /condo|townhom|townhouse/.test(txt)) cats.add("condos");
  if (/single[- ]family|\badu\b|duplex/.test(txt) ||
      (ev.event_type === "residential_project" && ev.residential_units != null && ev.residential_units <= 2))
    cats.add("single_family");
  return cats;
}

/* ── Base filtering (Filters panel + search apply everywhere) ─────────── */
function matchesPanel(ev) {
  const s = STATE;
  if (s.stage && ev.stage !== s.stage) return false;
  if (s.from && (!ev.date || ev.date < s.from)) return false;
  if (s.to && (!ev.date || ev.date > s.to)) return false;
  if (s.q) {
    const hay = `${ev.project_name || ""} ${ev.summary || ""} ${ev.applicant || ""} ` +
                `${ev.owner || ""} ${ev.address || ""} ${ev.board || ""}`.toLowerCase();
    if (!hay.includes(s.q.toLowerCase())) return false;
  }
  return true;
}
function matchesChips(ev) {
  if (STATE.mode === "realtor") {
    if (!STATE.types.length) return true;
    const cats = housingCatsOf(ev);
    return STATE.types.some((t) => cats.has(t));
  }
  if (!STATE.trades.length) return true;
  return (ev.trades || []).some((t) => STATE.trades.includes(t));
}
const visibleEvents = () => FEED.events.filter((e) => matchesPanel(e) && matchesChips(e));
function visibleStories(pool) {
  const ids = new Set(pool.map((e) => e.story_id).filter(Boolean));
  return FEED.stories.filter((s) => ids.has(s.story_id));
}

/* ── Card renderers (4 lines) ─────────────────────────────────────────── */
function stageBadge(stage) {
  return stage ? `<span class="sbadge sg-${esc(stage)}">${esc(stage.replace("_", " "))}</span>` : "";
}
function unitsPill(units, tenure) {
  if (units == null) return "";
  const t = (tenure === "rental" || tenure === "ownership") ? ` · ${tenure}` : "";
  return `<span class="tag units">${fmtNum(units)} unit${units === 1 ? "" : "s"}${esc(t)}</span>`;
}
function firstSentence(text) {
  const m = String(text || "").match(/^.*?[.!?](?:\s|$)/);
  return m ? m[0].trim() : String(text || "");
}
function contactLine(ev) {
  if (ev.event_type === "bid_rfp" && ev.job_contact) {
    const j = ev.job_contact;
    const who = [j.role, j.name, j.org].filter(Boolean).join(", ");
    if (who) return `Contact: <b>${esc(who)}</b>${j.contact_info ? " — " + esc(j.contact_info) : ""}`;
  }
  const who = ev.applicant || ev.owner;
  if (who) return `${ev.owner && !ev.applicant ? "Owner" : "Applicant"}: <b>${esc(who)}</b>`;
  if (ev.job_contact && (ev.job_contact.name || ev.job_contact.role)) {
    const j = ev.job_contact;
    return `Contact: <b>${esc([j.role, j.name, j.org].filter(Boolean).join(", "))}</b>`;
  }
  return "";
}
function tagChips(ev) {
  const tags = [];
  tags.push(`<span class="tag">${esc(TYPE_LABELS[ev.event_type] || ev.event_type)}</span>`);
  if (ev.residential_units != null) tags.push(unitsPill(ev.residential_units, ev.tenure));
  if (ev.commercial_sqft != null) tags.push(`<span class="tag units">${fmtNum(ev.commercial_sqft)} sq ft</span>`);
  if (ev.dollar_value != null) tags.push(`<span class="tag units">$${fmtNum(ev.dollar_value)}</span>`);
  (ev.trades || []).slice(0, 4).forEach((t) =>
    tags.push(`<span class="tag">${esc(TRADE_LABELS[t] || t)}</span>`));
  return tags.join("");
}
function srcLinks(ev) {
  const links = (ev.sources || []).map((s) =>
    `<a href="${esc(s.url)}" rel="noopener">${esc(s.kind)} ↗</a>`);
  if (!links.length && ev.source_url)
    links.push(`<a href="${esc(ev.source_url)}" rel="noopener">source ↗</a>`);
  return links.join(" ");
}
function eventCard(ev, { duePill = false } = {}) {
  const story = ev.story_id && storyById(ev.story_id);
  const title = ev.project_name || (story && story.name) || ev.address ||
                (TYPE_LABELS[ev.event_type] || ev.event_type);
  const head = story ? `<a href="#story/${esc(story.story_id)}">${esc(title)}</a>` : esc(title);
  const datebit = duePill && ev.next_date
    ? `<span class="due-pill">Due ${esc(fmtDate(ev.next_date))}</span>`
    : `<span class="date-pill">${esc(fmtDate(ev.date))}</span>`;
  return `<article class="scard">
    <div class="l1"><h3>${head}</h3>${stageBadge(ev.stage)}${datebit}</div>
    <p class="l2">${esc(firstSentence(ev.summary))}</p>
    <div class="l3">${tagChips(ev)}</div>
    <div class="l4"><span>${contactLine(ev)}</span><span class="links">${srcLinks(ev)}</span></div>
  </article>`;
}
function storyCard(story, { nextPill = true } = {}) {
  const evs = storyFullEvents(story);
  const latest = evs[evs.length - 1];
  if (!latest) return "";
  if (evs.length === 1) return eventCard(latest);
  const next = storyNextDate(story);
  const datebit = nextPill && next
    ? `<span class="due-pill">Next ${esc(fmtDate(next))}</span>`
    : `<span class="date-pill">${esc(fmtDate(story.last_activity))}</span>`;
  const trades = [...storyTrades(story)].slice(0, 4).map((t) =>
    `<span class="tag">${esc(TRADE_LABELS[t] || t)}</span>`).join("");
  return `<article class="scard">
    <div class="l1"><h3><a href="#story/${esc(story.story_id)}">${esc(story.name)}</a></h3>
      ${stageBadge(story.current_stage)}${datebit}</div>
    <p class="l2">${esc(firstSentence(latest.summary))}</p>
    <div class="l3">${unitsPill(story.total_units, storyTenure(story))}
      <span class="tag">${esc(story.events.length)} meetings</span>${trades}</div>
    <div class="l4"><span>${contactLine(latest)}</span>
      <span class="links"><a href="#story/${esc(story.story_id)}">History →</a></span></div>
  </article>`;
}
const sectionH = (title, n) =>
  `<h2 class="section-h">${esc(title)} <span class="count">${n}</span></h2>`;
const emptyCard = (msg) => `<div class="state-card">${msg}</div>`;

/* ── Views ────────────────────────────────────────────────────────────── */
function renderOpenJobs() {
  const evs = visibleEvents();
  const today = todayISO();
  const cutoff60 = daysAgoISO(60);
  const used = new Set();
  const take = (e) => { used.add(e.event_id); return e; };

  const openBids = evs.filter((e) => e.event_type === "bid_rfp" && e.next_date && e.next_date >= today)
    .sort((a, b) => a.next_date.localeCompare(b.next_date)).map(take);
  const wins = evs.filter((e) => !used.has(e.event_id) && e.event_type !== "bid_rfp" &&
      WON_STAGES.has(e.stage) && e.date && e.date >= cutoff60).map(take);
  const funded = evs.filter((e) => !used.has(e.event_id) &&
      e.is_public_work && e.dollar_value != null).map(take);

  let html = sectionH("Open bids", openBids.length);
  if (openBids.length) {
    html += openBids.map((e) => eventCard(e, { duePill: true })).join("");
  } else {
    const lastBid = evs.filter((e) => e.event_type === "bid_rfp" && e.next_date).sort((a, b) => b.next_date.localeCompare(a.next_date))[0];
    html += emptyCard(`No open bids right now${lastBid ? ` — the most recent closed <b>${esc(fmtDate(lastBid.next_date))}</b>` : ""}. New postings appear here automatically.`);
  }
  if (wins.length)
    html += sectionH("Approved in the last 60 days", wins.length) + wins.map((e) => eventCard(e)).join("");
  if (funded.length)
    html += sectionH("Funded public work", funded.length) + funded.map((e) => eventCard(e)).join("");
  return html;
}

function renderComingUp() {
  const stories = visibleStories(visibleEvents())
    .filter((s) => s.status === "active" && PRE_STAGES.has(s.current_stage));
  const withNext = stories.filter((s) => storyNextDate(s))
    .sort((a, b) => storyNextDate(a).localeCompare(storyNextDate(b)));
  const rest = stories.filter((s) => !storyNextDate(s))
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  const all = [...withNext, ...rest];
  if (!all.length) return sectionH("Coming up", 0) + emptyCard("Nothing pre-decision matches these filters.");
  return sectionH("Coming up — pre-decision, soonest first", all.length)
    + all.map((s) => storyCard(s)).join("");
}

function renderNewHousing() {
  const stories = visibleStories(visibleEvents())
    .filter((s) => [...storyTypes(s)].some((t) => HOUSING_TYPES.has(t)));
  const won = stories.filter((s) => WON_STAGES.has(s.current_stage))
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  const stalled = stories.filter((s) => DEAD_STAGES.has(s.current_stage) ||
      ["dormant", "dead"].includes(s.status))
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  let html = sectionH("New housing — approved or permitted", won.length);
  html += won.length ? won.map((s) => storyCard(s, { nextPill: false })).join("")
                     : emptyCard("No housing approvals match these filters yet.");
  if (stalled.length)
    html += sectionH("Recently denied or stalled", stalled.length)
          + stalled.map((s) => storyCard(s, { nextPill: false })).join("");
  return html;
}

function renderInTheWorks() {
  const stories = visibleStories(visibleEvents()).filter((s) => {
    const ts = storyTypes(s);
    const housingish = [...ts].some((t) => HOUSING_TYPES.has(t) || t === "zoning_amendment");
    return housingish && s.status === "active" && PRE_STAGES.has(s.current_stage);
  });
  const withNext = stories.filter((s) => storyNextDate(s))
    .sort((a, b) => storyNextDate(a).localeCompare(storyNextDate(b)));
  const rest = stories.filter((s) => !storyNextDate(s))
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  const all = [...withNext, ...rest];
  if (!all.length) return sectionH("In the works", 0) + emptyCard("No pre-decision housing or zoning matches these filters.");
  return sectionH("In the works — hearings soonest first", all.length)
    + all.map((s) => storyCard(s)).join("");
}

function renderEverything() {  // the chronological audit feed (unchanged shape)
  const evs = visibleEvents();
  const gen = (FEED.generated_at || "").slice(0, 10);
  const cutoff = new Date(gen || Date.now());
  cutoff.setDate(cutoff.getDate() - 7);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  const recent = evs.filter((e) => e.date && e.date >= cutoffIso);

  let html = sectionH("This week", recent.length);
  if (recent.length) {
    html += recent.map((e) => eventCard(e)).join("");
  } else {
    const latest = evs.find((e) => e.date);
    html += emptyCard(`No new activity in the last 7 days. Most recent meeting on record:
      <b>${esc(fmtDate(latest && latest.date))}</b> — minutes often post weeks after a meeting;
      agendas are the early signal.`);
  }
  const stories = visibleStories(evs).filter((s) => s.events.length > 1)
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  if (stories.length)
    html += sectionH("Project stories", stories.length) + stories.map((s) => storyCard(s, { nextPill: false })).join("");
  const inStories = new Set();
  stories.forEach((s) => s.events.forEach((se) => inStories.add(se.event_id)));
  const rest = evs.filter((e) => !inStories.has(e.event_id));
  if (rest.length)
    html += sectionH("All activity", rest.length) + rest.map((e) => eventCard(e)).join("");
  return html;
}

const RENDERERS = {
  open: renderOpenJobs, coming: renderComingUp,
  new: renderNewHousing, works: renderInTheWorks, all: renderEverything,
};

function renderFeed() {
  const html = RENDERERS[STATE.view] ? RENDERERS[STATE.view]() : renderEverything();
  $("content").innerHTML = html;
  $("stateCard").hidden = true;
}

/* ── Detail route (#story/<id>) ───────────────────────────────────────── */
function collectContacts(evs) {
  const out = [], seen = new Set();
  const add = (role, name, extra) => {
    const key = `${role}|${name}`.toLowerCase();
    if (!name || seen.has(key)) return;
    seen.add(key);
    out.push({ role, name, extra });
  };
  for (const e of evs) {
    if (e.applicant) add("Applicant", e.applicant, "");
    if (e.owner) add("Owner", e.owner, "");
    (e.applicant_reps || []).forEach((r) =>
      add(r.role || "Representative", r.name || r.firm, r.firm && r.name ? r.firm : ""));
    const j = e.job_contact;
    if (j && (j.name || j.role))
      add(j.role || "Contact", j.name || j.org, [j.org, j.contact_info].filter(Boolean).join(" — "));
  }
  return out;
}
function briefBlock(b) {  // Step 5 synthesis; "outlook" is the only projected field
  if (!b || !b.what) return "";
  const row = (label, val) => val
    ? `<div><dt>${esc(label)}</dt><dd>${esc(val)}</dd></div>` : "";
  return `<div class="brief">
    <p class="brief-what">${esc(b.what)}</p>
    <dl class="brief-dl">${row("Status", b.status)}${row("What's next", b.whats_next)}</dl>
    ${b.outlook ? `<div class="outlook">
      <div class="outlook-tag">Outlook — projection, not from the record</div>
      <p>${esc(b.outlook)}</p></div>` : ""}
  </div>`;
}
function renderDetail(storyId) {
  const story = storyById(storyId);
  if (!story) {
    $("detailView").innerHTML = `<div class="state-card">Project not found.
      <a href="#" onclick="location.hash='';return false;">Back to the feed</a></div>`;
    return;
  }
  const evs = storyFullEvents(story);
  const maxDollar = Math.max(...evs.map((e) => e.dollar_value ?? -1));
  const maxSqft = Math.max(...evs.map((e) => e.commercial_sqft ?? -1));
  const next = storyNextDate(story);
  const numbers = [
    story.total_units != null && { k: "Units", v: fmtNum(story.total_units) + (storyTenure(story) ? ` · ${storyTenure(story)}` : "") },
    maxSqft >= 0 && { k: "Sq ft", v: fmtNum(maxSqft) },
    maxDollar >= 0 && { k: "Stated value", v: "$" + fmtNum(maxDollar) },
    { k: "First seen", v: fmtDate(story.first_seen) },
    { k: "Latest", v: fmtDate(story.last_activity) },
    next && { k: "Next date", v: fmtDate(next) },
  ].filter(Boolean);
  const trades = [...storyTrades(story)];
  const contacts = collectContacts(evs);
  const timeline = evs.map((e) => `
    <div class="tl-item sg-${esc(e.stage || "informational")}">
      <div class="tl-date">${esc(fmtDate(e.date))} · ${esc(e.board || "")} ${stageBadge(e.stage)}</div>
      <div class="tl-body">${esc(e.summary)}</div>
      <div class="tl-src">${srcLinks(e)}</div>
    </div>`).join("");

  $("detailView").innerHTML = `
    <a class="backlink" href="#" onclick="location.hash='';return false;">← All signals</a>
    <h2 class="section-h" style="margin-top:0">${esc(story.name)}</h2>
    <div class="l1" style="margin-bottom:4px">
      ${stageBadge(story.current_stage)}
      <span class="tag">${esc(story.status)}</span>
      ${story.address ? `<span class="date-pill">${esc(story.address)}</span>` : ""}
      <span class="date-pill">${esc(story.town)}, MA</span>
    </div>
    <div class="numbers">${numbers.map((n) =>
      `<div><div class="k">${esc(n.k)}</div><div class="v">${esc(n.v)}</div></div>`).join("")}</div>
    ${briefBlock(story.brief)}
    ${trades.length ? `<div class="l3">${trades.map((t) =>
      `<span class="tag">${esc(TRADE_LABELS[t] || t)}</span>`).join("")}</div>` : ""}
    ${contacts.length ? `<h3 class="section-h" style="font-size:15px">Who's involved
      <span class="count">${contacts.length}</span></h3>
    <div class="contact-list">${contacts.map((c) => `<div class="c">
      <span class="role">${esc(c.role)}</span><br><b>${esc(c.name)}</b>${c.extra ? " — " + esc(c.extra) : ""}
      <span style="float:right;font-size:11px;color:var(--muted)">public record</span></div>`).join("")}</div>` : ""}
    <h3 class="section-h" style="font-size:15px">Timeline
      <span class="count">${evs.length} meeting(s)</span></h3>
    <div class="timeline">${timeline}</div>
    ${story.place_fips ? `<p style="margin-top:18px;font-size:13.5px">
      <a href="../output/towns/${esc(story.place_fips)}.html">Civica town report for ${esc(story.town)} →</a></p>` : ""}`;
}

/* ── Controls (mode switch, tabs, chips, filters panel) ───────────────── */
function buildTabs() {
  const tabs = VIEWS[STATE.mode || "contractor"];
  $("viewTabs").innerHTML = tabs.map(([key, label]) =>
    `<button type="button" role="tab" data-view="${key}"
       aria-selected="${STATE.view === key}">${esc(label)}</button>`).join("");
}
function buildChips() {
  const mode = STATE.mode || "contractor";
  let html = "";
  if (mode === "realtor") {
    html = Object.entries(HOUSING_CATS).map(([key, label]) =>
      `<button class="chip" type="button" data-chip="${key}"
         aria-pressed="${STATE.types.includes(key)}">${esc(label)}</button>`).join("");
  } else {
    const present = new Set();
    FEED.events.forEach((e) => (e.trades || []).forEach((t) => present.add(t)));
    html = [...present].sort().map((t) =>
      `<button class="chip" type="button" data-chip="${t}"
         aria-pressed="${STATE.trades.includes(t)}">${esc(TRADE_LABELS[t] || t)}</button>`).join("");
  }
  $("chipRow").innerHTML = html;
}
function syncModeButtons() {
  document.querySelectorAll(".mode-switch button").forEach((b) =>
    b.setAttribute("aria-pressed", b.dataset.mode === STATE.mode));
  $("modeHint").hidden = !!STATE.mode;  // first visit: neither pressed, hint invites a pick
}
function setMode(mode) {
  STATE.mode = mode;
  localStorage.setItem(MODE_KEY, mode);
  STATE.view = VIEWS[mode][0][0];
  writeState(STATE);
  syncModeButtons(); buildTabs(); buildChips(); renderFeed();
}
function buildControls() {
  syncModeButtons(); buildTabs(); buildChips();
  document.querySelector(".mode-switch").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (b) setMode(b.dataset.mode);
  });
  $("viewTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]");
    if (!b) return;
    STATE.view = b.dataset.view;
    writeState(STATE); buildTabs(); renderFeed();
  });
  $("chipRow").addEventListener("click", (e) => {
    const b = e.target.closest("[data-chip]");
    if (!b) return;
    const key = b.dataset.chip;
    const list = STATE.mode === "realtor" ? STATE.types : STATE.trades;
    const i = list.indexOf(key);
    i >= 0 ? list.splice(i, 1) : list.push(key);
    writeState(STATE); buildChips(); renderFeed();
  });
  $("filtersBtn").addEventListener("click", () => {
    const panel = $("filtersPanel");
    panel.hidden = !panel.hidden;
    $("filtersBtn").setAttribute("aria-expanded", String(!panel.hidden));
  });
  const stageSel = $("stageSel");
  STAGES.filter((s) => FEED.events.some((e) => e.stage === s)).forEach((s) => {
    const o = document.createElement("option");
    o.value = s; o.textContent = s.replace("_", " ");
    stageSel.appendChild(o);
  });
  stageSel.value = STATE.stage;
  $("qInput").value = STATE.q;
  $("fromDate").value = STATE.from;
  $("toDate").value = STATE.to;
  const syncPanel = () => {
    STATE.q = $("qInput").value.trim();
    STATE.stage = stageSel.value;
    STATE.from = $("fromDate").value;
    STATE.to = $("toDate").value;
    writeState(STATE); renderFeed();
  };
  ["change", "search"].forEach((evt) => $("qInput").addEventListener(evt, syncPanel));
  [stageSel, $("fromDate"), $("toDate")].forEach((el) => el.addEventListener("change", syncPanel));
  $("clearBtn").addEventListener("click", () => {
    Object.assign(STATE, { q: "", stage: "", from: "", to: "", trades: [], types: [] });
    $("qInput").value = ""; stageSel.value = ""; $("fromDate").value = ""; $("toDate").value = "";
    writeState(STATE); buildChips(); renderFeed();
  });
  $("controls").hidden = false;
}

/* ── Coverage, routing, boot ──────────────────────────────────────────── */
function renderCoverage() {
  const updated = FEED.generated_at
    ? `<span class="cov-chip">updated <b class="fresh">${esc(fmtDate(FEED.generated_at.slice(0, 10)))}</b></span>` : "";
  $("coverage").innerHTML = updated + FEED.coverage.map((c) =>
    `<span class="cov-chip"><b>${esc(c.name)}</b> · data <span class="fresh">${
      c.doc_freshness_days == null ? "—" : c.doc_freshness_days + "d"}</span> old</span>`).join("")
    + `<span class="cov-chip">expanding across the North Shore — 30 towns planned</span>`;
}
function route() {
  const m = location.hash.match(/^#story\/(.+)$/);
  $("detailView").hidden = !m;
  $("feedView").hidden = !!m;
  if (m && FEED) renderDetail(decodeURIComponent(m[1]));
  if (m) window.scrollTo(0, 0);
}
function showError(msg) {
  $("stateCard").hidden = false;
  $("stateCard").innerHTML = `<b>Signals data unavailable.</b><br>${esc(msg)}<br>
    If this persists, the feed may be rebuilding — try again shortly.`;
}
async function boot() {
  const access = checkAccess();          // the future 401 path lives here
  if (!access.authorized) { location.replace("login.html"); return; }
  const signoutLink = $("signoutLink");
  if (signoutLink) signoutLink.addEventListener("click", (e) => { e.preventDefault(); signOut(); });
  try {
    const resp = await fetch(`${DATA_BASE_URL}/feed.json`, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    FEED = await resp.json();
  } catch (err) {
    showError(String(err.message || err));
    return;
  }
  STATE = readState();
  if (STATE.mode) localStorage.setItem(MODE_KEY, STATE.mode);
  renderCoverage();
  buildControls();
  renderFeed();
  route();
}
window.addEventListener("hashchange", route);
boot();
