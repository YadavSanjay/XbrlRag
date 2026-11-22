"use strict";
// H-X filing locator UI. Everything stays in this browser and the local server.

const $ = (id) => document.getElementById(id);
const state = { meta: null, family: "Revenue", scale: "auto", draft: null,
                cf: null, cp: null, co: { rank: null, dep: null }, tx: { filing: null, passages: [] } };
const SCALE_EXP = { units: 0, thousands: 3, millions: 6, billions: 9 };

// ---------------------------------------------------------------- helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, params) {
  const url = params ? `${path}?${new URLSearchParams(params)}` : path;
  const r = await fetch(url);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(out.detail || r.statusText);
  return out;
}
function busy(el, text = "Working…") { el.innerHTML = `<p class="spin">${esc(text)}</p>`; }
function fail(el, e) { el.innerHTML = `<p class="err">${esc(e.message || e)}</p>`; }
function intOrNull(d) { const n = parseInt(d, 10); return Number.isFinite(n) ? n : null; }

function defaultScale(value, unit, decimals) {
  if (unit && (unit.includes("/") || unit === "pure")) return "units";
  const d = intOrNull(decimals) ?? 0;
  if (d <= -5 && Math.abs(value) >= 1e6) return "millions";
  if (d <= -3 && Math.abs(value) >= 1e3) return "thousands";
  return "units";
}
function groupDigits(s) {
  const neg = s.startsWith("-"); if (neg) s = s.slice(1);
  const [w, f] = s.split(".");
  return (neg ? "-" : "") + w.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (f !== undefined ? "." + f : "");
}
// Mirrors snippets.format_amount: the digits the filing reports, at the chosen scale.
function fmtAmount(value, scale, decimals) {
  const exp = SCALE_EXP[scale];
  const v = value / 10 ** exp;
  const d = intOrNull(decimals);
  if (d === null) return groupDigits(String(Number(v.toPrecision(15))));
  return groupDigits(v.toFixed(Math.max(0, d + exp)));
}
function scaleFor(value, unit, decimals) {
  return state.scale === "auto" ? defaultScale(value, unit, decimals) : state.scale;
}
function shown(obs) {
  if (!obs || obs.value === null || obs.value === undefined) return obs && obs.nil ? "nil" : "—";
  const sc = scaleFor(obs.value, obs.unit, obs.decimals);
  const tag = sc === "units" ? "" : ` <span class="hint">${sc.slice(0, 3)}</span>`;
  return `${esc(fmtAmount(obs.value, sc, obs.decimals))}${tag}`;
}
function unitLabel(u) { return esc(u || ""); }
function amountItem(obs, scale) {
  const item = { expected: fmtAmount(obs.value, scale, obs.decimals) };
  if (scale !== "units") item.scale = scale;
  return item;
}
function links(obs) {
  if (!obs) return "";
  return `<a href="${esc(obs.local_url)}" target="_blank" rel="noopener" title="Open the filing here, highlighted">in filing</a>
          · <a href="${esc(obs.viewer_url)}" target="_blank" rel="noopener" title="SEC inline viewer (find the value there)">sec.gov</a>`;
}
function filingCell(obs) {
  if (!obs) return "";
  return `<span class="mono">${esc(obs.accession)}</span><br><span class="hint">${esc(obs.form)} filed ${esc(obs.filing_date)}</span>`;
}
function dimsText(dims) {
  return (dims || []).map(([a, m]) => `${a.split(":").pop()} = ${m.split(":").pop()}`).join("; ");
}
function roleBadge(row) {
  if (!row.family) return `<span class="badge mute">no family</span>`;
  const cls = row.role === "primary" ? "" : "warn";
  return `<span class="badge ${cls}">${esc(row.role || "?")} · rank ${esc(row.rank)}</span>`;
}
function restBadge(row) {
  if (row.restated) return `<span class="badge warn" title="A later annual report states a different value">restated</span>`;
  if (row.precision_only) return `<span class="badge mute" title="Later report differs only by rounding">precision</span>`;
  if (row.restatements && row.restatements.length) return `<span class="badge ok" title="Later reports repeat the same value">confirmed later</span>`;
  return "";
}
function storeButton(ticker, fy, obs, concept, member, accession) {
  const sc = scaleFor(obs.value, obs.unit, obs.decimals);
  const payload = { ticker, fiscal_year: fy, expected: fmtAmount(obs.value, sc, obs.decimals),
                    scale: sc === "units" ? null : sc, unit: obs.unit || "USD",
                    concept: concept || null, member: member || null, accession: accession || null };
  return `<button class="link store-check" data-p='${esc(JSON.stringify(payload))}'>check-keys</button><span class="store-res"></span>`;
}
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".store-check");
  if (!btn) return;
  const res = btn.nextElementSibling;
  res.innerHTML = ` <span class="spin">…</span>`;
  try {
    const out = await post("/api/store/check", JSON.parse(btn.dataset.p));
    const cls = out.status === "verified" ? "ok" : out.status === "ambiguous" ? "warn" : "bad";
    const ids = (out.matches || []).map((m) => `${m.fact_id}${m.member_label ? " (" + m.member_label + ")" : ""}`).join(", ");
    res.innerHTML = ` <span class="badge ${cls}" title="${esc(ids || out.error || "")}">${esc(out.status)}</span>`;
  } catch (e) { res.innerHTML = ` <span class="badge bad">${esc(e.message)}</span>`; }
});
function checkedValues(container) {
  return [...container.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
}
function fillSelect(sel, values, selected) {
  sel.innerHTML = values.map((v) => `<option ${String(v) === String(selected) ? "selected" : ""}>${esc(v)}</option>`).join("");
}
function tickerBoxes(container, tickers, checked) {
  container.innerHTML = tickers.map((t) =>
    `<label><input type="checkbox" value="${esc(t)}" ${checked.includes(t) ? "checked" : ""}>${esc(t)}</label>`).join("");
}
function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }

// ---------------------------------------------------------------- setup

async function init() {
  const meta = await api("/api/meta");
  state.meta = meta;
  const st = [];
  st.push(meta.db.ok ? `<span class="pill ok">fact store: ${esc(meta.db.database)}</span>`
                     : `<span class="pill bad" title="${esc(meta.db.error)}">fact store unreachable: raw filings only</span>`);
  if (meta.db.ok && !meta.db.quiet_errors) {
    st.push(`<span class="pill warn" title="Not a superuser: the server's log_min_error_statement and log_min_messages apply, so a failing SQL statement and its error are written to the Postgres log">failing SQL is logged by Postgres</span>`);
  }
  st.push(`<span class="pill">${meta.filings} annual filings · ${meta.tickers.length} filers</span>`);
  st.push(meta.labels_from_registry ? `<span class="pill ok">fiscal years: fact-store labels</span>`
                                    : `<span class="pill warn">fiscal years: cover-page labels (DB down)</span>`);
  $("status").innerHTML = st.join("");
  if (meta.label_notes.length) {
    $("labelNotes").innerHTML = `<h3>Fiscal-year labels</h3><p>These filings' download folder or cover page names
      another year than the label used here (the fact store's, which the keys use):</p>` +
      meta.label_notes.map((f) => `<div>${esc(f.ticker)} ${esc(f.form)} ends ${esc(f.period_end)}: <b>FY${f.fiscal_year}</b>
        <span class="hint">(folder ${f.folder_year}, cover ${f.dei_year ?? "–"}, from ${esc(f.label_source)})</span></div>`).join("");
  }
  renderFamTree();
  const years = meta.years;
  const defYear = years.length > 1 ? years[years.length - 2] : years[0];
  document.querySelectorAll("select.fy").forEach((s) => fillSelect(s, years, defYear));
  document.querySelectorAll("select.tk").forEach((s) => fillSelect(s, meta.tickers, meta.tickers[0]));
  const groupNames = ["(pick filers below)", ...Object.keys(meta.groups), "(all development filers)"];
  for (const id of ["cfGroup", "coGroup"]) fillSelect($(id), groupNames, groupNames[0]);
  tickerBoxes($("cfTickers"), meta.tickers, []);
  tickerBoxes($("coTickers"), meta.tickers, []);
  fillSelect($("coDepFam"), familyList(), "Revenue");
  cpYearBoxes();
  wire();
}
function familyList() {
  return state.meta.family_tree.flatMap((s) => s.families.flatMap((f) => [f.family, ...f.subs]));
}
function renderFamTree() {
  const q = $("famFilter").value.toLowerCase();
  const html = state.meta.family_tree.map((s) => {
    const items = s.families.flatMap((f) => [
      { name: f.family, sub: false }, ...f.subs.map((n) => ({ name: n, sub: true }))])
      .filter((i) => !q || i.name.toLowerCase().includes(q));
    if (!items.length) return "";
    return `<div class="st">${esc(s.statement)}</div>` + items.map((i) =>
      `<button class="f ${i.sub ? "sub" : ""} ${i.name === state.family ? "on" : ""}" data-f="${esc(i.name)}">${esc(i.name)}</button>`).join("");
  }).join("");
  $("famTree").innerHTML = html;
}
function groupChange(sel, box) {
  const g = sel.value;
  let picks = [];
  if (state.meta.groups[g]) picks = state.meta.groups[g];
  else if (g === "(all development filers)") picks = state.meta.tickers;
  else return;
  tickerBoxes(box, state.meta.tickers, picks);
}
function cpYearBoxes() {
  const t = $("cpTicker").value;
  $("cpYears").innerHTML = state.meta.years.map((y) =>
    `<label><input type="checkbox" value="${y}" checked>FY${y}</label>`).join("") +
    `<span class="hint">(${esc(t)}: unchecked years are skipped)</span>`;
}
function wire() {
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]"); if (!b) return;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
  });
  $("famTree").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]"); if (!b) return;
    state.family = b.dataset.f; $("famPicked").textContent = state.family; renderFamTree();
  });
  $("famFilter").addEventListener("input", renderFamTree);
  $("scale").addEventListener("change", () => {
    state.scale = $("scale").value;
    if (state.cf) renderCf(); if (state.cp) renderCp();
  });
  $("cfGroup").addEventListener("change", () => groupChange($("cfGroup"), $("cfTickers")));
  $("coGroup").addEventListener("change", () => groupChange($("coGroup"), $("coTickers")));
  $("cpTicker").addEventListener("change", cpYearBoxes);
  $("cfGo").addEventListener("click", runCf);
  $("cfSub").addEventListener("change", cfSubUi);
  $("cfDraft").addEventListener("click", draftCf);
  $("cpGo").addEventListener("click", runCp);
  $("cpSub").addEventListener("change", cpSubUi);
  $("cpDraft").addEventListener("click", draftCp);
  $("coGo").addEventListener("click", runCoRank);
  $("coDepKind").addEventListener("change", coDepUi);
  $("coDepGo").addEventListener("click", runCoDep);
  $("coDraft").addEventListener("click", draftCo);
  $("hoCov").addEventListener("click", runCoverage);
  $("hoAmbGo").addEventListener("click", runAmbiguous);
  $("hoSub").addEventListener("change", hoSubUi);
  $("hoDraft").addEventListener("click", draftHo);
  $("txGo").addEventListener("click", runText);
  $("txQ").addEventListener("keydown", (e) => { if (e.key === "Enter") runText(); });
  $("txDraft").addEventListener("click", draftTx);
  $("exFiling").addEventListener("click", runExFilings);
  $("exFam").addEventListener("click", runExFamily);
  $("exMembers").addEventListener("click", runExMembers);
  $("exSearch").addEventListener("click", runExSearch);
  $("exQ").addEventListener("keydown", (e) => { if (e.key === "Enter") runExSearch(); });
  $("fsCands").addEventListener("click", runFsCands);
  $("fsCheck").addEventListener("click", runFsCheck);
  $("fsSql").addEventListener("click", runFsSql);
  $("dRender").addEventListener("click", renderDraft);
  $("dCopy").addEventListener("click", () => navigator.clipboard.writeText($("dYaml").textContent));
  $("dClose").addEventListener("click", () => { $("draft").hidden = true; });
  cfSubUi(); cpSubUi(); coDepUi(); hoSubUi();
}

// ---------------------------------------------------------------- S1 table (shared)

function s1Table(rows, ctx) {
  if (!rows.length) return `<p class="hint">No tagged fact matches.</p>`;
  const body = rows.map((r, i) => {
    const hist = (r.restatements || []).map((o) =>
      `<div class="hint">${esc(o.filing_date)} ${esc(o.form)} <span class="mono">${esc(o.accession)}</span>: ${shown(o)} · ${links(o)}</div>`).join("");
    return `<tr>
      <td><span class="mono">${esc(r.concept)}</span><br><span class="hint">${esc(r.label)}</span></td>
      <td>${r.family ? esc(r.family) : ""} ${roleBadge(r)}</td>
      <td class="hint">${esc(dimsText(r.dims))}</td>
      <td class="num">${shown(r.original)}<br><span class="hint">${links(r.original)}</span></td>
      <td class="num">${shown(r.latest)} ${restBadge(r)}<br><span class="hint">${esc(r.latest.filing_date)}</span></td>
      <td>${unitLabel(r.latest.unit)}<br><span class="hint">${esc(r.original.period_start || "")} → ${esc(r.original.period_end)}</span></td>
      <td>${ctx && ctx.ticker ? storeButton(ctx.ticker, ctx.fy, r.latest, r.family || "", "", "") : ""}</td>
    </tr>${hist ? `<tr class="detail"><td colspan="7">Later statements of this fact: ${hist}</td></tr>` : ""}`;
  }).join("");
  return `<div class="scroll"><table><thead><tr><th>Concept as tagged</th><th>Family (our mapping)</th><th>Dimensions</th>
    <th class="num">As originally reported</th><th class="num">Latest (S1)</th><th>Unit · period</th><th>Fact store</th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}
function shadowNote(shadowed, family) {
  const winners = [...new Set(Object.values(shadowed || {}))];
  if (!winners.length) return "";
  return `<div class="callout info"><b>${esc(family)}</b> lists concepts that our mapping gives to
    <b>${esc(winners.join(", "))}</b> when they carry no dimension (${Object.keys(shadowed).map((q) => `<span class="mono">${esc(q)}</span>`).join(", ")}).
    Such a split usually sits in ${esc(winners[0])}'s members: pick ${esc(winners[0])} and set
    <i>Member contains</i> (e.g. "Product", "Service"), or see Explore filing → members by axis.</div>`;
}
function filingLine(f) {
  if (!f) return `<div class="callout">No annual report for this filer and year on disk (<b>not_filed</b>).</div>`;
  return `<div class="hint">${esc(f.ticker)} FY${f.fiscal_year} · ${esc(f.form)} · <span class="mono">${esc(f.accession)}</span>
    · period ${esc(f.period_start)} → ${esc(f.period_end)} · filed ${esc(f.filing_date)}
    · <a href="${esc(f.local_url)}" target="_blank" rel="noopener">open here</a>
    · <a href="${esc(f.viewer_url)}" target="_blank" rel="noopener">sec.gov viewer</a>
    · <a href="${esc(f.index_url)}" target="_blank" rel="noopener">index</a></div>`;
}

// ---------------------------------------------------------------- cross-filer

async function runCf() {
  const tickers = checkedValues($("cfTickers"));
  if (tickers.length < 2) { fail($("cfOut"), "Pick at least 2 filers."); return; }
  busy($("cfOut"), "Reading filings (first time per filing takes a moment)…");
  try {
    state.cf = await api("/api/cross_filer", { tickers: tickers.join(","), fy: $("cfFy").value,
                                               family: state.family, member: $("cfMember").value });
    renderCf();
    $("cfBuild").hidden = false;
    fillSelect($("cfSubject"), state.cf.filers.filter((e) => !e.exclusion).map((e) => e.ticker));
  } catch (e) { fail($("cfOut"), e); }
}
function cfScale() {
  const first = state.cf.filers.find((e) => e.pick);
  return first ? scaleFor(first.pick.latest.value, first.pick.latest.unit, first.pick.latest.decimals) : "units";
}
function renderCf() {
  const r = state.cf; const fy = r.fiscal_year;
  const sorted = [...r.filers].sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));
  const rows = sorted.map((e) => {
    if (e.exclusion && !e.pick) {
      return `<tr class="excl"><td></td><td><b>${esc(e.ticker)}</b></td><td colspan="5"><span class="badge bad">${esc(e.exclusion)}</span>
        ${e.exclusion === "not_tagged" ? "no fact of this family in the year's own column; try Explore filing → concept search" : "no annual report for this year on disk"}</td></tr>`;
    }
    const p = e.pick;
    const others = e.rows.length > 1 ? `<button class="link toggle" data-t="${esc(e.ticker)}">${e.rows.length - 1} more concept(s)</button>` : "";
    return `<tr class="${e.exclusion ? "excl" : ""}">
      <td class="num">${e.rank ?? ""}</td>
      <td><b>${esc(e.ticker)}</b>${e.exclusion ? ` <span class="badge bad">${esc(e.exclusion)}</span>` : ""}</td>
      <td class="num">${shown(p.latest)} ${restBadge(p)}${p.restated ? `<br><span class="hint">orig. ${shown(p.original)}</span>` : ""}</td>
      <td><span class="mono">${esc(p.concept)}</span> ${roleBadge(p)}<br><span class="hint">${esc(p.label)} ${esc(dimsText(p.dims))}</span> ${others}</td>
      <td>${filingCell(p.latest)}</td>
      <td>${links(p.latest)}</td>
      <td>${storeButton(e.ticker, fy, p.latest, r.family, r.member, "")}</td></tr>
      <tr class="detail" data-for="${esc(e.ticker)}" hidden><td colspan="7">${s1Table(e.rows, { ticker: e.ticker, fy })}</td></tr>`;
  }).join("");
  const a = r.aggregates; const sc = cfScale(); const dec = (state.cf.filers.find((e) => e.pick) || {}).pick?.latest.decimals;
  const concepts = r.filers.filter((e) => e.pick && !e.exclusion).reduce((m, e) => {
    (m[e.pick.concept] = m[e.pick.concept] || []).push(e.ticker); return m; }, {});
  const variance = Object.keys(concepts).length > 1
    ? `<div class="callout"><b>Filers tag this family differently</b> — a nuance worth a question:
        ${Object.entries(concepts).map(([c, ts]) => `<span class="mono">${esc(c)}</span> (${esc(ts.join(", "))})`).join("; ")}</div>` : "";
  const subs = r.filers.filter((e) => e.pick && e.pick.role && e.pick.role !== "primary");
  const subNote = subs.length ? `<div class="callout info">Answered by a <b>${esc(subs.map((e) => e.pick.role).join("/"))}</b> concept for
        ${esc(subs.map((e) => e.ticker).join(", "))}: check the filing that this is the figure your question means.</div>` : "";
  const multi = r.member ? r.filers.filter((e) => e.rows.length > 1).map((e) => e.ticker) : [];
  const multiNote = multi.length ? `<div class="callout">"${esc(r.member)}" matches several facts for <b>${esc(multi.join(", "))}</b>;
      the single-axis one is shown. Open "more concept(s)" and confirm it is the member your question means.</div>` : "";
  const restNote = r.filers.some((e) => e.pick && e.pick.restated)
    ? `<div class="callout">Some values were <b>restated</b> in a later report. Keys use the latest value (S1).</div>` : "";
  const shadow = r.filers.some((e) => e.pick) ? "" : shadowNote((r.filers.find((e) => e.shadowed) || {}).shadowed, r.family);
  $("cfOut").innerHTML = `${shadow}${variance}${subNote}${multiNote}${restNote}
    <div class="agg"><span>Ranked filers: <b>${a.count}</b></span>
      <span>Sum: <b>${a.sum === null ? "—" : esc(fmtAmount(a.sum, sc, dec))}</b> ${sc}</span>
      <span>Median: <b>${a.median === null ? "—" : esc(fmtAmount(a.median, sc, dec))}</b> ${sc}</span>
      <span>Order (high → low): <b>${esc(a.order_desc.join(" > "))}</b></span></div>
    <table><thead><tr><th class="num">Rank</th><th>Filer</th><th class="num">Latest value (S1)</th><th>Concept as tagged</th>
      <th>Filing</th><th>Find it</th><th>Fact store</th></tr></thead><tbody>${rows}</tbody></table>`;
  $("cfOut").querySelectorAll(".toggle").forEach((b) => b.addEventListener("click", () => {
    const tr = $("cfOut").querySelector(`tr.detail[data-for="${b.dataset.t}"]`); tr.hidden = !tr.hidden; }));
}
function cfSubUi() {
  const s = $("cfSub").value;
  document.querySelectorAll(".cf-n").forEach((e) => (e.hidden = !["screen_top", "screen_bottom"].includes(s)));
  document.querySelectorAll(".cf-count").forEach((e) => (e.hidden = s !== "count"));
  document.querySelectorAll(".cf-sum").forEach((e) => (e.hidden = s !== "sum"));
  document.querySelectorAll(".cf-peer").forEach((e) => (e.hidden = s !== "peer_relative"));
}
// Builds the cross_filer fields from a cross-filer result (also used by compound).
function crossFilerFields(res, subtype, opts) {
  const sc = opts.scale;
  const ranked = res.filers.filter((e) => e.pick && !e.exclusion)
    .sort((a, b) => b.pick.latest.value - a.pick.latest.value);
  const row = (e) => ({ ticker: e.ticker, ...amountItem(e.pick.latest, sc) });
  const unit = ranked.length ? ranked[0].pick.latest.unit : "USD";
  const f = { subtype, fy: res.fiscal_year, unit, concept: res.family };
  if (res.member) f.member = res.member;
  if (subtype === "peer_relative") f.ticker = opts.subject;
  f.universe = res.filers.map((e) => e.ticker);
  const excl = res.filers.filter((e) => e.exclusion).map((e) => ({ ticker: e.ticker, reason: e.exclusion }));
  if (excl.length) f.exclusions = excl;
  const dec = ranked.length ? ranked[0].pick.latest.decimals : null;
  if (subtype === "screen_top") f.rows = ranked.slice(0, opts.n).map(row);
  else if (subtype === "screen_bottom") f.rows = [...ranked].reverse().slice(0, opts.n).map(row);
  else if (subtype === "count") {
    const thr = parseFloat(String(opts.threshold).replace(/,/g, "")) * 10 ** SCALE_EXP[sc];
    const hits = ranked.filter((e) => (opts.cmp === "gt" ? e.pick.latest.value > thr : e.pick.latest.value < thr));
    f.rows = hits.map(row); f.expected = hits.length;
  } else if (subtype === "sum") {
    const want = (opts.sumOf || "").split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
    const used = want.length ? ranked.filter((e) => want.includes(e.ticker)) : ranked;
    f.rows = used.map(row);
    const total = used.reduce((s, e) => s + e.pick.latest.value, 0);
    f.expected = fmtAmount(total, sc, dec); if (sc !== "units") f.scale = sc;
  } else if (subtype === "median") {
    f.rows = ranked.map(row);
    const v = ranked.map((e) => e.pick.latest.value).sort((x, y) => x - y);
    const m = v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2;
    f.expected = fmtAmount(m, sc, v.length % 2 ? dec : (intOrNull(dec) ?? 0) + 1); if (sc !== "units") f.scale = sc;
  } else if (subtype === "peer_relative") {
    f.rows = ranked.map(row);
    const subj = ranked.find((e) => e.ticker === opts.subject);
    f.expected = subj ? 1 + ranked.filter((e) => e.pick.latest.value > subj.pick.latest.value).length : null;
  }
  return f;
}
function draftCf() {
  const f = crossFilerFields(state.cf, $("cfSub").value, { scale: cfScale(), n: +$("cfN").value,
    cmp: $("cfCmp").value, threshold: $("cfThr").value, sumOf: $("cfSumOf").value, subject: $("cfSubject").value });
  openDraft({ kind: "cross_filer", ...f }, `cf-${slug(f.subtype)}-${slug(state.cf.family)}-${f.fy}`);
}

// ---------------------------------------------------------------- cross-period

async function runCp() {
  const years = checkedValues($("cpYears"));
  if (!years.length) { fail($("cpOut"), "Check at least one year."); return; }
  busy($("cpOut"), "Reading filings…");
  try {
    state.cp = await api("/api/cross_period", { ticker: $("cpTicker").value, years: years.join(","),
                                                family: state.family, member: $("cpMember").value });
    renderCp(); $("cpBuild").hidden = false;
    fillSelect($("cpPitFy"), state.cp.years.filter((y) => y.pick).map((y) => y.fiscal_year));
  } catch (e) { fail($("cpOut"), e); }
}
function renderCp() {
  const r = state.cp;
  const rows = r.years.map((y) => {
    if (!y.pick) return `<tr class="excl"><td>FY${y.fiscal_year}</td><td colspan="6">${y.filing ? `<span class="badge bad">not_tagged</span> no fact of this family`
                                                                              : `<span class="badge bad">not_filed</span> no annual report on disk`}</td></tr>`;
    const p = y.pick;
    return `<tr><td>FY${y.fiscal_year}</td>
      <td class="num">${shown(p.original)}<br>${filingCell(p.original)}<br><span class="hint">${links(p.original)}</span></td>
      <td class="num">${shown(p.latest)} ${restBadge(p)}<br>${filingCell(p.latest)}<br><span class="hint">${links(p.latest)}</span></td>
      <td><span class="mono">${esc(p.concept)}</span> ${roleBadge(p)}<br><span class="hint">${esc(p.label)} ${esc(dimsText(p.dims))}</span></td>
      <td class="hint">${esc(p.original.period_start || "")} → ${esc(p.original.period_end)}</td>
      <td>${storeButton(r.ticker, y.fiscal_year, p.latest, r.family, r.member, "")}</td></tr>`;
  }).join("");
  const concepts = [...new Set(r.years.filter((y) => y.pick).map((y) => y.pick.concept))];
  const change = concepts.length > 1 ? `<div class="callout"><b>The filer changed concept across years</b>: ${concepts.map((c) => `<span class="mono">${esc(c)}</span>`).join(" → ")}</div>` : "";
  const cagr = r.cagr === null ? "" : `<div class="agg"><span>CAGR FY${r.cagr_from} → FY${r.cagr_to} (latest values):
      <b>${(r.cagr * 100).toFixed(2)}%</b></span><span class="hint">(end/start)^(1/years) − 1, S4</span></div>`;
  $("cpOut").innerHTML = `${change}${cagr}<table><thead><tr><th>Year</th><th class="num">As originally reported</th>
    <th class="num">Latest (S1)</th><th>Concept as tagged</th><th>Period</th><th>Fact store</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function cpSubUi() { document.querySelectorAll(".cp-pit").forEach((e) => (e.hidden = $("cpSub").value !== "point_in_time")); }
function crossPeriodFields(res, subtype, pitFy) {
  const have = res.years.filter((y) => y.pick);
  const first = have[0]?.pick.latest;
  const sc = first ? scaleFor(first.value, first.unit, first.decimals) : "units";
  const f = { subtype, ticker: res.ticker, unit: first ? first.unit : "USD", concept: res.family };
  if (res.member) f.member = res.member;
  if (subtype === "trend") f.rows = have.map((y) => ({ fy: y.fiscal_year, ...amountItem(y.pick.latest, sc) }));
  else if (subtype === "cagr") {
    const a = have[0], b = have[have.length - 1];
    f.rows = [a, b].map((y) => ({ fy: y.fiscal_year, ...amountItem(y.pick.latest, sc) }));
    const yrs = b.fiscal_year - a.fiscal_year;
    f.expected = `${((Math.pow(b.pick.latest.value / a.pick.latest.value, 1 / yrs) - 1) * 100).toFixed(1)}%`;
  } else {
    const y = have.find((x) => String(x.fiscal_year) === String(pitFy));
    f.fy = y.fiscal_year;
    f.as_originally_reported = { ...amountItem(y.pick.original, sc), accession: y.pick.original.accession };
    f.latest = { ...amountItem(y.pick.latest, sc), accession: y.pick.latest.accession };
  }
  return f;
}
function draftCp() {
  const sub = $("cpSub").value;
  const f = crossPeriodFields(state.cp, sub, $("cpPitFy").value);
  if (sub === "point_in_time" && f.as_originally_reported.accession === f.latest.accession) {
    alert("No later report states this year's value, so both accessions are the same; the validator will refuse it. Pick a restated year.");
  }
  openDraft({ kind: "cross_period", ...f }, `cp-${slug(sub)}-${slug(state.cp.ticker)}-${slug(state.cp.family)}`);
}

// ---------------------------------------------------------------- compound

async function runCoRank() {
  const tickers = checkedValues($("coTickers"));
  if (tickers.length < 2) { fail($("coRankOut"), "Pick at least 2 filers."); return; }
  busy($("coRankOut"));
  try {
    const res = await api("/api/cross_filer", { tickers: tickers.join(","), fy: $("coFy").value, family: state.family });
    const first = res.filers.find((e) => e.pick);
    const sc = first ? scaleFor(first.pick.latest.value, first.pick.latest.unit, first.pick.latest.decimals) : "units";
    state.co.rank = { res, fields: crossFilerFields(res, $("coSub").value, { scale: sc, n: +$("coN").value }) };
    const rows = state.co.rank.fields.rows;
    $("coRankOut").innerHTML = `<div class="agg"><span>${esc(res.family)} FY${res.fiscal_year} ${esc($("coSub").value)}:
        <b>${esc(rows.map((x) => `${x.ticker} ${x.expected}`).join(" · "))}</b> (${sc})</span>
        ${state.co.rank.fields.exclusions ? `<span>excluded: ${esc(state.co.rank.fields.exclusions.map((x) => x.ticker + " " + x.reason).join(", "))}</span>` : ""}</div>`;
    fillSelect($("coDepTicker"), rows.map((x) => x.ticker));
    $("coStep2").hidden = false;
  } catch (e) { fail($("coRankOut"), e); }
}
function coDepUi() {
  const k = $("coDepKind").value;
  document.querySelectorAll(".co-dep-cp").forEach((e) => (e.hidden = k === "cross_filer"));
  document.querySelectorAll(".co-dep-cf").forEach((e) => (e.hidden = k !== "cross_filer"));
  $("coDepFam").parentElement.hidden = k === "text";
  $("coDepCpSub").parentElement.hidden = k !== "cross_period";
  $("coDepYears").parentElement.hidden = k !== "cross_period";
}
async function runCoDep() {
  const k = $("coDepKind").value; const out = $("coDepOut");
  busy(out);
  try {
    if (k === "text") {
      $("txTicker").value = $("coDepTicker").value;
      state.co.dep = { kind: "text" };
      out.innerHTML = `<div class="callout info">Collect passages, the reference answer and key points on the <b>Text</b> tab
        (filer ${esc($("coDepTicker").value)} is preselected), then come back and press Draft.</div>`;
    } else if (k === "cross_period") {
      const res = await api("/api/cross_period", { ticker: $("coDepTicker").value, years: $("coDepYears").value, family: $("coDepFam").value });
      state.co.dep = { kind: "cross_period", res };
      const prev = state.cp; state.cp = res; renderCp(); out.innerHTML = $("cpOut").innerHTML; state.cp = prev; if (prev) renderCp();
    } else {
      const tickers = state.co.rank.fields.rows.map((x) => x.ticker);
      if (tickers.length < 2) throw new Error("A cross-filer dependent needs at least 2 ranked filers.");
      const res = await api("/api/cross_filer", { tickers: tickers.join(","), fy: $("coDepFy").value, family: $("coDepFam").value });
      state.co.dep = { kind: "cross_filer", res };
      const prev = state.cf; state.cf = res; renderCf(); out.innerHTML = $("cfOut").innerHTML; state.cf = prev; if (prev) renderCf();
    }
    $("coDraft").disabled = false;
  } catch (e) { fail(out, e); }
}
function draftCo() {
  const k = $("coDepKind").value; let dep;
  if (k === "text") dep = { kind: "text", ...textFields() };
  else if (k === "cross_period") dep = { kind: "cross_period", ...crossPeriodFields(state.co.dep.res, $("coDepCpSub").value) };
  else {
    const res = state.co.dep.res; const first = res.filers.find((e) => e.pick);
    const sc = first ? scaleFor(first.pick.latest.value, first.pick.latest.unit, first.pick.latest.decimals) : "units";
    dep = { kind: "cross_filer", ...crossFilerFields(res, $("coDepCfSub").value, { scale: sc, n: 1, subject: state.co.rank.fields.rows[0].ticker }) };
  }
  openDraft({ kind: "compound", ranking: state.co.rank.fields, dependent: dep },
            `co-${slug(state.co.rank.res.family)}-${slug(k)}`);
}

// ---------------------------------------------------------------- honesty

async function runCoverage() {
  busy($("hoCovOut"), "Checking every filer-year (first run reads all filings)…");
  try {
    const r = await api("/api/coverage", { family: state.family });
    const head = r.years.map((y) => `<th>FY${y}</th>`).join("");
    const rows = r.rows.map((row) => `<tr><td><b>${esc(row.ticker)}</b></td>${r.years.map((y) => {
      const c = row.cells[y];
      if (!c.forms.length) return `<td class="hint">—</td>`;
      const tag = c.tagged ? `<span class="badge ok" title="${esc(c.concept)}">tagged</span>` : `<span class="badge bad">not tagged</span>`;
      return `<td>${esc(c.forms.join(", "))}<br>${tag}</td>`;
    }).join("")}</tr>`).join("");
    $("hoCovOut").innerHTML = `<p class="hint">${esc(r.family)}: "—" = no annual report on disk for that year (out_of_period for that filer);
      "not tagged" = the year was filed but no fact of this family (not_tagged). Any company not listed is out_of_corpus.</p>
      <div class="scroll"><table><thead><tr><th>Filer</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  } catch (e) { fail($("hoCovOut"), e); }
}
async function runAmbiguous() {
  const out = $("hoAmbOut"); busy(out);
  try {
    const fams = $("hoFams").value.split(",").map((s) => s.trim()).filter(Boolean);
    const t = $("hoTicker").value, fy = $("hoFy").value;
    const res = await Promise.all(fams.map((f) => api("/api/family", { ticker: t, fy, family: f }).catch((e) => ({ error: e.message }))));
    out.innerHTML = `<table><thead><tr><th>Reading</th><th class="num">Latest (S1)</th><th>Concept</th><th>Find it</th></tr></thead><tbody>${
      res.map((r, i) => {
        if (r.error) return `<tr><td>${esc(fams[i])}</td><td colspan="3" class="err">${esc(r.error)}</td></tr>`;
        const flat = r.rows.filter((x) => !x.dims.length);
        const p = flat.find((x) => x.role === "primary") || flat[0];
        return p ? `<tr><td>${esc(fams[i])}</td><td class="num">${shown(p.latest)}</td><td class="mono">${esc(p.concept)}</td><td>${links(p.latest)}</td></tr>`
                 : `<tr><td>${esc(fams[i])}</td><td colspan="3" class="hint">not tagged</td></tr>`;
      }).join("")}</tbody></table>`;
  } catch (e) { fail(out, e); }
}
function hoSubUi() {
  const s = $("hoSub").value;
  document.querySelectorAll(".ho-dec").forEach((e) => (e.hidden = s !== "decline"));
  document.querySelectorAll(".ho-amb").forEach((e) => (e.hidden = s !== "ambiguous"));
  document.querySelectorAll(".ho-fp").forEach((e) => (e.hidden = s !== "false_premise"));
}
function draftHo() {
  const s = $("hoSub").value;
  const c = { kind: "honesty", subtype: s };
  const tks = $("hoTickers").value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
  if (tks.length) c.tickers = tks;
  const fy = intOrNull($("hoCaseFy").value); if (fy) c.fy = fy;
  if (s === "ambiguous") c.assumptions = $("hoAssume").value.split("\n").map((x) => x.trim()).filter(Boolean);
  if (s === "false_premise") c.correction = $("hoCorr").value.trim();
  if (s === "decline") c.reason = $("hoReason").value;
  openDraft(c, `ho-${slug(s)}-${slug(tks.join("-") || "x")}`);
}

// ---------------------------------------------------------------- text

function markWords(text, q) {
  const terms = [...q.matchAll(/"([^"]+)"/g)].map((m) => m[1]).concat(q.replace(/"[^"]+"/g, " ").split(/\s+/)).filter((w) => w.length > 1);
  let out = esc(text);
  for (const t of terms) {
    const re = new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    out = out.replace(re, (m) => `<mark>${m}</mark>`);
  }
  return out;
}
async function runText() {
  const out = $("txOut"); busy(out);
  try {
    const r = await api("/api/text", { ticker: $("txTicker").value, fy: $("txFy").value, q: $("txQ").value, section: $("txSection").value });
    state.tx.filing = r.filing;
    $("txFiling").innerHTML = filingLine(r.filing);
    const keep = $("txSection").value;
    $("txSection").innerHTML = `<option value="">all sections</option>` + r.sections.map((s) =>
      `<option ${s === keep ? "selected" : ""}>${esc(s)}</option>`).join("");
    out.innerHTML = r.hits.length ? r.hits.map((h, i) => `<div class="hit" data-i="${i}">
        <div class="sec">${esc(h.section)}</div><div class="txt">${markWords(h.text, $("txQ").value)}</div>
        <div class="row"><button class="q-all">Quote paragraph</button><button class="q-sel">Quote selection</button></div></div>`).join("")
      : `<p class="hint">No paragraph contains every word.</p>`;
    out.querySelectorAll(".hit").forEach((el, i) => {
      el.querySelector(".q-all").addEventListener("click", () => addPassage(r.hits[i].text, r.hits[i].section));
      el.querySelector(".q-sel").addEventListener("click", () => {
        const sel = String(window.getSelection() || "").trim();
        if (!sel) { alert("Select words inside this paragraph first."); return; }
        addPassage(sel, r.hits[i].section);
      });
    });
  } catch (e) { fail(out, e); }
}
function addPassage(quote, section) {
  state.tx.passages.push({ accession: state.tx.filing.accession, quote, section, ticker: state.tx.filing.ticker,
                           fy: state.tx.filing.fiscal_year });
  renderPassages();
}
function renderPassages() {
  $("txPassages").innerHTML = state.tx.passages.map((p, i) => `<div class="passage">${esc(p.ticker)} FY${p.fy}
    <span class="mono">${esc(p.accession)}</span> · ${esc(p.section)}<br>${esc(p.quote)}
    <button class="link" data-rm="${i}">remove</button></div>`).join("");
  $("txPassages").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => {
    state.tx.passages.splice(+b.dataset.rm, 1); renderPassages(); }));
}
function textFields() {
  const p = state.tx.passages;
  if (!p.length) throw new Error("Quote at least one passage on the Text tab.");
  return { ticker: p[0].ticker, fy: p[0].fy, reference_answer: $("txRef").value.trim(),
           key_points: $("txKeys").value.split("\n").map((x) => x.trim()).filter(Boolean),
           passages: p.map((x) => ({ accession: x.accession, quote: x.quote })) };
}
function draftTx() {
  try { const f = textFields(); openDraft({ kind: "text", ...f }, `tx-${slug(f.ticker)}-${f.fy}`); }
  catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- explore

async function runExFilings() {
  const out = $("exOut"); busy(out);
  try {
    const fs = await api("/api/filings", { ticker: $("exTicker").value });
    out.innerHTML = `<table><thead><tr><th>FY</th><th>Form</th><th>Accession</th><th>Period</th><th>Filed</th><th>Label</th><th>Open</th></tr></thead><tbody>${
      fs.map((f) => `<tr><td>FY${f.fiscal_year}</td><td>${esc(f.form)}</td><td class="mono">${esc(f.accession)}</td>
        <td>${esc(f.period_start)} → ${esc(f.period_end)}</td><td>${esc(f.filing_date)}</td>
        <td class="hint">${esc(f.label_source)}${f.folder_year !== f.fiscal_year ? ` (folder ${f.folder_year})` : ""}</td>
        <td><a href="${esc(f.local_url)}" target="_blank" rel="noopener">here</a> · <a href="${esc(f.viewer_url)}" target="_blank" rel="noopener">sec.gov</a></td></tr>`).join("")}</tbody></table>`;
  } catch (e) { fail(out, e); }
}
async function runExFamily() {
  const out = $("exOut"); busy(out);
  try {
    const t = $("exTicker").value, fy = +$("exFy").value;
    const [flat, dims] = await Promise.all([
      api("/api/family", { ticker: t, fy, family: state.family }),
      api("/api/family", { ticker: t, fy, family: state.family, dims: true })]);
    const mem = flat.members.map((m) => `<span class="mono">${esc(m.qname)}</span> <span class="hint">r${m.rank} ${esc(m.role)}${m.filer_specific ? " · filer-specific" : ""}</span>`).join(" · ");
    out.innerHTML = filingLine(flat.filing) + (flat.rows.length ? "" : shadowNote(flat.shadowed, state.family)) +
      `<p class="hint">${esc(state.family)} maps these concepts for this filer: ${mem || "none"}</p>
      <h4>Undimensioned</h4>${s1Table(flat.rows, { ticker: t, fy })}<h4>Dimensional (members)</h4>${s1Table(dims.rows, { ticker: t, fy })}`;
  } catch (e) { fail(out, e); }
}
async function runExMembers() {
  const out = $("exOut"); busy(out);
  try {
    const t = $("exTicker").value, fy = +$("exFy").value;
    const r = await api("/api/members", { ticker: t, fy, family: state.family });
    out.innerHTML = filingLine(r.filing) + (r.axes.length ? r.axes.map((a) => `<h4>${esc(a.axis)}</h4>${s1Table(a.rows, { ticker: t, fy })}`).join("")
                                                         : `<p class="hint">No dimensional facts of ${esc(state.family)}.</p>`);
  } catch (e) { fail(out, e); }
}
async function runExSearch() {
  const out = $("exOut"); busy(out);
  try {
    const t = $("exTicker").value, fy = +$("exFy").value;
    const r = await api("/api/concepts", { ticker: t, fy, q: $("exQ").value, dims: $("exDims").checked });
    out.innerHTML = filingLine(r.filing) + (r.truncated ? `<p class="hint">First 400 shown.</p>` : "") + s1Table(r.rows, { ticker: t, fy });
  } catch (e) { fail(out, e); }
}

// ---------------------------------------------------------------- fact store

async function runFsCands() {
  const out = $("fsCandOut"); busy(out);
  try {
    const t = $("fsTicker").value, fy = +$("fsFy").value;
    const [r, fs] = await Promise.all([
      api("/api/store/candidates", { ticker: t, fy, family: state.family, accession: $("fsAcc").value.trim() }),
      api("/api/filings", { ticker: t })]);
    if (!r.ok) throw new Error(r.error);
    const byAcc = Object.fromEntries(fs.map((f) => [f.accession, f]));
    out.innerHTML = `<p class="hint">The candidates <code>check-keys</code> matches ${esc(state.family)} amounts against for ${esc(t)} FY${fy}
      (S1: the latest later-labelled filing's value; with an accession, that filing's column).</p>
      <div class="scroll"><table><thead><tr><th>fact_id</th><th>Concept</th><th>Role · rank</th><th class="num">Value</th><th>Unit</th>
      <th>Period</th><th>Member</th><th>From filing</th><th>Element</th></tr></thead><tbody>${
      r.rows.map((c) => { const f = byAcc[c.accession];
        const el = f && c.xml_id ? `<a href="${esc(f.local_url)}#${esc(c.xml_id)}" target="_blank" rel="noopener">${esc(c.xml_id)}</a>` : esc(c.xml_id || "");
        return `<tr><td class="mono">${c.fact_id}</td><td class="mono">${esc(c.concept_short)}</td><td>${esc(c.role || "")} ${esc(c.rank ?? "")}</td>
          <td class="num">${esc(fmtAmount(c.value, scaleFor(c.value, c.unit, c.decimals), c.decimals))}</td><td>${esc(c.unit)}</td>
          <td class="hint">${esc(c.period_start || "")} → ${esc(c.period_end)}</td><td>${esc(c.member_label || "")}</td>
          <td class="mono">${esc(c.accession || "")}<br><span class="hint">filed ${esc(c.filed_date || "")}</span></td><td>${el}</td></tr>`; }).join("")
      }</tbody></table></div>`;
  } catch (e) { fail(out, e); }
}
async function runFsCheck() {
  const out = $("fsCheckOut"); busy(out);
  try {
    const sc = $("fsScale").value;
    const r = await post("/api/store/check", { ticker: $("fsTicker").value, fiscal_year: +$("fsFy").value,
      expected: $("fsExp").value, scale: sc === "units" ? null : sc, unit: $("fsUnit").value,
      concept: $("fsConcept").value || null, member: $("fsMember").value || null, accession: $("fsAcc").value.trim() || null });
    const cls = r.status === "verified" ? "ok" : r.status === "ambiguous" ? "warn" : "bad";
    const advice = { verified: "One class of facts carries it: check-keys will pass.",
      ambiguous: "Several concepts or members carry it: add a concept hint or a member.",
      not_found: "No fact carries it: check year, scale, unit, sign, rounding (S1: use the latest value).",
      invalid: r.error, error: r.error }[r.status] || "";
    out.innerHTML = `<p><span class="badge ${cls}">${esc(r.status)}</span> ${esc(advice)}</p>` + ((r.matches || []).length
      ? `<table><thead><tr><th>fact_id</th><th>Concept</th><th>Family</th><th class="num">Value</th><th>Member</th><th>Period</th></tr></thead><tbody>${
        r.matches.map((m) => `<tr><td class="mono">${m.fact_id}</td><td class="mono">${esc(m.concept_short)}</td><td>${esc(m.concept_family || "")}</td>
          <td class="num">${esc(m.value)}</td><td>${esc(m.member_label || "")}</td><td class="hint">${esc(m.period_end)}</td></tr>`).join("")}</tbody></table>` : "");
  } catch (e) { fail(out, e); }
}
async function runFsSql() {
  const out = $("fsSqlOut"); busy(out);
  try {
    const tickers = $("fsSqlTickers").value || $("fsTicker").value;
    const years = $("fsSqlYears").value || $("fsFy").value;
    const tpls = await api("/api/sql", { tickers, years, family: state.family, accession: $("fsAcc").value.trim() });
    out.innerHTML = tpls.map((t, i) => `<div class="builder"><h4>${esc(t.name)}</h4><p class="hint">${esc(t.note)}</p>
      <textarea class="sql" id="sql${i}">${esc(t.sql)}</textarea>
      <div class="row"><button class="primary" data-run="${i}">Run (read-only)</button>
        <button data-copy="${i}">Copy SQL</button></div><div id="sqlOut${i}"></div></div>`).join("");
    out.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => runSql(b.dataset.run)));
    out.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => navigator.clipboard.writeText($("sql" + b.dataset.copy).value)));
  } catch (e) { fail(out, e); }
}
async function runSql(i) {
  const out = $("sqlOut" + i); busy(out);
  try {
    const r = await post("/api/sql/run", { sql: $("sql" + i).value });
    if (!r.ok) throw new Error(r.error);
    out.innerHTML = `<div class="scroll"><table><thead><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
      r.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="hint">${r.rows.length} row(s)${r.truncated ? " (first 500)" : ""}</p>`;
  } catch (e) { fail(out, e); }
}

// ---------------------------------------------------------------- draft

function openDraft(fields, suggestedId) {
  state.draft = fields;
  // Replace the id only while it is still a suggestion (empty, or the last one offered).
  if (!$("dId").value || $("dId").value === state.autoId) $("dId").value = suggestedId;
  state.autoId = suggestedId;
  $("draft").hidden = false;
  $("dYaml").textContent = ""; $("dReport").innerHTML = ""; $("dSummary").textContent = "";
  renderDraft();
  $("draft").scrollIntoView({ behavior: "smooth", block: "end" });
}
async function renderDraft() {
  if (!state.draft) return;
  const c = { id: $("dId").value.trim() || "draft", question: $("dQ").value.trim() || "WRITE YOUR QUESTION", ...state.draft };
  $("dSummary").innerHTML = `<span class="spin">validating…</span>`;
  try {
    const r = await post("/api/case", { case: c });
    $("dYaml").textContent = r.yaml;
    const errs = r.errors.length ? `<p class="err"><b>Validator errors</b> (fix before check-keys):</p><ul>${r.errors.map((e) => `<li class="mono">${esc(e)}</li>`).join("")}</ul>` : "";
    let chk = "";
    if (r.check) {
      const cls = r.check.status === "verified" ? "ok" : r.check.status === "ambiguous" ? "warn" : "bad";
      chk = `<p>check-keys: <span class="badge ${cls}">${esc(r.check.status)}</span></p><table><thead><tr><th>Item</th><th>Status</th><th>Fact classes</th></tr></thead><tbody>${
        r.check.items.map((it) => `<tr><td class="mono">${esc(it.name)}</td><td>${esc(it.status)}</td><td class="mono">${esc(JSON.stringify(it.classes))}</td></tr>`).join("")}</tbody></table>`;
    } else if (!r.errors.length) {
      chk = `<p class="hint">${["text", "honesty"].includes(c.kind) ? "Text and honesty cases have no numeric key: check-keys skips them." : "Fact store unreachable: key check skipped."}</p>`;
    }
    $("dReport").innerHTML = errs + chk;
    $("dSummary").innerHTML = r.errors.length ? `<span class="badge bad">${r.errors.length} error(s)</span>`
      : r.check ? `<span class="badge ${r.check.status === "verified" ? "ok" : "warn"}">${esc(r.check.status)}</span>` : `<span class="badge ok">valid</span>`;
  } catch (e) { $("dSummary").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<p class="err">Failed to start: ${esc(e.message)}</p>`); });
