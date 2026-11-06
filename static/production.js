"use strict";
// ======================================================================== Produzione
// Ordini di produzione (modelli × copie su stampanti scelte, con priorità) e pianificazione a Gantt.
// Usa le funzioni comuni di app.js: t, esc, api, act, openDialog, $, $$, S, RENDER, fmtDur, canOperate, isAdmin.

const PRIO = {0: "Bassa", 1: "Normale", 2: "Alta", 3: "Urgente"};
const USTATE = {pending: "In attesa", starting: "In partenza", printing: "In stampa", done: "Completato", failed: "Non riuscito", cancelled: "Saltato"};
const PSTATE = {active: "Attiva", paused: "In pausa", completed: "Completata", cancelled: "Annullata"};
const ZOOM_LABEL = {12: "12 h", 24: "24 h", 72: "3 giorni", 168: "7 giorni"};
const ZOOMS = Object.entries(ZOOM_LABEL).map(([h, l]) => [+h, l]);
let PTAB = localStorage.getItem("fb_ptab") || "orders";
let PZOOM = +(localStorage.getItem("fb_pzoom") || 24);
let PHILITE = null;          // ordine evidenziato nel Gantt
let PDATA = null, PPLAN = null, PTIMER = null;

const pmsg = m => { if (!m) return ""; try { const o = JSON.parse(m); return t(o.msg, o.params || {}); } catch { return m; } };
const whenAbs = ts => new Date(ts * 1000).toLocaleString(LOCALES[LANG], {weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
const whenRel = ts => ts <= Date.now() / 1000 + 60 ? t("adesso") : t("tra {d}", {d: fmtDur(ts - Date.now() / 1000)});
const fileKind = f => f.meta.format === "ufp" ? "ufp" : f.meta.kind === "gcode" ? "gcode" : "3mf";
function compatibleWith(p, kind) {
  const c = p.caps || {};
  if (kind === "3mf") return !p.direct;
  if (!p.direct || c.upload === false) return false;
  return kind !== "ufp" || !!c.ufp;
}

RENDER.production = async function renderProduction() {
  const el = $("#v-production");
  let data, plan;
  try {
    [data, plan] = await Promise.all([api("/api/productions"), api(`/api/production/plan?hours=${PZOOM}`)]);
  } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  PDATA = data; PPLAN = plan;
  const act_ = data.productions.filter(p => ["active", "paused"].includes(p.status));
  const pending = act_.reduce((n, p) => n + p.summary.pending + p.summary.printing, 0);
  const lastEta = Math.max(0, ...act_.map(p => p.eta || 0));
  $("#pbadge").textContent = act_.length || "";
  el.innerHTML = `<div class="head"><div><h1>${t("Produzione")}</h1>
      <p class="sub">${act_.length ? t("{a} ordini attivi · {b} pezzi da completare", {a: act_.length, b: pending}) + (lastEta ? " · " + t("fine prevista {w}", {w: whenAbs(lastEta)}) : "")
        : t("Nessun ordine di produzione attivo")}</p></div>
      ${canOperate() ? `<button class="primary" id="pnew">＋ ${t("Nuovo ordine")}</button>` : ""}</div>
    <div class="tabs" role="tablist"><a href="#" data-pt="orders" class="${PTAB === "orders" ? "on" : ""}" role="tab">${t("Ordini")}</a>
      <a href="#" data-pt="gantt" class="${PTAB === "gantt" ? "on" : ""}" role="tab">${t("Pianificazione")}</a></div>
    <div id="pbody"></div>`;
  $$("#v-production [data-pt]").forEach(a => a.onclick = e => { e.preventDefault(); PTAB = a.dataset.pt; localStorage.setItem("fb_ptab", PTAB); RENDER.production(); });
  if ($("#pnew")) $("#pnew").onclick = () => newOrderDialog();
  if (PTAB === "gantt") renderGantt(); else renderOrders();
  clearInterval(PTIMER);
  PTIMER = setInterval(() => { if (view === "production" && !document.hidden && !$("#dlg").open) RENDER.production(); }, 30000);
};

// ------------------------------------------------------------------------ ordini
function orderCard(p, i, list) {
  const s = p.summary, tot = Math.max(1, s.total);
  const seg = (n, cls) => n ? `<i class="${cls}" style="width:${100 * n / tot}%" title="${n}"></i>` : "";
  const open = ["active", "paused"].includes(p.status);
  const printers = p.printers.map(sn => S.printers.find(x => x.sn === sn)).filter(Boolean);
  return `<article class="porder ${p.status} ${PHILITE === p.id ? "hl" : ""}" style="--pc:${esc(p.color)}" data-pid="${p.id}">
    <div class="po-head"><span class="po-dot"></span><b class="po-name">${esc(p.name)}</b>
      <span class="prio p${p.priority}">${t(PRIO[p.priority])}</span>
      ${p.status !== "active" ? `<span class="bdg">${t(PSTATE[p.status])}</span>` : ""}
      <span class="grow"></span>
      ${open && canOperate() ? `<div class="po-actions">
        <button type="button" data-po="up" title="${esc(t("Sposta prima"))}" ${i === 0 ? "disabled" : ""}>↑</button>
        <button type="button" data-po="down" title="${esc(t("Sposta dopo"))}" ${i === list.length - 1 ? "disabled" : ""}>↓</button>
        <select data-po="prio" aria-label="${esc(t("Priorità"))}">${Object.entries(PRIO).map(([k, l]) => `<option value="${k}" ${+k === p.priority ? "selected" : ""}>${t(l)}</option>`).join("")}</select>
        <button type="button" data-po="${p.status === "paused" ? "resume" : "pause"}">${p.status === "paused" ? "▶ " + t("Riprendi") : "⏸ " + t("Pausa")}</button>
        <button type="button" class="danger" data-po="cancel">${t("Annulla")}</button></div>`
        : canOperate() && !open ? `<button type="button" class="linkbtn" data-po="delete">${t("Elimina")}</button>` : ""}</div>
    <div class="po-bar">${seg(s.done, "d")}${seg(s.printing, "r")}${seg(s.failed, "f")}${seg(s.pending, "w")}${seg(s.cancelled, "c")}</div>
    <div class="po-meta"><b>${s.done}/${s.total}</b> ${t("pezzi")}${s.printing ? " · " + t("{n} in stampa", {n: s.printing}) : ""}${s.failed ? ` · <span class="warn">${t("{n} non riusciti", {n: s.failed})}</span>` : ""}
      ${open && p.eta ? ` · ${t("fine stimata")} <b>${whenAbs(p.eta)}</b> <span class="muted">(${whenRel(p.eta)})</span>` : ""}
      ${p.status === "completed" && p.completed_at ? ` · ${t("completata {w}", {w: whenAbs(p.completed_at)})}` : ""}
      ${open && p.unplaced ? ` · <span class="warn">${t("{n} pezzi senza stampante disponibile", {n: p.unplaced})}</span>` : ""}</div>
    <div class="po-chips">${p.items.map(it => `<span class="chip">${esc(it.filename)}${it.plate > 1 ? ` · ${t("piatto {n}", {n: it.plate})}` : ""} <b>${it.done}/${it.copies}</b></span>`).join("")}
      <span class="po-sep"></span>${printers.map(x => `<span class="chip pr ${x.online ? "" : "off"}">${esc(x.name)}</span>`).join("")}</div>
    <details class="po-units"><summary>${t("Pezzi")} (${p.units.length})</summary>
      <div class="tablewrap"><table><thead><tr><th>#</th><th>${t("Modello")}</th><th>${t("Stato")}</th><th>${t("Stampante")}</th><th></th></tr></thead><tbody>
      ${p.units.map(u => `<tr data-uid="${u.id}"><td>${u.seq}</td><td>${esc(u.filename || "?")}</td>
        <td><span class="ust ${u.status}">${t(USTATE[u.status] || u.status)}</span>${u.attempts ? ` <small class="muted">${t("tentativo {n}", {n: u.attempts + 1})}</small>` : ""}
          ${u.message ? `<br><small class="muted">${esc(pmsg(u.message))}</small>` : ""}</td>
        <td>${esc(u.printer_name || "")}</td>
        <td>${canOperate() && ["failed", "cancelled"].includes(u.status) ? `<button type="button" data-u="retry">${t("Rilancia")}</button>` : ""}
          ${canOperate() && u.status === "pending" ? `<button type="button" class="linkbtn" data-u="skip">${t("Salta")}</button>` : ""}</td></tr>`).join("")}
      </tbody></table></div></details></article>`;
}

function renderOrders() {
  const box = $("#pbody");
  const list = PDATA.productions.filter(p => ["active", "paused"].includes(p.status));
  const closed = PDATA.productions.filter(p => !["active", "paused"].includes(p.status));
  box.innerHTML = (list.length ? `<div class="porders">${list.map(orderCard).join("")}</div>`
    : `<div class="empty pempty"><div class="big">🏭</div><p>${t("Crea un ordine di produzione: scegli i modelli, il numero di copie e le stampanti. FleetBuddy distribuisce i pezzi e ti mostra quando finirà.")}</p>
       ${canOperate() ? `<button class="primary" id="pnew2">＋ ${t("Nuovo ordine")}</button>` : ""}</div>`)
    + (closed.length ? `<details class="pclosed"><summary>${t("Concluse")} (${closed.length})</summary><div class="porders">${closed.map(orderCard).join("")}</div></details>` : "");
  if ($("#pnew2")) $("#pnew2").onclick = () => newOrderDialog();
  box.onclick = async e => {
    const card = e.target.closest("[data-pid]"); if (!card) return;
    const pid = +card.dataset.pid, b = e.target.closest("button");
    if (b && b.dataset.po) {
      const a = b.dataset.po;
      if (a === "up" || a === "down") await act(() => api(`/api/productions/${pid}/move`, {method: "POST", body: {direction: a}}));
      if (a === "pause" || a === "resume") await act(() => api(`/api/productions/${pid}`, {method: "PATCH", body: {status: a === "pause" ? "paused" : "active"}}));
      if (a === "cancel" && confirm(t("Annullare l'ordine? I pezzi non ancora avviati non verranno stampati; quelli in stampa terminano normalmente."))) await act(() => api(`/api/productions/${pid}/cancel`, {method: "POST"}));
      if (a === "delete" && confirm(t("Eliminare l'ordine dall'elenco? Lo storico delle stampe resta."))) await act(() => api(`/api/productions/${pid}`, {method: "DELETE"}));
      return RENDER.production();
    }
    if (b && b.dataset.u) {
      const uid = b.closest("[data-uid]").dataset.uid;
      await act(() => api(`/api/productions/units/${uid}/${b.dataset.u}`, {method: "POST"}));
      return RENDER.production();
    }
  };
  box.onchange = async e => {
    const s = e.target.closest('select[data-po="prio"]'); if (!s) return;
    await act(() => api(`/api/productions/${s.closest("[data-pid]").dataset.pid}`, {method: "PATCH", body: {priority: +s.value}}));
    RENDER.production();
  };
}

// ------------------------------------------------------------------------ Gantt
function renderGantt() {
  const box = $("#pbody"), pl = PPLAN;
  const now = pl.now, t0 = now - 3600, t1 = now + PZOOM * 3600;
  const pph = {12: 110, 24: 60, 72: 24, 168: 10}[PZOOM] || 40;          // pixel per ora
  const W = Math.round((t1 - t0) / 3600 * pph);
  const x = ts => Math.round((Math.max(t0, Math.min(t1, ts)) - t0) / 3600 * pph);
  const prods = Object.fromEntries(PDATA.productions.map(p => [p.id, p]));
  const step = PZOOM <= 12 ? 1 : PZOOM <= 24 ? 2 : PZOOM <= 72 ? 6 : 24;    // ore tra le etichette
  const ticks = [];
  const first = new Date(t0 * 1000); first.setMinutes(0, 0, 0);
  for (let ts = first.getTime() / 1000 + 3600; ts < t1; ts += 3600) {
    const d = new Date(ts * 1000), h = d.getHours();
    if (h % step) continue;
    ticks.push(`<div class="gt ${h === 0 ? "day" : ""}" style="left:${x(ts)}px"><span>${h === 0 || step >= 24
      ? d.toLocaleDateString(LOCALES[LANG], {weekday: "short", day: "2-digit", month: "2-digit"}) : d.toLocaleTimeString(LOCALES[LANG], {hour: "2-digit", minute: "2-digit"})}</span></div>`);
  }
  const rows = [...pl.rows].sort((a, b) => (b.online - a.online) || (b.used - a.used) || a.name.localeCompare(b.name));
  const KIND = {current: t("in stampa"), plan: t("pianificato"), queue: t("coda"), clear: t("cambio piatto"), starting: t("in partenza")};
  const bar = b => {
    const left = x(b.start), w = Math.max(3, x(b.end) - left);
    if (b.end <= t0 || b.start >= t1) return "";
    const p = prods[b.production_id];
    const col = b.color || (p && p.color) || "";
    const dim = PHILITE && b.production_id !== PHILITE ? " dim" : "";
    const tip = [p ? p.name : b.kind === "queue" ? t("Coda manuale") : "", b.label, `${KIND[b.kind] || ""}`,
      `${whenAbs(b.start)} → ${whenAbs(b.end)} (${fmtDur(b.end - b.start)})`].filter(Boolean).join("\n");
    return `<div class="gb ${b.kind}${col ? " pc" : ""}${b.paused ? " paused" : ""}${dim}" style="left:${left}px;width:${w}px;${col ? `--pc:${esc(col)}` : ""}"
      title="${esc(tip)}" ${b.production_id ? `data-hp="${b.production_id}"` : ""}>${b.kind === "current" ? `<i style="width:${b.progress || 0}%"></i>` : ""}
      <span>${w > 46 ? esc(b.kind === "clear" ? "⟲" : (p ? p.name + " · " : "") + (b.label || "").replace(/\.(gcode\.3mf|gcode|bgcode|ufp|3mf)$/i, "")) : ""}</span></div>`;
  };
  const etas = Object.entries(pl.eta).map(([id, ts]) => [prods[+id], ts]).filter(([p, ts]) => p && ts <= t1 && ts >= t0);
  const legend = PDATA.productions.filter(p => ["active", "paused"].includes(p.status));
  box.innerHTML = `<div class="gtools">
      <div class="seg" role="group">${ZOOMS.map(([h, l]) => `<button type="button" data-z="${h}" class="${h === PZOOM ? "on" : ""}">${t(l)}</button>`).join("")}</div>
      <div class="glegend">${legend.map(p => `<button type="button" class="chip ${PHILITE === p.id ? "on" : ""}" data-hp="${p.id}" style="--c:${esc(p.color)}"><i></i>${esc(p.name)}${p.eta ? ` · ${whenAbs(p.eta)}` : ""}</button>`).join("")}
        <span class="chip lg queue"><i></i>${t("Coda manuale")}</span><span class="chip lg clear"><i></i>${t("Cambio piatto ({m} min)", {m: Math.round(pl.changeover_s / 60)})}</span></div>
      ${isAdmin() ? `<label class="inline small">${t("Cambio piatto")} <input type="number" id="pco" min="0" max="240" value="${Math.round(pl.changeover_s / 60)}" style="width:64px"> min</label>` : ""}</div>
    ${rows.length ? `<div class="gantt">
      <div class="gl"><div class="gh">${t("Stampante")} · ${t("saturazione")}</div>
        ${rows.map(r => `<div class="gr ${r.online ? "" : "off"}"><b>${esc(r.name)}</b><small class="muted">${esc(r.model || "")} · ${r.online ? stateName(r.state) : t("Offline")}</small>
          ${r.saturation != null ? `<span class="gsat" title="${esc(t("Tempo occupato nelle prossime {h} ore", {h: PZOOM}))}"><i style="width:${r.saturation}%;${r.saturation > 85 ? "background:var(--err)" : r.saturation > 60 ? "background:var(--pause)" : ""}"></i><em>${r.saturation}%</em></span>` : ""}</div>`).join("")}</div>
      <div class="gw"><div class="gc" style="width:${W}px">
        <div class="gh">${ticks.join("")}${etas.map(([p, ts]) => `<div class="geta" style="left:${x(ts)}px;--pc:${esc(p.color)}" title="${esc(t("Fine stimata di {p}: {w}", {p: p.name, w: whenAbs(ts)}))}">⚑</div>`).join("")}</div>
        ${rows.map(r => `<div class="grow2 ${r.online ? "" : "off"}">${pl.bars.filter(b => b.sn === r.sn).map(bar).join("")}</div>`).join("")}
        <div class="gnow" style="left:${x(now)}px"><span>${t("adesso")}</span></div>
        ${ticks.map(tk => tk.replace('class="gt', 'class="gline gt')).join("")}
      </div></div></div>
      <p class="sub small">${t("La pianificazione segue le priorità e l'ordine degli ordini, la durata stimata di ogni piatto e il tempo di cambio piatto. Si aggiorna da sola: una stampa fallita viene rilanciata e il piano slitta.")}</p>`
      : `<div class="empty">${t("Nessuna stampante da pianificare.")}</div>`}`;
  $$("#pbody [data-z]").forEach(b => b.onclick = () => { PZOOM = +b.dataset.z; localStorage.setItem("fb_pzoom", PZOOM); RENDER.production(); });
  box.onclick = e => { const h = e.target.closest("[data-hp]"); if (!h) return; PHILITE = PHILITE === +h.dataset.hp ? null : +h.dataset.hp; renderGantt(); };
  if ($("#pco")) $("#pco").onchange = async e => { await act(() => api("/api/production/settings", {method: "PUT", body: {changeover_min: +e.target.value}}), t("Salvato")); RENDER.production(); };
  const gw = $("#pbody .gw"); if (gw) gw.scrollLeft = Math.max(0, x(now) - 80);
}

// ------------------------------------------------------------------------ nuovo ordine
async function newOrderDialog() {
  const files = (await api("/api/files").catch(() => [])).filter(f => (f.meta.plates || []).length);
  if (!files.length) return toast(t("Carica prima i modelli nell'archivio"), "error");
  const items = [{file_id: files[0].id, plate: files[0].meta.plates[0].index, copies: 1}];
  const fileOf = id => files.find(f => f.id === +id);
  openDialog(`<h3>${t("Nuovo ordine di produzione")}</h3>
    <div class="row"><label style="flex:1">${t("Nome dell'ordine")}<input name="pname" maxlength="80" placeholder="${esc(t("es. Staffe cliente Rossi"))}"></label></div>
    <label>${t("Priorità")}</label><div class="seg" id="pprio">${Object.entries(PRIO).map(([k, l]) => `<button type="button" data-v="${k}" class="${k === "1" ? "on" : ""} p${k}">${t(l)}</button>`).join("")}</div>
    <h4>${t("Modelli e copie")}</h4><div id="pitems"></div>
    <button type="button" id="padd">＋ ${t("Aggiungi modello")}</button>
    <h4>${t("Stampanti")}</h4><div class="row small"><button type="button" class="linkbtn" id="pall">${t("Tutte le compatibili")}</button>
      ${GROUPS.map(g => `<button type="button" class="linkbtn" data-grp="${g.id}">${esc(g.name)}</button>`).join("")}<button type="button" class="linkbtn" id="pnone">${t("Nessuna")}</button></div>
    <div id="pprn" class="pprn"></div>
    <label class="inline"><input type="checkbox" name="pretry" checked> ${t("Rilancia automaticamente i pezzi non riusciti")}</label>
    <label class="inline"><input type="checkbox" name="ppaused"> ${t("Crea in pausa (non avviare subito)")}</label>
    <div id="pprev" class="pprev"></div>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button class="primary" id="psave">${t("Crea ordine")}</button></div>`);
  const fm = $("#dlgbody");
  let prio = 1;
  const kinds = () => [...new Set(items.map(it => fileKind(fileOf(it.file_id))))];
  const drawItems = () => {
    $("#pitems").innerHTML = items.map((it, i) => {
      const f = fileOf(it.file_id), pls = f.meta.plates;
      const pl = pls.find(x => x.index === it.plate) || pls[0];
      return `<div class="pitem" data-i="${i}">
        <select data-k="file">${files.map(x => `<option value="${x.id}" ${x.id === it.file_id ? "selected" : ""}>${esc(x.filename)}</option>`).join("")}</select>
        ${pls.length > 1 ? `<select data-k="plate">${pls.map(x => `<option value="${x.index}" ${x.index === it.plate ? "selected" : ""}>${t("Piatto {n}", {n: x.index})}</option>`).join("")}</select>` : ""}
        <label class="inline">× <input type="number" data-k="copies" min="1" max="999" value="${it.copies}" style="width:72px"></label>
        <small class="muted">${fmtDur(pl.prediction_s)} · ${pl.weight_g || 0} g</small>
        ${items.length > 1 ? `<button type="button" class="linkbtn" data-k="del" aria-label="${esc(t("Rimuovi"))}">✕</button>` : ""}</div>`;
    }).join("");
  };
  const checked = new Set(S.printers.filter(p => p.online && kinds().every(k => compatibleWith(p, k))).map(p => p.sn));
  const drawPrinters = () => {
    const ks = kinds();
    $("#pprn").innerHTML = [...S.printers].sort((a, b) => (a.brand || "").localeCompare(b.brand || "") || a.name.localeCompare(b.name)).map(p => {
      const ok = ks.every(k => compatibleWith(p, k));
      if (!ok) checked.delete(p.sn);
      return `<label class="pp ${ok ? "" : "no"} ${p.online ? "" : "off"}" title="${ok ? "" : esc(t("Non adatta ai file scelti"))}"><input type="checkbox" value="${esc(p.sn)}" ${checked.has(p.sn) ? "checked" : ""} ${ok ? "" : "disabled"}>
        <b>${esc(p.name)}</b><small class="muted">${esc([p.brand, p.printer_model || p.model].filter(Boolean).join(" "))}${p.online ? "" : " · " + t("Offline")}</small></label>`;
    }).join("");
  };
  let tmr;
  const preview = () => { clearTimeout(tmr); tmr = setTimeout(async () => {
    const body = {name: fm.elements.namedItem("pname").value, priority: prio, items, printers: [...checked]};
    if (!body.printers.length) { $("#pprev").innerHTML = `<span class="warn">${t("Scegli almeno una stampante")}</span>`; return; }
    try {
      const r = await api("/api/productions/preview", {method: "POST", body});
      $("#pprev").innerHTML = `<div class="kpis mini">
        <div class="kpi"><b>${r.pieces}</b><span>${t("pezzi")}</span></div>
        <div class="kpi"><b>${r.hours} h</b><span>${t("ore macchina")}</span></div>
        <div class="kpi"><b>${r.eta ? whenAbs(r.eta) : "—"}</b><span>${r.eta ? t("fine stimata ({w})", {w: whenRel(r.eta)}) : t("fine stimata")}</span></div></div>
        <div class="small muted">${Object.entries(r.per_printer).map(([sn, n]) => `${esc((S.printers.find(p => p.sn === sn) || {}).name || sn)}: ${n}`).join(" · ")}</div>
        ${r.unplaced ? `<div class="warn small">${t("{n} pezzi senza stampante disponibile (stampanti offline o non adatte)", {n: r.unplaced})}</div>` : ""}`;
    } catch (e) { $("#pprev").innerHTML = `<span class="warn">${esc(e.message)}</span>`; }
  }, 350); };
  drawItems(); drawPrinters(); preview();
  $("#pprio").onclick = e => { const b = e.target.closest("[data-v]"); if (!b) return; prio = +b.dataset.v; $$("#pprio button").forEach(x => x.classList.toggle("on", x === b)); preview(); };
  $("#padd").onclick = () => { items.push({file_id: items[items.length - 1].file_id, plate: items[items.length - 1].plate, copies: 1}); drawItems(); drawPrinters(); preview(); };
  $("#pitems").onchange = $("#pitems").oninput = e => {
    const row = e.target.closest("[data-i]"); if (!row) return;
    const it = items[+row.dataset.i], k = e.target.dataset.k;
    if (k === "file") { it.file_id = +e.target.value; it.plate = fileOf(it.file_id).meta.plates[0].index; drawItems(); drawPrinters(); }
    if (k === "plate") it.plate = +e.target.value;
    if (k === "copies") it.copies = Math.max(1, Math.min(999, +e.target.value || 1));
    preview();
  };
  $("#pitems").onclick = e => { if (e.target.dataset.k !== "del") return; items.splice(+e.target.closest("[data-i]").dataset.i, 1); drawItems(); drawPrinters(); preview(); };
  $("#pprn").onchange = e => { const c = e.target; if (c.type !== "checkbox") return; c.checked ? checked.add(c.value) : checked.delete(c.value); preview(); };
  $("#pall").onclick = () => { S.printers.filter(p => kinds().every(k => compatibleWith(p, k))).forEach(p => checked.add(p.sn)); drawPrinters(); preview(); };
  $("#pnone").onclick = () => { checked.clear(); drawPrinters(); preview(); };
  $$("#dlgbody [data-grp]").forEach(b => b.onclick = () => { const g = GROUPS.find(x => x.id === +b.dataset.grp);
    checked.clear(); g.printers.forEach(sn => { const p = S.printers.find(x => x.sn === sn); if (p && kinds().every(k => compatibleWith(p, k))) checked.add(sn); }); drawPrinters(); preview(); });
  fm.elements.namedItem("pname").oninput = preview;
  $("#psave").onclick = async ev => {
    ev.preventDefault();
    const body = {name: fm.elements.namedItem("pname").value || t("Produzione"), priority: prio, items, printers: [...checked],
      auto_retry: fm.elements.namedItem("pretry").checked, paused: fm.elements.namedItem("ppaused").checked};
    if (await act(() => api("/api/productions", {method: "POST", body}), t("Ordine creato"))) { $("#dlg").close(); PTAB = "gantt"; localStorage.setItem("fb_ptab", PTAB); RENDER.production(); }
  };
}
