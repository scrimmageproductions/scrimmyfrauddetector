/* Scrimmy Forensic Console (hosted version).
 *
 * The API is stateless (see scrimmy/service.py): this script drives a scan step by step
 * (search -> enrich each recipient -> score -> optional site checks) and keeps the
 * results, including raw response bytes for the evidence archive, in memory.
 * Every value that comes from an API is escaped before it reaches the DOM.
 */
"use strict";

const STATES = ["AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","PR","GU","VI","AS","MP"];
const FLOORS = [["$100K", 100000], ["$250K", 250000], ["$500K", 500000], ["$1M", 1000000], ["$2.5M", 2500000], ["$5M+", 5000000]];
const VIEWS = ["Strike Force Scanner", "Lead Inspector", "Anomaly Matrix", "Dossier Export"];
// Short investigator-facing labels; dossiers use the neutral rule titles.
const PILLS = {
  MATHEMATICAL_IMPOSSIBILITY: ["Capacity Impossibility", "red"], LEIE_EXCLUSION_MATCH: ["LEIE Exclusion Match", "red"],
  PHANTOM_HEALTHCARE_PROVIDER: ["Missing NPI Provider", "orange"], HIGH_DOLLAR_RESIDENTIAL_ZONE: ["Residential / Mail Drop", "orange"],
  PHYSICAL_SITE_MISMATCH: ["Site Mismatch", "orange"], NEWLY_FORMED_WINDFALL: ["Newly Formed", "yellow"],
  CO_LOCATED_SHELL_CLUSTER: ["Co-located Cluster", "purple"], EXECUTIVE_SERIAL_REGISTRATION: ["Serial Officer", "indigo"],
  UNRESOLVED_PHYSICAL_DISCREPANCY: ["Unresolved Address", "gray"],
};
const MATRIX_COLS = [["LEIE_EXCLUSION_MATCH", "LEIE"], ["MATHEMATICAL_IMPOSSIBILITY", "Capacity"], ["PHANTOM_HEALTHCARE_PROVIDER", "NPI"],
  ["HIGH_DOLLAR_RESIDENTIAL_ZONE", "Residential"], ["PHYSICAL_SITE_MISMATCH", "Site"], ["NEWLY_FORMED_WINDFALL", "New entity"],
  ["CO_LOCATED_SHELL_CLUSTER", "Co-location"], ["EXECUTIVE_SERIAL_REGISTRATION", "Officers"], ["UNRESOLVED_PHYSICAL_DISCREPANCY", "Address"]];
const ENRICH_CONCURRENCY = 2; // be polite to public APIs: each function instance throttles itself, the browser caps fan-out

const S = {
  caps: null, view: VIEWS[0], selected: null, running: false, opts: null,
  verticals: new Set(), profile: "registry",
  scan: null, // {entities: Map key->{entity, enrichment, error}, records: Map id->bundle, results, network, awardsScanned, failedGroups, dossiers: Map}
};

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => x >= 1e6 ? `$${(x / 1e6).toFixed(2)}M` : x >= 1e3 ? `$${Math.round(x / 1e3)}K` : `$${Math.round(x)}`;
const dollars = (x) => `$${Number(x || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/* ------------------------------------------------------------------ API */

async function api(path, body, asBlob = false) {
  const headers = { "Content-Type": "application/json" };
  const token = $("token").value.trim();
  if (token) headers["X-Scrimmy-Token"] = token;
  const res = await fetch(`/api/${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch (_) { /* non-JSON error page */ }
    throw new Error(`${path}: ${detail}`);
  }
  return asBlob ? res.blob() : res.json();
}

/* ------------------------------------------------------------- Controls */

function segmented(el, options, current, onPick, role = "radio") {
  el.innerHTML = "";
  for (const [label, value] of options) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.setAttribute("role", role);
    b.setAttribute(role === "tab" ? "aria-selected" : "aria-checked", String(value === current));
    b.addEventListener("click", () => onPick(value));
    el.appendChild(b);
  }
}

function renderControls() {
  segmented($("profile"), [["Registry", "registry"], ["Capacity", "capacity"]], S.profile, (v) => { S.profile = v; renderControls(); });
  segmented($("views"), VIEWS.map((v) => [v, v]), S.view, (v) => { S.view = v; renderControls(); render(); }, "tab");
  const chips = $("verticals");
  chips.innerHTML = "";
  for (const key of Object.keys(S.caps.verticals)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = shortLabel(key);
    b.title = S.caps.verticals[key];
    b.setAttribute("aria-pressed", String(S.verticals.has(key)));
    b.addEventListener("click", () => { S.verticals.has(key) ? S.verticals.delete(key) : S.verticals.add(key); renderControls(); });
    chips.appendChild(b);
  }
}

function shortLabel(key) {
  return { childcare: "Child care & nutrition", home_health: "Home health", hospice: "Hospice", clinics: "Clinics", healthcare: "Healthcare",
           behavioral_health: "Behavioral", food_nutrition: "Nutrition", housing: "Housing" }[key] || key;
}

function syncPreset() {
  const key = $("preset").value, p = S.caps.strike_forces[key];
  $("customGeo").hidden = Boolean(p);
  $("presetHint").innerHTML = p ? `${esc(p.metros.join(", "))}<br>${esc(p.districts.join("; "))}` : "Choose states, or scan nationwide.";
  $("states").disabled = $("nationwide").checked;
}

function initControls() {
  $("preset").innerHTML = Object.entries(S.caps.strike_forces).map(([k, p]) => `<option value="${esc(k)}">${esc(p.label)}</option>`).join("")
    + `<option value="custom">Custom / Nationwide</option>`;
  $("preset").addEventListener("change", syncPreset);
  $("nationwide").addEventListener("change", syncPreset);
  $("states").innerHTML = STATES.map((s) => `<option${s === "MN" ? " selected" : ""}>${s}</option>`).join("");
  const syncFloor = () => { $("floorOut").textContent = FLOORS[Number($("floor").value)][0]; };
  $("floor").addEventListener("input", syncFloor); syncFloor();
  syncPreset();
  const now = new Date();
  const fy = now.getFullYear() + (now.getMonth() >= 9 ? 1 : 0);
  $("fy").innerHTML = `<option value="">All years</option>` + Array.from({ length: fy - 2007 }, (_, i) => fy - i)
    .map((y, i) => `<option value="${y}"${i === 1 ? " selected" : ""}>FY${y}</option>`).join(""); // default: last complete FY
  for (const [id, out] of [["maxAwards", "maxAwardsOut"], ["threshold", "thresholdOut"]]) {
    const sync = () => { $(out).textContent = $(id).value; };
    $(id).addEventListener("input", sync); sync();
  }
  S.verticals = new Set(S.caps.priority_verticals);
  for (const p of S.caps.llm) $("llm").insertAdjacentHTML("beforeend", `<option value="${esc(p)}">${esc(p)} (prose only)</option>`);
  if (!S.caps.vision) {
    $("useVision").disabled = true; $("visionToggle").classList.add("disabled");
    $("visionHint").textContent = "Needs MAPILLARY_TOKEN or GOOGLE_MAPS_API_KEY plus ANTHROPIC_API_KEY or OPENAI_API_KEY in the deployment's environment.";
  }
  $("tokenRow").hidden = !S.caps.access_token_required;
  $("run").addEventListener("click", runScan);
  $("menuBtn").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  renderControls();
}

function collectOptions() {
  let licenseSources = [];
  const raw = $("licenseSources").value.trim();
  if (raw) {
    const parsed = JSON.parse(raw);
    licenseSources = Array.isArray(parsed) ? parsed : parsed.sources || [];
  }
  const preset = $("preset").value;
  const custom = preset === "custom";
  const nationwide = custom && $("nationwide").checked;
  return {
    strike_force: custom ? null : preset, nationwide,
    states: custom && !nationwide ? [...$("states").selectedOptions].map((o) => o.value) : [],
    verticals: [...S.verticals], min_award: FLOORS[Number($("floor").value)][1],
    fiscal_year: $("fy").value ? Number($("fy").value) : null, max_awards: Number($("maxAwards").value),
    threshold: Number($("threshold").value), scoring_profile: S.profile, use_osm: $("useOsm").checked,
    leie: $("useLeie").checked, vision: $("useVision").checked, llm_provider: $("llm").value, license_sources: licenseSources,
  };
}

/* ----------------------------------------------------------------- Scan */

function progress(text, done, total) {
  $("progress").hidden = false;
  $("progressText").textContent = text;
  $("progressCount").textContent = total ? `${done} / ${total}` : "";
  $("progressBar").style.width = `${total ? Math.round((100 * done) / total) : 5}%`;
}

function alerts(list) {
  $("alerts").innerHTML = list.map(([kind, msg]) => `<div class="alert ${kind}">${esc(msg)}</div>`).join("");
}

function addRecords(bundles) { for (const b of bundles) S.scan.records.set(b.record.record_id, b); }

async function pool(items, limit, fn) {
  let i = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) { const idx = i++; await fn(items[idx], idx); }
  });
  await Promise.all(workers);
}

function itemsPayload() {
  return [...S.scan.entities.values()].map(({ entity, enrichment }) => ({ entity, enrichment }));
}

function slimRecords() {
  return [...S.scan.records.values()].map(({ record }) => ({ ...record, raw_snippet: null, request_body: null }));
}

async function score() {
  const res = await api("score", { options: S.opts, items: itemsPayload(), records: slimRecords() });
  S.scan.results = res.results; S.scan.network = res.network;
  return res;
}

async function runScan() {
  if (S.running) return;
  if (!S.verticals.size) { alerts([["warn", "Choose at least one vertical."]]); return; }
  let opts;
  try { opts = collectOptions(); } catch (e) { alerts([["err", `Licensing sources JSON is invalid: ${e.message}`]]); return; }
  S.running = true; $("run").disabled = true; $("sidebar").classList.remove("open");
  S.opts = opts; S.selected = null;
  S.scan = { entities: new Map(), records: new Map(), results: [], network: {}, awardsScanned: 0, failedGroups: {}, dossiers: new Map(), enrichErrors: 0, partial: 0, skipped: {}, done: false };
  alerts([]); render();
  try {
    progress("Querying USASpending.gov awards…", 0, 0);
    const found = await api("search", opts);
    addRecords(found.records);
    S.scan.awardsScanned = found.awards_scanned; S.scan.failedGroups = found.failed_groups;
    if (found.all_failed) throw new Error("Every USASpending request failed. The data source may be down; try again shortly.");
    for (const e of found.entities) S.scan.entities.set(e.entity_key, { entity: e, enrichment: undefined });

    const keys = [...S.scan.entities.keys()];
    let done = 0;
    await pool(keys, ENRICH_CONCURRENCY, async (key) => {
      const slot = S.scan.entities.get(key);
      try {
        const r = await api("enrich", { options: opts, entity: slot.entity });
        slot.entity = r.entity; slot.enrichment = r.enrichment; addRecords(r.records);
        if (r.data_gaps?.length) S.scan.partial++;
      } catch (e) {
        // Keep the recipient: score it on the award-row data from the search (UEI, name, address, amounts).
        // Rules that needed the failed lookups report "insufficient data" instead of being silently lost.
        slot.error = e.message; S.scan.enrichErrors++;
        slot.entity = { ...slot.entity, data_gaps: [...(slot.entity.data_gaps || []), `enrichment request failed: ${e.message}`] };
        slot.enrichment = structuredClone(S.caps.empty_enrichment);
        slot.enrichment.notes.push(`Enrichment request failed (${e.message}); scored on award data only.`);
      }
      progress(`Cross-referencing ${slot.entity.name}`, ++done, keys.length);
    });
    // Only recipients without core award financials are left out (the server reports them as "skipped").

    progress("Building entity graph and scoring…", keys.length, keys.length);
    const res = await score();
    for (const name of Object.keys(res.skipped || {})) {
      for (const [k, v] of S.scan.entities) if (v.entity.name === name && !v.entity.awards.some((a) => a.amount > 0)) S.scan.entities.delete(k);
    }
    S.scan.skipped = res.skipped || {};
    if (res.site_check_candidates.length) {
      let n = 0;
      for (const key of res.site_check_candidates) {
        progress(`Street-level site check: ${S.scan.entities.get(key).entity.name}`, n++, res.site_check_candidates.length);
        try {
          const slot = S.scan.entities.get(key);
          const r = await api("site-check", { options: opts, entity: slot.entity, enrichment: slot.enrichment });
          slot.enrichment = r.enrichment; addRecords(r.records);
        } catch (e) { S.scan.enrichErrors++; }
      }
      await score();
    }
    S.scan.done = true;
    const warn = [];
    if (Object.keys(S.scan.failedGroups).length) warn.push(["warn", `Some award searches failed, so results are incomplete: ${Object.keys(S.scan.failedGroups).join(", ")}.`]);
    if (S.scan.enrichErrors) warn.push(["warn", `${S.scan.enrichErrors} recipient lookup(s) failed; those recipients were scored on their award data alone, and rules that needed the missing lookups show "insufficient data".`]);
    else if (S.scan.partial) warn.push(["warn", `${S.scan.partial} recipient(s) had partial lookups (see "Data gaps" in the Lead Inspector); affected rules show "insufficient data".`]);
    const nSkipped = Object.keys(S.scan.skipped || {}).length;
    if (nSkipped) warn.push(["warn", `${nSkipped} recipient(s) had no award with a positive amount and were not scored.`]);
    alerts(warn);
  } catch (e) {
    alerts([["err", e.message]]);
  } finally {
    S.running = false; $("run").disabled = false; $("progress").hidden = true; render();
  }
}

/* -------------------------------------------------------------- Helpers */

function ring(score, size = 58) {
  const color = score >= 75 ? "var(--red)" : score >= 50 ? "var(--orange)" : score >= 25 ? "var(--yellow)" : "var(--gray)";
  const r = size / 2 - 5, c = 2 * Math.PI * r, dash = (c * score) / 100;
  return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="score ${score}">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--fill)" stroke-width="6"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"
      stroke-dasharray="${dash.toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})"/>
    <text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-size="${Math.round(size * 0.3)}">${score}</text></svg>`;
}

function pills(scoreObj) {
  const out = scoreObj.evaluations.filter((ev) => ev.status === "triggered" && ev.points_awarded > 0).map((ev) => {
    const [label, color] = PILLS[ev.rule_id] || [ev.title, "gray"];
    return `<span class="pill ${color}">${esc(label)} +${ev.points_awarded}</span>`;
  });
  return out.join("") || `<span class="pill gray">No scored indicators</span>`;
}

function empty(icon, title, sub) {
  return `<div class="card empty"><div class="empty-icon">${icon}</div><div class="empty-title">${esc(title)}</div><div class="empty-sub">${esc(sub)}</div></div>`;
}

function card(html, extra = "") { return `<div class="card ${extra}">${html}</div>`; }

function oneLine(a) {
  const tail = [a.city, [a.state, a.zip5].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  return [a.line1, a.line2, tail].filter(Boolean).join(", ");
}

function rows() {
  return S.scan.results.map((r) => ({ ...r, ...S.scan.entities.get(r.entity_key) })).filter((r) => r.entity);
}

function leads() { return rows().filter((r) => r.score.score >= S.opts.threshold); }

function sourceIds(entity, enr, scoreObj) {
  const ids = new Set([...(entity.source_record_ids || []), ...(entity.formation_source_record_ids || [])]);
  for (const a of entity.awards) a.source_record_ids.forEach((x) => ids.add(x));
  for (const o of entity.officers) o.source_record_ids.forEach((x) => ids.add(x));
  for (const part of ["npi", "geocode", "land_use", "leie", "license"]) (enr[part]?.source_record_ids || []).forEach((x) => ids.add(x));
  (enr.license?.best_match?.source_record_ids || []).forEach((x) => ids.add(x));
  (enr.vision?.imagery?.source_record_ids || []).forEach((x) => ids.add(x));
  for (const ev of scoreObj.evaluations) for (const item of ev.evidence) if (item.source_record_id) ids.add(item.source_record_id);
  return ids;
}

function leadPayload(row) {
  const ids = sourceIds(row.entity, row.enrichment, row.score);
  const records = [...ids].map((id) => S.scan.records.get(id)).filter(Boolean);
  return { options: S.opts, items: itemsPayload(), records, entity_key: row.entity_key };
}

async function getDossier(row) {
  if (!S.scan.dossiers.has(row.entity_key)) S.scan.dossiers.set(row.entity_key, await api("dossier", leadPayload(row)));
  return S.scan.dossiers.get(row.entity_key);
}

function download(name, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function selector(onChange) {
  const all = rows();
  if (!S.selected || !all.find((r) => r.entity_key === S.selected)) S.selected = all[0]?.entity_key;
  const opts = all.map((r) => `<option value="${esc(r.entity_key)}"${r.entity_key === S.selected ? " selected" : ""}>${r.score.score} · ${esc(r.entity.name)}</option>`).join("");
  setTimeout(() => $("recipient")?.addEventListener("change", (e) => { S.selected = e.target.value; onChange(); }));
  return `<div class="toolbar"><select id="recipient" class="input" aria-label="Recipient">${opts}</select></div>`;
}

/* ----------------------------------------------------------------- Views */

function render() {
  const v = $("view");
  if (!S.scan) { v.innerHTML = empty("◎", "Ready to scan", "Pick a Strike Force preset, verticals and an award floor, then run a live scan."); return; }
  if (S.running && !S.scan.done) { v.innerHTML = empty("◌", "Scan in progress", "Results appear when scoring finishes."); return; }
  if (!S.scan.done) { v.innerHTML = ""; return; }
  ({ "Strike Force Scanner": viewScanner, "Lead Inspector": viewInspector, "Anomaly Matrix": viewMatrix, "Dossier Export": viewExport })[S.view](v);
}

function viewScanner(v) {
  const all = rows();
  const top = Math.max(0, ...all.map((r) => r.score.score));
  const metrics = [["Awards scanned", S.scan.awardsScanned], ["Recipients screened", all.length], ["Anomalies flagged", leads().length], ["Highest discrepancy score", top]];
  let html = `<div class="grid4">${metrics.map(([l, x]) => card(`<div class="metric-label">${l}</div><div class="metric-value">${Number(x).toLocaleString()}</div>`)).join("")}</div>`;
  if (!all.length) { v.innerHTML = html + empty("◎", "No awards matched these filters", "Try another state, fiscal year or a lower award floor."); return; }
  const showAll = v.dataset.showAll === "1";
  html += `<label class="toggle"><input type="checkbox" id="showAll"${showAll ? " checked" : ""}><span class="track"></span>Show recipients below threshold</label>`;
  const list = showAll ? all : leads();
  if (!list.length) {
    html += empty("✓", "No anomalies found matching current threshold", `${all.length} recipients were screened against live records. None scored ${S.opts.threshold} or higher.`);
  } else {
    html += `<h2 class="section">${showAll ? "All recipients" : "Anomalies flagged"}</h2>`;
    for (const r of list) {
      const e = r.entity;
      html += `<div class="card lead" data-key="${esc(r.entity_key)}" tabindex="0" role="button" aria-label="Inspect ${esc(e.name)}">${ring(r.score.score)}
        <div class="lead-main"><div class="lead-name">${esc(e.name)}</div>
        <div class="lead-meta">${esc(oneLine(e.address) || "No address published")} · UEI ${esc(e.uei || "n/a")} · ${e.awards.length} award${e.awards.length === 1 ? "" : "s"}</div>
        ${r.enrichment.strike_force?.district ? `<div class="district">${esc(r.enrichment.strike_force.district)}</div>` : ""}
        <div>${pills(r.score)}</div></div>
        <div><div class="amount">${money(e.total_awarded)}</div><div class="amount-sub">awarded in scope</div></div></div>`;
    }
  }
  v.innerHTML = html;
  $("showAll").addEventListener("change", (e) => { v.dataset.showAll = e.target.checked ? "1" : "0"; viewScanner(v); });
  v.querySelectorAll(".lead").forEach((el) => {
    const go = () => { S.selected = el.dataset.key; S.view = "Lead Inspector"; renderControls(); render(); };
    el.addEventListener("click", go);
    el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
  });
}

function viewInspector(v) {
  if (!rows().length) { v.innerHTML = empty("◎", "Nothing to inspect", "The scan returned no recipients."); return; }
  const head = selector(() => viewInspector(v));
  const r = rows().find((x) => x.entity_key === S.selected);
  const e = r.entity, enr = r.enrichment, s = r.score;
  const npi = enr.npi?.best_match;
  const kv = [
    ["UEI / CAGE", `${e.uei || "n/a"} / ${e.cage || "n/a"}`],
    ["Organizational NPI", npi ? `${npi.npi} (${npi.status})` : enr.npi?.performed ? "none matched" : "not checked"],
    ["Total awarded", `${dollars(e.total_awarded)} · ${e.awards.length} award(s)`],
    ["Strike Force district", enr.strike_force ? (enr.strike_force.district || "outside preset districts") : "no preset"],
    ["County / tract", `${enr.geocode.county_name || "n/a"} / ${enr.geocode.census_tract || "n/a"}`],
    ["Formation date", e.formation_date || "unknown"],
    ["People on record", e.officers.map((o) => o.name).join("; ") || "none published"],
    ["Land use (OSM)", enr.land_use.classification],
    ["LEIE", enr.leie.performed ? `${enr.leie.matches.length} candidate(s)` : "not screened"],
    ["NPPES lookup", enr.npi?.performed ? (enr.npi.stages || []).join(" → ") || "performed" : "not applicable"],
    ["Data gaps", (e.data_gaps || []).join("; ") || "none"],
  ];
  const cap = enr.capacity;
  const capHtml = cap.performed
    ? `<div class="grid4" style="grid-template-columns:repeat(3,1fr)">${[["Implied daily beneficiaries", cap.estimated_daily_beneficiaries.toFixed(1)], [cap.capacity_basis === "state_license" ? "Licensed capacity" : "Facility-type ceiling", cap.licensed_capacity], ["Ratio (threshold " + cap.ratio_threshold + "x)", cap.capacity_ratio.toFixed(2) + "x"]].map(([l, x]) => card(`<div class="metric-label">${esc(l)}</div><div class="metric-value">${esc(x)}</div>`)).join("")}</div>
       <div class="code">${esc(cap.formula)}</div><p class="hint">${esc(cap.benchmark_label)}. ${esc(cap.benchmark_citation)}</p>`
    : `<p>${esc(cap.reason_not_performed || "Not computed.")}</p>`;
  const vis = enr.vision;
  const siteHtml = (enr.land_use.signals || []).map((x) => `<p>• ${esc(x)}</p>`).join("") +
    (vis?.assessment ? `<p><b>Vision (${esc(vis.model)})</b>: <code>${esc(vis.assessment.category)}</code>, confidence ${vis.assessment.confidence.toFixed(2)}. Markers: ${esc(vis.assessment.physical_markers.join(", ") || "none")}</p>
       <p class="hint">${esc(vis.imagery.provider)} ${esc(vis.imagery.image_id)} · captured ${esc(vis.imagery.captured_at)} · ${esc(vis.imagery.license_note)}</p>`
      : `<p class="hint">Vision check: ${esc(vis?.error || "not run")}</p>`);
  const ids = [...sourceIds(e, enr, s)];
  const payloads = ids.map((id) => S.scan.records.get(id)).filter(Boolean).map(({ record: rec }) =>
    `<details class="panel"><summary><code>${esc(rec.record_id)}</code> · ${esc(rec.source)} · ${esc(rec.method)} ${esc(rec.url.slice(0, 90))}</summary>
     <div class="body"><p class="hint">Retrieved ${esc(rec.retrieved_at)} · HTTP ${esc(rec.status_code ?? rec.error)} · SHA-256 ${esc((rec.response_sha256 || "").slice(0, 16))}…</p>
     <div class="code">${esc(JSON.stringify(rec.raw_snippet, null, 2))}</div></div></details>`).join("");

  v.innerHTML = head + `
    <div class="grid2">
      <div>${card(`<div class="lead">${ring(s.score, 76)}<div class="lead-main"><div class="lead-name" style="font-size:21px">${esc(e.name)}</div><div class="lead-meta">${esc(oneLine(e.address))}</div><div>${pills(s)}</div></div></div>`)}
        ${card(`<h2 class="section" style="margin-top:0">Identity</h2><dl class="kv">${kv.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join("")}</dl>`)}</div>
      <div>${enr.geocode.matched ? `<div id="map"></div><p class="hint">Census match: ${esc(enr.geocode.matched_address)} (${enr.geocode.latitude.toFixed(5)}, ${enr.geocode.longitude.toFixed(5)})</p>`
        : empty("⌖", "Address not geocoded", enr.geocode.error || "The Census Geocoder returned no match for the address of record.")}</div>
    </div>
    <h2 class="section">Deterministic score breakdown</h2>
    ${card(`<table class="rules"><thead><tr><th>Indicator</th><th>Status</th><th>Weight</th><th>Points</th><th>Rationale</th></tr></thead><tbody>
      ${s.evaluations.map((ev) => `<tr><td><b>${esc(ev.title)}</b><br><span class="hint">${esc(ev.rule_id)}</span></td>
        <td class="${ev.status === "triggered" && ev.points_awarded ? "status-triggered" : ""}">${esc(ev.status.replace(/_/g, " "))}</td>
        <td class="num">${ev.weight}</td><td class="num">${ev.points_awarded}</td><td>${esc(ev.rationale)}</td></tr>`).join("")}
      </tbody></table><div class="code" style="margin-top:12px">${esc(s.formula)}</div>`)}
    <details class="panel" open><summary>Mathematical breakdown</summary><div class="body">${capHtml}</div></details>
    <details class="panel"><summary>Physical site verification</summary><div class="body">${siteHtml}</div></details>
    <details class="panel"><summary>Raw API payloads (chain of custody)</summary><div class="body">${payloads || "<p>None.</p>"}</div></details>
    <details class="panel"><summary>Network: shared addresses and officers</summary><div class="body">${networkHtml(r.entity_key)}</div></details>
    <details class="panel" open><summary>2-minute physical verification checklist</summary><div class="body">
      ${S.caps.quick_checklist.map((item, i) => `<label class="check"><input type="checkbox" id="q${i}"><span>${esc(item)}</span></label>`).join("")}</div></details>
    <details class="panel" id="checklistPanel"><summary>Full ground-truthing checklist</summary><div class="body"><p class="hint">Loading checklist…</p></div></details>
    <div class="export-row" style="margin-top:14px">${exportButtonsHtml(0)}</div>
    <details class="panel" id="briefingPanel"><summary>Briefing preview</summary><div class="body"><p class="hint">Loading…</p></div></details>`;

  if (enr.geocode.matched && window.L) {
    const map = L.map("map", { scrollWheelZoom: false }).setView([enr.geocode.latitude, enr.geocode.longitude], 17);
    // CARTO basemaps: tile.openstreetmap.org blocks embedded/referer-less use (osm.wiki/Blocked). Positron in light mode,
    // Dark Matter in dark mode, both monochrome so the red site marker stays the only accent.
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    L.tileLayer(`https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`, {
      subdomains: "abcd", maxZoom: 20,
      attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    L.circleMarker([enr.geocode.latitude, enr.geocode.longitude], { radius: 9, color: "#FF3B30", fillOpacity: 0.6 }).addTo(map);
  }
  drawGraph(r.entity_key);
  wireExports(v, [r]);
  getDossier(r).then((d) => {
    if (S.selected !== r.entity_key || !$("checklistPanel")) return;
    $("checklistPanel").querySelector(".body").innerHTML = d.dossier.ground_truthing_checklist
      .map((item, i) => `<label class="check"><input type="checkbox" id="chk${i}"><span>${esc(item)}</span></label>`).join("") +
      `<p class="hint">Follow docs/FIELD_INVESTIGATOR_SOP.md. Public records and public right-of-way observation only.</p>`;
    $("briefingPanel").querySelector(".body").innerHTML = `<div class="briefing">${DOMPurify.sanitize(marked.parse(d.markdown))}</div>`;
  }).catch((err) => {
    if ($("checklistPanel")) $("checklistPanel").querySelector(".body").innerHTML = `<div class="alert err">${esc(err.message)}</div>`;
  });
}

function networkHtml(key) {
  const n = S.scan.network[key];
  if (n.component_entities.length <= 1 && !n.shared_people.length) return `<p class="hint">This recipient shares no address or officer with another awardee in the scan.</p>`;
  return `<div class="graph" id="graph"><p class="hint">Rendering graph…</p></div>
    ${n.shared_addresses.map((a) => `<p><b>Shared address</b> ${esc(a.address)}: ${esc(a.entities.join("; "))}</p>`).join("")}
    ${n.shared_people.map((p) => `<p><b>Shared person</b> ${esc(p.person)}: ${esc(p.entities.join("; "))}</p>`).join("")}`;
}

function drawGraph(key) {
  const n = S.scan.network[key];
  if (!$("graph")) return;
  const draw = () => Viz.instance().then((viz) => {
    const g = $("graph"); if (g) { g.innerHTML = ""; g.appendChild(viz.renderSVGElement(n.dot)); }
  }).catch((err) => { const g = $("graph"); if (g) g.innerHTML = `<div class="alert err">${esc(err.message)}</div>`; });
  if (window.Viz) draw(); else window.addEventListener("load", draw, { once: true });
}

function matrixCell(r, ruleId) {
  const ev = r.score.evaluations.find((x) => x.rule_id === ruleId);
  if (ev.status === "triggered") return `<span class="cell ${ev.points_awarded ? "hit" : "hit0"}" title="${esc(ev.rationale)}">+${ev.points_awarded}</span>`;
  const [sym, cls] = { not_triggered: ["·", "no"], insufficient_data: ["?", "insuf"], not_applicable: ["–", "na"] }[ev.status];
  return `<span class="cell ${cls}" title="${esc(ev.rationale)}">${sym}</span>`;
}

function viewMatrix(v) {
  if (!rows().length) { v.innerHTML = empty("◎", "No recipients to compare", "Run a live scan with broader filters."); return; }
  const onlyLeads = v.dataset.onlyLeads === "1";
  const list = onlyLeads ? leads() : rows();
  let html = `<label class="toggle"><input type="checkbox" id="onlyLeads"${onlyLeads ? " checked" : ""}><span class="track"></span>Anomalies only</label>`;
  if (!list.length) {
    html += empty("✓", "No anomalies found matching current threshold", "Turn off 'Anomalies only' to see every screened recipient.");
  } else {
    html += card(`<div class="matrix-wrap"><table class="matrix"><thead><tr><th>Recipient</th><th>Awarded</th>
      ${MATRIX_COLS.map(([, l]) => `<th>${esc(l)}</th>`).join("")}<th>Score</th></tr></thead><tbody>
      ${list.map((r) => `<tr data-key="${esc(r.entity_key)}"><td><b>${esc(r.entity.name)}</b><div class="district">${esc(r.enrichment.strike_force?.district || oneLine(r.entity.address))}</div></td>
        <td>${money(r.entity.total_awarded)}</td>${MATRIX_COLS.map(([id]) => `<td>${matrixCell(r, id)}</td>`).join("")}
        <td><b>${r.score.score}</b></td></tr>`).join("")}
      </tbody></table></div>
      <div class="legend"><span><b>+n</b> triggered</span><span><b>·</b> not triggered</span><span><b>?</b> insufficient data</span><span><b>–</b> not applicable</span><span>Click a row to inspect; hover a cell for the rationale.</span></div>`);
  }
  v.innerHTML = html;
  $("onlyLeads").addEventListener("change", (e) => { v.dataset.onlyLeads = e.target.checked ? "1" : "0"; viewMatrix(v); });
  v.querySelectorAll("tr[data-key]").forEach((tr) => tr.addEventListener("click", () => {
    S.selected = tr.dataset.key; S.view = "Lead Inspector"; renderControls(); render();
  }));
}

function exportButtonsHtml(i) {
  return `<button class="btn" data-i="${i}" data-kind="md">Markdown briefing</button>
     <button class="btn" data-i="${i}" data-kind="json">JSON dossier</button>
     <button class="btn" data-i="${i}" data-kind="zip">Evidence archive (.zip)</button>`;
}

function wireExports(v, list) {
  v.querySelectorAll("button[data-kind]").forEach((b) => b.addEventListener("click", async () => {
    const r = list[Number(b.dataset.i)], label = b.textContent;
    b.disabled = true; b.textContent = "Preparing…";
    try {
      if (b.dataset.kind === "zip") {
        download(`${r.entity.name.replace(/[^A-Za-z0-9]+/g, "_")}_evidence.zip`, await api("evidence", leadPayload(r), true));
      } else {
        const d = await getDossier(r);
        const id = d.dossier.dossier_id;
        if (b.dataset.kind === "md") download(`${id}.md`, new Blob([d.markdown], { type: "text/markdown" }));
        else download(`${id}.json`, new Blob([JSON.stringify(d.dossier, null, 2)], { type: "application/json" }));
      }
    } catch (err) { alerts([["err", err.message]]); }
    b.disabled = false; b.textContent = label;
  }));
}

function viewExport(v) {
  const list = leads();
  if (!list.length) { v.innerHTML = empty("✓", "No anomalies found matching current threshold", "Only flagged leads are exported with evidence archives."); return; }
  v.innerHTML = list.map((r, i) => card(`<div class="lead">${ring(r.score.score)}<div class="lead-main"><div class="lead-name">${esc(r.entity.name)}</div>
      <div class="lead-meta">${esc(oneLine(r.entity.address))} · ${money(r.entity.total_awarded)}</div>
      ${r.enrichment.strike_force?.district ? `<div class="district">${esc(r.enrichment.strike_force.district)}</div>` : ""}</div></div>`) +
    `<div class="export-row">${exportButtonsHtml(i)}</div>`).join("") +
    `<p class="notice">Each archive holds the raw API responses, ledger, provenance.json, the dossier and a SHA256SUMS file (<code>sha256sum -c SHA256SUMS</code>).
     Exports are working papers for intake agents and whistleblower counsel. Verify every indicator before any filing.</p>`;
  wireExports(v, list);
}

/* ------------------------------------------------------------------ Boot */

(async function boot() {
  try {
    S.caps = await api("health");
  } catch (e) {
    $("view").innerHTML = empty("⚠︎", "API unavailable", `The scan API did not respond (${e.message}). Check the deployment's function logs.`);
    $("run").disabled = true;
    return;
  }
  initControls();
  render();
})();
