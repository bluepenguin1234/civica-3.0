/* Civica Signals dashboard — static shell, data fetched at runtime.
 *
 * ARCHITECTURE RULE: no event data is embedded in the page. Everything is
 * fetched from DATA_BASE_URL — the ONE place the data endpoint is configured.
 * Today it points at the static feed committed to the site; when the paywall
 * arrives, point it at the authenticated API and make checkAccess() read the
 * real session. Nothing else may hardcode data paths.
 */
"use strict";

const DATA_BASE_URL = "../output/signals";   // future: https://api.<host>/signals
const WAITLIST_EMAIL = "TODO-waitlist@example.com"; // placeholder until Formspree/ConvertKit is configured

/* The gating seam. ALL data access flows through here. */
function checkAccess() {
  return { authorized: true, tier: "free_preview" };
}

const TYPE_LABELS = {
  residential_project: "Residential", commercial_project: "Commercial",
  mixed_use_project: "Mixed-use", subdivision: "Subdivision",
  "40b_application": "40B", zoning_amendment: "Zoning change",
  variance_special_permit: "Variance / SP", tax_override_debt_exclusion: "Override",
  infrastructure_project: "Infrastructure", municipal_property: "Municipal property",
  master_plan_comp_plan: "Plans & studies", other_notable: "Other",
};
const STAGES = ["proposed", "hearing", "continued", "approved", "denied",
                "withdrawn", "permitted", "under_construction", "informational"];

let FEED = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US",
    { month: "short", day: "numeric", year: "numeric" });
};
const fmtNum = (n) => n == null ? null : Number(n).toLocaleString("en-US");

/* ── URL-persisted filter state ───────────────────────────────────────── */
function readFilters() {
  const q = new URLSearchParams(location.search);
  return {
    types: (q.get("types") || "").split(",").filter(Boolean),
    stage: q.get("stage") || "",
    from: q.get("from") || "",
    to: q.get("to") || "",
  };
}
function writeFilters(f) {
  const q = new URLSearchParams();
  if (f.types.length) q.set("types", f.types.join(","));
  if (f.stage) q.set("stage", f.stage);
  if (f.from) q.set("from", f.from);
  if (f.to) q.set("to", f.to);
  const qs = q.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
}

function applyFilters(events, f) {
  return events.filter((e) =>
    (!f.types.length || f.types.includes(e.event_type)) &&
    (!f.stage || e.stage === f.stage) &&
    (!f.from || (e.date && e.date >= f.from)) &&
    (!f.to || (e.date && e.date <= f.to)));
}

/* ── Shared render helpers ────────────────────────────────────────────── */
function stageBadge(stage) {
  if (!stage) return "";
  return `<span class="sbadge sg-${esc(stage)}">${esc(stage.replace("_", " "))}</span>`;
}
function typeBadge(t) {
  return `<span class="tbadge">${esc(TYPE_LABELS[t] || t)}</span>`;
}
function townLink(ev) {
  return ev.place_fips
    ? `<a href="../output/towns/${esc(ev.place_fips)}.html">${esc(ev.town)}, MA</a>`
    : esc(ev.town);
}
function contactsHtml(ev) {
  const bits = [];
  if (ev.applicant) bits.push(`<b>Applicant:</b> ${esc(ev.applicant)}`);
  if (Array.isArray(ev.applicant_reps) && ev.applicant_reps.length) {
    bits.push("<b>Reps:</b> " + ev.applicant_reps.map((r) =>
      esc([r.name, r.firm ? `(${r.firm})` : "", r.role ? `— ${r.role}` : ""]
        .filter(Boolean).join(" "))).join("; "));
  }
  if (ev.job_contact && (ev.job_contact.name || ev.job_contact.role)) {
    const j = ev.job_contact;
    bits.push(`<b>Contact for the work:</b> ${esc([j.role, j.name, j.org]
      .filter(Boolean).join(", "))}${j.contact_info ? " — " + esc(j.contact_info) : ""}`);
  }
  return bits.length ? `<div class="contacts">${bits.join("<br>")}</div>` : "";
}
function numFacts(ev) {
  const f = [];
  if (ev.residential_units != null) f.push(`${fmtNum(ev.residential_units)} units`);
  if (ev.commercial_sqft != null) f.push(`${fmtNum(ev.commercial_sqft)} sq ft`);
  if (ev.dollar_value != null) f.push(`$${fmtNum(ev.dollar_value)}`);
  return f.length
    ? `<div class="numfacts">${f.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : "";
}
function sourceLinks(ev) {
  const src = ev.source_url
    ? `<a href="${esc(ev.source_url)}" rel="noopener">Source document${ev.source_page ? ` (p.${esc(ev.source_page)})` : ""} ↗</a>` : "";
  return `<div class="srclinks">${src}<span>${townLink(ev)}</span></div>`;
}

function eventCard(ev, { showStory = true } = {}) {
  const story = showStory && ev.story_id ? storyById(ev.story_id) : null;
  const title = ev.project_name || (story && story.name) || ev.address ||
    (TYPE_LABELS[ev.event_type] || ev.event_type);
  const head = story && story.events.length > 1
    ? `<a href="#story/${esc(story.story_id)}">${esc(title)}</a>` : esc(title);
  return `<article class="scard">
    <div class="scard-top">
      <div><h3>${head}</h3>
        <div class="meta"><span class="mono">${esc(fmtDate(ev.date))}</span> · ${esc(ev.board || "")}${ev.address ? " · " + esc(ev.address) : ""}</div>
      </div>
      <div class="badges">${typeBadge(ev.event_type)}${stageBadge(ev.stage)}</div>
    </div>
    <p class="summary">${esc(ev.summary)}</p>
    ${numFacts(ev)}${contactsHtml(ev)}${sourceLinks(ev)}
  </article>`;
}

function storyCard(story) {
  const latest = story.events[story.events.length - 1];
  const fullEvents = story.events.map((se) => fullEventById(se.event_id) || se);
  const latestFull = fullEvents[fullEvents.length - 1];
  return `<article class="scard">
    <div class="scard-top">
      <div><h3><a href="#story/${esc(story.story_id)}">${esc(story.name)}</a></h3>
        <div class="meta">${esc(story.town)}, MA${story.address ? " · " + esc(story.address) : ""} ·
          <span class="mono">${esc(fmtDate(story.first_seen))} → ${esc(fmtDate(story.last_activity))}</span> ·
          ${story.events.length} meetings</div>
      </div>
      <div class="badges">${story.total_units ? `<span class="tbadge">${fmtNum(story.total_units)} units</span>` : ""}${stageBadge(story.current_stage)}</div>
    </div>
    <p class="summary">${esc(latest.summary)}</p>
    ${latestFull ? contactsHtml(latestFull) : ""}
    <div class="timeline">${story.events.map((se) => `
      <div class="tl-item sg-${esc(se.stage || "informational")}">
        <div class="tl-date">${esc(fmtDate(se.date))} · ${esc(se.board || "")} ${stageBadge(se.stage)}</div>
      </div>`).join("")}
    </div>
    <div class="srclinks"><a href="#story/${esc(story.story_id)}">Full project history →</a>
      ${latest.source_url ? `<a href="${esc(latest.source_url)}" rel="noopener">Latest source ↗</a>` : ""}</div>
  </article>`;
}

const storyById = (id) => FEED.stories.find((s) => s.story_id === id);
const fullEventById = (id) => FEED.events.find((e) => e.event_id === id);

/* ── Sections ─────────────────────────────────────────────────────────── */
function renderCoverage() {
  const updated = FEED.generated_at
    ? `<span class="cov-chip">updated <b class="fresh">${esc(fmtDate(FEED.generated_at.slice(0, 10)))}</b></span>` : "";
  $("coverage").innerHTML = updated + FEED.coverage.map((c) =>
    `<span class="cov-chip"><b>${esc(c.name)}</b> · data <span class="fresh">${
      c.doc_freshness_days == null ? "—" : c.doc_freshness_days + "d"}</span> old</span>`).join("")
    + `<span class="cov-chip">expanding across the North Shore — 30 towns planned</span>`;
}

function renderThisWeek(events) {
  const gen = (FEED.generated_at || "").slice(0, 10);
  const cutoff = new Date(gen || Date.now());
  cutoff.setDate(cutoff.getDate() - 7);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  const recent = events.filter((e) => e.date && e.date >= cutoffIso);

  let html = `<h2 class="section-h">This week <span class="count">${recent.length} item(s)</span></h2>`;
  if (!recent.length) {
    const latest = events.find((e) => e.date);
    html += `<div class="state-card">No new activity in the last 7 days.
      Most recent meeting on record: <b>${esc(fmtDate(latest && latest.date))}</b> —
      minutes often post weeks after a meeting; agendas are the early signal.</div>`;
    $("thisWeek").innerHTML = html;
    return;
  }
  const stageChanges = [], newStories = [], other = [];
  for (const ev of recent) {
    const story = ev.story_id && storyById(ev.story_id);
    if (story && story.events.length > 1 && story.events[0].event_id !== ev.event_id) {
      stageChanges.push(ev);
    } else if (story && story.events[0].event_id === ev.event_id) {
      newStories.push(ev);
    } else other.push(ev);
  }
  html += [...stageChanges, ...newStories, ...other].map((e) => eventCard(e)).join("");
  $("thisWeek").innerHTML = html;
}

function renderFeed() {
  const f = readFilters();
  const events = applyFilters(FEED.events, f);
  renderThisWeek(events);

  const storyIds = new Set(events.map((e) => e.story_id).filter(Boolean));
  const stories = FEED.stories.filter((s) => storyIds.has(s.story_id) && s.events.length > 1)
    .sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));
  $("stories").innerHTML = stories.length
    ? `<h2 class="section-h">Project stories <span class="count">${stories.length}</span></h2>`
      + stories.map(storyCard).join("") : "";

  const inStories = new Set();
  stories.forEach((s) => s.events.forEach((se) => inStories.add(se.event_id)));
  const standalone = events.filter((e) => !inStories.has(e.event_id));
  $("standalone").innerHTML = standalone.length
    ? `<h2 class="section-h">All activity <span class="count">${standalone.length}</span></h2>`
      + standalone.map((e) => eventCard(e)).join("") : "";

  const empty = !stories.length && !standalone.length;
  $("stateCard").hidden = !empty;
  if (empty) $("stateCard").textContent =
    "No events match these filters. Clear them to see everything we track.";
}

function renderDetail(storyId) {
  const story = storyById(storyId);
  if (!story) {
    $("detailView").innerHTML = `<div class="state-card">Project not found.
      <a href="#" onclick="location.hash=''">Back to the feed</a></div>`;
    return;
  }
  const cards = story.events.map((se) => {
    const full = fullEventById(se.event_id);
    return full ? eventCard(full, { showStory: false })
                : `<article class="scard"><p class="summary">${esc(se.summary)}</p></article>`;
  }).join("");
  $("detailView").innerHTML = `
    <a class="backlink" href="#" onclick="location.hash='';return false;">← All signals</a>
    <h2 class="section-h" style="margin-top:0">${esc(story.name)}</h2>
    <div class="meta" style="margin-bottom:6px">${esc(story.town)}, MA${story.address ? " · " + esc(story.address) : ""} ·
      first seen <span class="mono">${esc(fmtDate(story.first_seen))}</span> ·
      latest <span class="mono">${esc(fmtDate(story.last_activity))}</span></div>
    <div class="badges" style="margin-bottom:14px">${stageBadge(story.current_stage)}
      ${story.total_units ? `<span class="tbadge">${fmtNum(story.total_units)} units</span>` : ""}
      <span class="tbadge">${esc(story.status)}</span></div>
    ${cards}`;
}

/* ── Filters UI ───────────────────────────────────────────────────────── */
function buildFilters() {
  const f = readFilters();
  const present = new Set(FEED.events.map((e) => e.event_type));
  $("typeChips").innerHTML = [...present].sort().map((t) =>
    `<button class="chip" type="button" data-type="${esc(t)}"
       aria-pressed="${f.types.includes(t)}">${esc(TYPE_LABELS[t] || t)}</button>`).join("");
  $("typeChips").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-type]");
    if (!btn) return;
    btn.setAttribute("aria-pressed", btn.getAttribute("aria-pressed") !== "true");
    syncFilters();
  });
  const stageSel = $("stageSel");
  STAGES.filter((s) => FEED.events.some((e) => e.stage === s)).forEach((s) => {
    const o = document.createElement("option");
    o.value = s; o.textContent = s.replace("_", " ");
    stageSel.appendChild(o);
  });
  stageSel.value = f.stage;
  $("fromDate").value = f.from;
  $("toDate").value = f.to;
  ["stageSel", "fromDate", "toDate"].forEach((id) =>
    $(id).addEventListener("change", syncFilters));
  $("clearBtn").addEventListener("click", () => {
    writeFilters({ types: [], stage: "", from: "", to: "" });
    buildFiltersState({ types: [], stage: "", from: "", to: "" });
    renderFeed();
  });
  $("filters").hidden = false;
}
function buildFiltersState(f) {
  document.querySelectorAll("#typeChips .chip").forEach((c) =>
    c.setAttribute("aria-pressed", f.types.includes(c.dataset.type)));
  $("stageSel").value = f.stage; $("fromDate").value = f.from; $("toDate").value = f.to;
}
function syncFilters() {
  const types = [...document.querySelectorAll('#typeChips [aria-pressed="true"]')]
    .map((c) => c.dataset.type);
  writeFilters({ types, stage: $("stageSel").value,
                 from: $("fromDate").value, to: $("toDate").value });
  renderFeed();
}

/* ── Routing + banner + boot ──────────────────────────────────────────── */
function route() {
  const m = location.hash.match(/^#story\/(.+)$/);
  $("detailView").hidden = !m;
  $("feedView").hidden = !!m;
  if (m && FEED) renderDetail(decodeURIComponent(m[1]));
  if (m) window.scrollTo(0, 0);
}

function setupBanner(access) {
  const banner = $("previewBanner");
  if (access.tier === "free_preview") {
    $("tierLabel").textContent = "Free preview";
    banner.hidden = false;
    $("waitlistForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const email = $("waitlistEmail").value.trim();
      // Placeholder channel until Formspree/ConvertKit is configured (see TODO.md):
      location.href = `mailto:${WAITLIST_EMAIL}?subject=${encodeURIComponent("Civica Signals founding waitlist")}` +
        `&body=${encodeURIComponent(`Add me to the founding-member waitlist: ${email}`)}`;
    });
  }
}

function showError(msg) {
  $("stateCard").hidden = false;
  $("stateCard").innerHTML = `<b>Signals data unavailable.</b><br>${esc(msg)}<br>
    If this persists, the feed may be rebuilding — try again shortly.`;
}

async function boot() {
  const access = checkAccess();          // the future 401 path lives here
  setupBanner(access);
  if (!access.authorized) { showError("Sign-in required."); return; }
  try {
    const resp = await fetch(`${DATA_BASE_URL}/feed.json`, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    FEED = await resp.json();
  } catch (err) {
    showError(String(err.message || err));
    return;
  }
  renderCoverage();
  buildFilters();
  renderFeed();
  route();
}

window.addEventListener("hashchange", route);
boot();
