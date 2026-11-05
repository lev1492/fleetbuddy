"use strict";
// FleetBuddy – frontend senza build step (adatto al Raspberry Pi)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// ------------------------------------------------------------------ lingue
// Il testo di riferimento è l'italiano; static/i18n/<lingua>.json contiene le traduzioni.
const LANGS = {it: "Italiano", en: "English", fr: "Français", de: "Deutsch", es: "Español", pt: "Português"};
const LOCALES = {it: "it-IT", en: "en-GB", fr: "fr-FR", de: "de-DE", es: "es-ES", pt: "pt-PT"};
let LANG = "it", CAT = {};
function t(msg, params) {
  // "testo|contesto": il contesto distingue parole uguali in italiano ma diverse in altre lingue
  let s = CAT[msg] ?? String(msg).split("|")[0];
  if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => k in params ? params[k] : m);
  return s;
}
function detectLang() {
  const saved = localStorage.getItem("fb_lang");
  if (saved && LANGS[saved]) return saved;
  for (const l of navigator.languages || [navigator.language || "it"]) { const c = String(l).slice(0, 2).toLowerCase(); if (LANGS[c]) return c; }
  return "it";
}
async function setLang(lang, save = true) {
  LANG = LANGS[lang] ? lang : "it";
  CAT = LANG === "it" ? {} : await fetch(`/static/i18n/${LANG}.json?v=${APP.version}`).then(r => r.json()).catch(() => ({}));
  localStorage.setItem("fb_lang", LANG);
  document.cookie = `fb_lang=${LANG}; path=/; max-age=31536000; samesite=lax`;
  document.documentElement.lang = LANG;
  $$("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  $$("select.langsel").forEach(s => { s.value = LANG; });
  if (save && ME) api("/api/me", {method: "PATCH", body: {language: LANG}}).catch(() => {});
}
const langOptions = () => Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${k === LANG ? "selected" : ""}>${v}</option>`).join("");

const STATES = {IDLE: "Inattiva", PREPARE: "Preparazione", RUNNING: "In stampa", PAUSE: "In pausa", PAUSED: "In pausa",
  FINISH: "Completata", FAILED: "Fallita", UNKNOWN: "Sconosciuto", SLICING: "Preparazione"};
const JOBS = {finished: "Completata", failed: "Fallita", cancelled: "Annullata", running: "In corso", unknown: "Interrotta",
  queued: "In attesa", sent: "In partenza", error: "Errore"};
const ROLES = {admin: "Amministratore", operator: "Operatore", viewer: "Osservatore"};
const SPEEDS = {1: "Silenziosa", 2: "Normale", 3: "Sport", 4: "Ludicrous"};
const stateName = s => t(STATES[s] || s);
const jobName = s => t(JOBS[s] || s);
const roleName = r => t(ROLES[r] || r);
const trayLabel = l => l === "EXT" ? t("Esterna") : l === "EXT_R" ? t("Esterna dx") : l === "EXT_L" ? t("Esterna sx") : l;

let APP = {name: "FleetBuddy", version: "", repo: ""}, UPDATE = null;
let S = {printers: [], hub_ok: false, settings: {camera_refresh_s: 60}}, view = "printers", presets = [];
let ME = null, GROUPS = [], PROJECTS = [], groupFilter = localStorage.getItem("fb_group") || "", es = null;
const isAdmin = () => ME && ME.role === "admin";
const canOperate = () => ME && ME.role !== "viewer";

async function api(path, opts = {}) {
  const o = {...opts, headers: {...(opts.headers || {}), "Accept-Language": LANG}, credentials: "same-origin"};
  if (o.body && !(o.body instanceof FormData)) { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (r.status === 401 && !path.startsWith("/api/login") && !path.startsWith("/api/setup")) { showLogin(); throw new Error(t("Sessione scaduta: accedi di nuovo")); }
  if (!r.ok) throw new Error(data.detail || data.error || data || r.statusText);
  return data;
}
function toast(text, level = "info") {
  const el = document.createElement("div"); el.className = "toast " + level; el.textContent = text;
  $("#toasts").append(el); setTimeout(() => el.remove(), level === "error" ? 9000 : 4500);
}
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(okMsg, "ok"); return r; } catch (e) { toast(e.message, "error"); } }
const fmtDur = s => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? t("{h} h {m} min", {h, m}) : t("{m} min", {m}); };
const fmtDate = ts => ts ? new Date(ts * 1000).toLocaleString(LOCALES[LANG], {dateStyle: "short", timeStyle: "short"}) : "—";
const dots = fils => `<span class="fdots">${(fils || []).map(f => `<i style="--c:${esc(f.color)}" title="${esc(f.type)}"></i>`).join("")}</span>`;
const printerName = sn => (S.printers.find(p => p.sn === sn) || {}).name || sn || t("Qualsiasi");
const projectOf = id => PROJECTS.find(p => p.id === id) || {name: t("Senza progetto"), color: "#8A949C"};
const projName = p => p.id === null ? t("Senza progetto") : p.name;
// messaggi di coda salvati dal server come {"msg": frase, "params": {...}}
const qMsg = m => { try { const o = JSON.parse(m); return t(o.msg, o.params); } catch { return m; } };
function openDialog(html) { $("#dlgbody").innerHTML = html; $("#dlg").showModal(); }
const btnCancel = () => `<button value="cancel">${t("Annulla")}</button>`;

// ------------------------------------------------------------------ accesso
async function boot() {
  const s = await fetch("/api/session").then(r => r.json()).catch(() => ({user: null}));
  if (s.app) { APP = s.app; UPDATE = s.update || null; showVersion(); }
  if (!s.user) { await setLang(detectLang(), false); return showLogin(s.setup_needed); }
  ME = s.user;
  await setLang(ME.language || detectLang(), !ME.language);
  document.body.classList.remove("locked");
  document.body.classList.toggle("is-admin", isAdmin());
  document.body.classList.toggle("is-viewer", ME.role === "viewer");
  $("#login").hidden = true;
  $("#mename").textContent = `${ME.username} · ${roleName(ME.role)}`;
  await loadMeta();
  connect(); go();
  if (ME.must_change_pw) firstLoginDialog();
}
// Primo accesso con una password assegnata dall'amministratore: proposta di cambiarla (facoltativa)
function firstLoginDialog() {
  openDialog(`<h3>${t("Benvenuto in FleetBuddy")}</h3>
    <p class="sub" style="margin:0">${t("La tua password è stata impostata dall'amministratore. Puoi sceglierne una tua adesso, oppure continuare a usare quella attuale.")}</p>
    <label>${t("Password attuale")}<input type="password" name="old" autocomplete="current-password"></label>
    <label>${t("Nuova password")}<input type="password" name="new" autocomplete="new-password"></label>
    <p class="sub" style="margin:0">${t("Almeno 8 caratteri, con lettere e numeri.")}</p>
    <div class="dlgfoot"><button type="button" id="pwkeep">${t("Mantieni quella attuale")}</button><button class="primary" id="pwnew">${t("Cambia password")}</button></div>`);
  $("#pwkeep").onclick = async () => { await act(() => api("/api/me/keep_password", {method: "POST"})); ME.must_change_pw = false; $("#dlg").close(); };
  $("#pwnew").onclick = async ev => {
    ev.preventDefault(); const f = $("#dlgbody");
    if (await act(() => api("/api/me/password", {method: "POST", body: {old: f.old.value, new: f.new.value}}), t("Password aggiornata"))) { ME.must_change_pw = false; $("#dlg").close(); }
  };
}
function showVersion() {
  const label = `${esc(APP.name)} v${esc(APP.version)}`;
  const html = APP.repo ? `<a href="${esc(APP.repo)}" target="_blank" rel="noopener">${label}</a>` : label;
  $("#appver").innerHTML = html; $("#loginver").innerHTML = html;
  // pallino sulla voce Amministrazione quando c'è un aggiornamento da installare
  const adm = $('.nav a[data-view="admin"]');
  if (adm) adm.classList.toggle("has-update", !!(UPDATE && UPDATE.available));
  document.title = APP.name;
}
async function loadMeta() {
  [GROUPS, PROJECTS] = await Promise.all([api("/api/groups").catch(() => []), api("/api/projects").catch(() => [])]);
  if (groupFilter && !GROUPS.some(g => String(g.id) === groupFilter)) groupFilter = "";
}
function showLogin(setup = false) {
  if (es) { es.close(); es = null; }
  document.body.classList.add("locked");
  $("#login").hidden = false;
  const f = $("#loginform");
  f.dataset.setup = setup ? "1" : "";
  f.password.autocomplete = setup ? "new-password" : "current-password";
  renderLoginTexts();
  f.username.focus();
}
function renderLoginTexts() {
  const setup = !!$("#loginform").dataset.setup;
  $("#loginintro").textContent = setup ? t("Primo avvio: crea l'account amministratore di FleetBuddy.") : t("Accedi per gestire le stampanti.");
  $("#loginbtn").textContent = setup ? t("Crea amministratore") : t("Accedi");
}
$("#loginform").onsubmit = async e => {
  e.preventDefault();
  const f = e.target, setup = !!f.dataset.setup;
  $("#loginerr").textContent = "";
  try {
    await api(setup ? "/api/setup" : "/api/login", {method: "POST", body: {username: f.username.value, password: f.password.value, language: LANG}});
    f.password.value = ""; boot();
  } catch (err) { $("#loginerr").textContent = err.message; }
};
$("#loginlang").onchange = async e => { await setLang(e.target.value, false); renderLoginTexts(); };
$("#melang").onchange = async e => { await setLang(e.target.value); $("#mename").textContent = `${ME.username} · ${roleName(ME.role)}`; reRender(); };
$("#logout").onclick = async () => { await api("/api/logout", {method: "POST"}).catch(() => {}); location.hash = ""; location.reload(); };
$("#mepwd").onclick = () => {
  openDialog(`<h3>${t("Cambia la tua password")}</h3>
    <label>${t("Password attuale")}<input type="password" name="old" autocomplete="current-password"></label>
    <label>${t("Nuova password")}<input type="password" name="new" autocomplete="new-password"></label>
    <p class="sub" style="margin:0">${t("Almeno 8 caratteri, con lettere e numeri.")}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="pwok">${t("Salva")}</button></div>`);
  $("#pwok").onclick = async ev => { ev.preventDefault(); const f = $("#dlgbody");
    if (await act(() => api("/api/me/password", {method: "POST", body: {old: f.old.value, new: f.new.value}}), t("Password aggiornata"))) $("#dlg").close(); };
};

// ------------------------------------------------------------------ navigazione
const RENDER = {};
function go() {
  view = (location.hash || "#printers").slice(1);
  if (["hub", "admin", "direct"].includes(view) && !isAdmin()) view = "printers";
  const navView = view === "hub" || view === "direct" ? "admin" : view;   // schede di Amministrazione
  $$(".nav a").forEach(a => a.classList.toggle("on", a.dataset.view === navView));
  $$(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + view));
  (RENDER[view] || RENDER.printers)();
}
const adminTabs = active => `<div class="tabs" role="tablist">
  <a href="#admin" role="tab" aria-selected="${active === "admin"}" class="${active === "admin" ? "on" : ""}">${t("Utenti e gruppi")}</a>
  <a href="#hub" role="tab" aria-selected="${active === "hub"}" class="${active === "hub" ? "on" : ""}">Fleet Hub</a>
  <a href="#direct" role="tab" aria-selected="${active === "direct"}" class="${active === "direct" ? "on" : ""}">${t("Altre stampanti")}</a></div>`;
function reRender() { $("#v-printers")._built = false; CARDS.clear(); go(); }
window.addEventListener("hashchange", () => ME && go());

// ------------------------------------------------------------------ eventi live (SSE)
// I messaggi del server arrivano come frase italiana + parametri e vengono tradotti qui.
function connect() {
  if (es) es.close();
  es = new EventSource("/api/events");
  es.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.kind === "state") {
      const oldRefresh = S.settings && S.settings.camera_refresh_s;
      S = m.data;
      const hs = $("#hubstate");
      hs.className = "hubstate " + (S.hub_ok ? "ok" : "ko");
      hs.textContent = S.hub_ok ? t("Hub connesso · {n} stampanti", {n: S.printers.length}) : (S.hub_error || t("Hub non raggiungibile"));
      $("#qbadge").textContent = S.queue_count || "";
      if (S.settings && S.settings.camera_refresh_s !== oldRefresh) startCamTimer();
      if (view === "printers") RENDER.printers();
    } else if (m.kind === "toast") {
      const d = m.data, p = {...(d.params || {})};
      if (p.status) p.status = jobName(p.status);
      toast(t(d.msg, p), d.level);
    }
  };
  es.onerror = async () => {
    const s = await fetch("/api/session").then(r => r.json()).catch(() => null);
    if (s && !s.user) return showLogin();
    $("#hubstate").className = "hubstate ko"; $("#hubstate").textContent = t("FleetBuddy non raggiungibile");
  };
}

// ------------------------------------------------------------------ stampanti
// Le schede vengono create una volta e poi aggiornate sul posto: l'immagine della camera
// non viene mai ricreata, quindi non sparisce né lampeggia quando arriva un nuovo stato.
const hmsDesc = {};
const IMGKIND = {};   // per stampante: "camera" oppure "thumbnail" (anteprima della stampa, quando manca la camera)
function snapAge(p) {
  if (IMGKIND[p.sn] === "thumbnail") return t("Anteprima della stampa (nessuna camera)");
  // solo l'orario della foto: il testo cambia quando arriva una foto nuova, non ogni minuto
  const ts = Date.parse(p.liveview_time || "");
  if (!ts || ts < Date.UTC(2000, 0, 1)) return p.direct ? t("Nessuna camera: durante la stampa compare l'anteprima") : t("Nessuna istantanea dal Hub");
  const d = new Date(ts), today = new Date().toDateString() === d.toDateString();
  return t("Foto delle {time}", {time: d.toLocaleString(LOCALES[LANG], today ? {timeStyle: "short"} : {dateStyle: "short", timeStyle: "short"})});
}
function activeColor(p) { const l = p.trays.filter(x => x.loaded); return l.length === 1 ? l[0].color : ""; }
const FLOW = {standard: "Flusso standard", high: "Alto flusso", tpu: "TPU alto flusso"};
const MATERIAL = {stainless: "Acciaio inox", hardened: "Acciaio temprato", tungsten: "Carburo di tungsteno"};
const deg = (v, target) => v == null ? "—" : `${Math.round(v)}°${target ? `<small>/${Math.round(target)}°</small>` : ""}`;
const sideLetter = side => side === "left" ? t("SX") : side === "right" ? t("DX") : "";
function nozzleType(n) {
  const parts = [n.flow ? t(FLOW[n.flow]) : "", n.material ? t(MATERIAL[n.material]) : ""].filter(Boolean);
  if (parts.length) return parts.join(" · ") + (n.code && /^H/i.test(n.code) ? ` <span class="muted">(${esc(n.code)})</span>` : "");
  return n.code ? esc(n.code) : `<span class="muted">${t("non comunicato")}</span>`;
}
function cardTop(p) {
  const n0 = p.nozzles[0] || {};
  const diam = p.nozzles.length > 1 ? p.nozzles.map(n => n.diameter ? `${sideLetter(n.side)} ${n.diameter}` : "").filter(Boolean).join(" · ")
    : (n0.diameter ? `${n0.diameter} mm` : "");
  const alerts = (p.print_error ? 1 : 0) + p.hms.length;
  const badges = [
    `<span class="bdg ${p.online ? "ok" : "off"}">${p.online ? t("Connessa") : t("Offline")}</span>`,
    p.direct ? `<span class="bdg">${esc(p.kind_label || p.kind)}${p.caps && p.caps.mode ? " · " + (p.caps.mode === "sbc" ? "SBC" : t("autonoma")) : ""}</span>` : "",
    p.wifi ? `<span class="bdg">${esc(p.wifi)}</span>` : "",
    `<span class="bdg ${alerts ? "err" : "ok"}">${alerts ? t("{n} avvisi", {n: alerts}) : t("Nessun errore")}</span>`,
    p.firmware ? `<span class="bdg ${p.fw_min ? "warn" : ""}" title="${p.fw_min ? esc(t("Firmware {v} inferiore al minimo supportato dal Hub ({min}).", {v: p.firmware, min: p.fw_min})) : ""}">fw ${esc(p.firmware)}</span>` : "",
  ].join("");
  return `<div class="pc-title"><div class="pc-name">${esc(p.name)}</div>
      <div class="pc-model">${esc([p.brand, p.printer_model || p.model].filter(Boolean).join(" "))}${diam ? " · " + esc(diam) : ""} · ${esc(p.sn)}</div></div>
    <div class="bdgs">${badges}</div>`;
}
function statusInfo(p) {
  const done = p.state === "FINISH" || p.state === "FAILED";
  const chip = done ? `<span class="chipst warn">${t("Piatto da liberare")}</span>`
    : p.state === "IDLE" && p.online ? `<span class="chipst ok">${t("Pronta")}</span>` : "";
  const fc = activeColor(p);
  const showJob = p.active || done;
  const next = p.next_job ? `<div class="nextq"><span class="muted">${t("Prossima in coda")}</span>
      <b title="${esc(p.next_job.filename)}">${esc(p.next_job.filename || "—")}</b>${p.next_job.more ? `<span class="bdg">+${p.next_job.more}</span>` : ""}</div>` : "";
  return `<div class="st-line"><b>${p.online ? stateName(p.state) : t("Offline")}</b>${chip}${p.status_text ? `<span class="muted small">${esc(p.status_text)}</span>` : ""}</div>
    <div class="task" title="${esc(p.task_name)}">${showJob ? esc(p.task_name || t("Stampa")) : `<span class="muted">${t("Nessun lavoro in corso")}</span>`}</div>
    <div class="progrow"><div class="strand" style="${fc ? "--fc:" + fc : ""}"><i style="width:${showJob ? p.progress : 0}%"></i></div><b>${showJob ? p.progress + "%" : "—"}</b></div>
    <div class="muted small">${p.active ? t("mancano {d}", {d: fmtDur(p.remaining_min * 60)}) + " · " + t("strato {a}/{b}", {a: p.layer, b: p.total_layers})
      : p.online ? (done ? t("Conferma che il piatto è libero per la prossima stampa") : t("Pronta per una nuova stampa")) : t("Non raggiungibile dal Hub")}</div>
    ${p.pending ? `<div class="small ${p.pending.warned ? "warn" : "muted"}">${p.pending.warned ? t("La stampa {f} non è partita: controlla la stampante", {f: esc(p.pending.task)})
      : t("Comando inviato: in attesa che la stampante scarichi {f} e parta…", {f: esc(p.pending.task)})}</div>` : ""}
    <div class="muted small camage">${snapAge(p)}</div>${next}`;
}
function slotBtn(x, num) {
  const pct = x.remain != null && x.remain >= 0 ? x.remain : null;
  return `<button class="sp slot ${x.loaded ? "" : "empty"}" data-ams="${x.ams_id}" data-slot="${x.slot_id}"
    title="${esc(trayLabel(x.label))}${x.loaded ? " · " + esc([x.type, x.sub_brand].filter(Boolean).join(" ")) : ""}${x.rfid ? " (RFID)" : ""}${pct != null ? " · " + esc(t("residuo {p}%", {p: pct})) + (x.remain_g ? ` (~${x.remain_g} g)` : "") : ""}">
    <span class="num" style="--c:${x.loaded ? x.color || "#888" : "transparent"}">${num}</span>
    <span class="ty">${x.loaded ? esc(x.type) : t("Vuoto")}</span>
    <span class="bar"><i style="width:${pct ?? 0}%;${pct != null && pct < 15 ? "background:var(--pause)" : ""}"></i></span></button>`;
}
function filamentCards(p) {
  const dual = p.nozzles.length > 1;
  const sideOf = id => { const n = p.nozzles.find(x => x.id === id); return n ? sideLetter(n.side) : ""; };
  const cards = p.ams_units.map(u => {
    const spools = p.trays.filter(x => x.kind === "ams" && x.ams_id === u.ams_id);
    const side = dual && u.nozzle != null ? sideOf(u.nozzle) : "";
    return `<div class="fcard"><div class="fcard-h"><b>${esc(u.kind)} ${esc(u.letter)}</b>${side ? `<span class="side">${side}</span>` : ""}
        <span class="fcard-m">${u.humidity != null ? `<span>💧 ${u.humidity}%</span>` : ""}${u.temp ? `<span>🌡 ${u.temp}°C</span>` : ""}</span></div>
      <div class="slots">${spools.map((x, i) => slotBtn(x, i + 1)).join("")}</div></div>`;
  });
  const ext = p.trays.filter(x => x.kind === "external").sort((a, b) => (b.nozzle ?? 0) - (a.nozzle ?? 0));   // sinistra (1) prima
  if (ext.length) cards.push(`<div class="fcard"><div class="fcard-h"><b>${t("Bobina esterna")}</b></div>
    <div class="slots">${ext.map(x => slotBtn(x, dual ? sideLetter(x.nozzle === 1 ? "left" : "right") : "E")).join("")}</div></div>`);
  return cards.length ? `<div class="fcards">${cards.join("")}</div>` : `<p class="muted small">${t("Nessun AMS o bobina collegati")}</p>`;
}
function cardBody(p) {
  const done = p.state === "FINISH" || p.state === "FAILED";
  const dual = p.nozzles.length > 1;
  const direct = !!p.direct, caps = p.caps || {}, editT = direct && caps.temperature && canOperate() && p.online;
  const tiles = [];
  const tile = (label, v, tgt, key, fault) => {
    const val = fault ? `<span class="warn" title="${esc(t("Sensore di temperatura guasto o scollegato"))}">⚠ ${t("sensore")}</span>` : deg(v, tgt);
    return editT && !fault && !(key === "chamber" && caps.chamber_heater === false)
      ? `<button type="button" class="tile edit" data-temp="${key}" data-label="${esc(label)}" data-target="${tgt || 0}" title="${esc(t("Clicca per impostare la temperatura"))}"><span>${label} ✎</span><b>${val}</b></button>`
      : `<div class="tile"><span>${label}</span><b>${val}</b></div>`;
  };
  if (direct) {
    p.nozzles.forEach(n => tiles.push(tile(esc(n.label || sideLetter(n.side) || t("Ugello")) + (n.active && p.nozzles.length > 1 ? " ●" : ""), n.temp, n.target, `tool:${n.id}`, n.fault)));
    if ((p.beds || []).length > 1) p.beds.forEach(b => tiles.push(tile(`${t("Piatto|letto")} ${esc(b.label)}`, b.temp, b.target, b.key, b.fault)));
    else tiles.push(tile(t("Piatto|letto"), p.bed, p.bed_target, "bed", p.beds && p.beds[0] && p.beds[0].fault));
    if (p.chamber != null) tiles.push(tile(t("Camera"), p.chamber, p.chamber_target, "chamber"));
    // dati dell'ugello per utensile, quando la stampante li comunica (es. variabili FabbrixOS sulla Duet)
    if (p.nozzles.some(n => n.diameter || n.type_name || n.filament_sensor))
      tiles.push(`<div class="tile wide"><span>${t("Utensili")}</span>${p.nozzles.map(n => `<div class="hot"><b class="side">${esc(n.label)}</b>
        ${n.diameter ? `<b>${n.diameter} mm</b>` : ""} <small>${[n.type_name ? esc(n.type_name) : "", n.abrasive ? t("per abrasivi") : "",
          n.filament_sensor ? (n.filament_sensor === "ok" ? t("filamento presente") : `<span class="warn">${t("filamento assente")}</span>`) : "",
          n.hot_hours != null ? t("{h} h di riscaldamento", {h: n.hot_hours}) : "", n.present === false ? `<span class="warn">${t("utensile non montato")}</span>` : ""].filter(Boolean).join(" · ")}</small></div>`).join("")}</div>`);
  } else if (dual) {
    const L = p.nozzles.find(n => n.side === "left") || p.nozzles[0], R = p.nozzles.find(n => n.side === "right") || p.nozzles[1];
    tiles.push(`<div class="tile"><span>${t("Ugelli")} ${sideLetter("left")} / ${sideLetter("right")}</span><b>${deg(L.temp, L.target)} / ${deg(R.temp, R.target)}</b></div>`);
  } else {
    const n = p.nozzles[0] || {temp: p.nozzle, target: p.nozzle_target};
    tiles.push(`<div class="tile"><span>${t("Ugello")}</span><b>${deg(n.temp, n.target)}</b></div>`);
  }
  if (!direct) {
    tiles.push(`<div class="tile"><span>${t("Piatto|letto")}</span><b>${deg(p.bed, p.bed_target)}</b></div>`);
    if (p.chamber != null) tiles.push(`<div class="tile"><span>${t("Camera")}</span><b>${deg(p.chamber, p.chamber_target)}</b></div>`);
    tiles.push(`<div class="tile wide"><span>${t("Hotend")}</span>${p.nozzles.map(n => `<div class="hot">${dual ? `<b class="side">${sideLetter(n.side)}</b>` : ""}<b>${n.diameter ? n.diameter + " mm" : "—"}</b> <small>${nozzleType(n)}</small></div>`).join("")}</div>`);
  }
  const f = p.fans || {};
  const fans = [["part", t("Ventola pezzo")], ["aux", t("Ventola ausiliaria")], ["chamber", t("Ventola camera")]].filter(([k]) => f[k] != null);
  const btns = [];
  if (canOperate()) {
    if (p.state === "RUNNING") btns.push(`<button data-opt="pause">${t("Pausa")}</button>`);
    if (p.state === "PAUSE" || p.state === "PAUSED") btns.push(`<button data-opt="resume" class="primary">${t("Riprendi")}</button>`);
    if (["RUNNING", "PAUSE", "PAUSED", "PREPARE"].includes(p.state)) btns.push(`<button data-opt="stop" class="danger">${t("Interrompi")}</button>`);
  }
  const alerts = [...(p.print_error ? [p.print_error] : []), ...p.hms]
    .map(c => `<div class="alert">${c} ${esc(hmsDesc[p.model + c] || "")}</div>`).join("")
    + (p.alerts || []).map(a => `<div class="alert">${esc(a.msg ? t(a.msg, a.params || {}) : a.text)}</div>`).join("");
  const groups = GROUPS.filter(g => g.printers.includes(p.sn));
  return `<div class="tiles">${tiles.join("")}</div>
    ${fans.length ? `<div class="tiles fans">${fans.map(([k, l]) => `<div class="tile"><span>${l}</span><b>${f[k]}%</b></div>`).join("")}</div>` : ""}
    ${done && canOperate() && (!p.direct || caps.bed_clean) ? `<button class="platebtn" data-opt="bed_clean">${t("Segna il piatto come liberato")}</button>` : ""}
    <div class="sec">${t("Comandi")}</div>
    <div class="ctrls">${caps.remote_files && canOperate() && p.online ? `<button type="button" data-remote>${t("File sulla stampante")}</button>` : ""}${direct ? (caps.speed ? `<button type="button" class="bdg edit" data-speed="${p.speed_pct}" ${canOperate() && p.online ? "" : "disabled"}>${t("Velocità")} · ${p.speed_pct}% ✎</button>`
      : `<span class="bdg">${t("Velocità")} · ${p.speed_pct}%</span>`)
      : `<span class="bdg">${t("Velocità")} · ${esc(t(SPEEDS[p.speed_lvl] || "—"))}</span>`}<span class="grow"></span>${btns.join("") || `<span class="muted small">${t("Nessun comando disponibile in questo stato")}</span>`}</div>
    ${direct ? "" : `<div class="sec">${t("Filamenti")}</div>
    ${filamentCards(p)}`}
    ${alerts ? `<div class="alerts">${alerts}</div>` : ""}
    ${p.last_cmd_error ? `<div class="warn">${t("Ultimo invio fallito: {e}", {e: esc(p.last_cmd_error)})}</div>` : ""}
    ${groups.length ? `<div>${groups.map(g => `<span class="tag" style="--c:${esc(g.color)}"><i></i>${esc(g.name)}</span>`).join(" ")}</div>` : ""}`;
}
// File nella memoria della stampante, per i tipi che non accettano caricamenti (es. Raise3D)
async function remoteFilesDialog(p, folder) {
  openDialog(`<h3>${esc(p.name)} – ${t("File sulla stampante")}</h3><p class="sub" style="margin:0">${esc(folder)}</p><div id="rfl" class="muted">${t("Caricamento…")}</div>
    <div class="dlgfoot"><button value="cancel">${t("Chiudi")}</button></div>`);
  let data;
  try { data = await api(`/api/printers/${p.sn}/files?dir=${encodeURIComponent(folder)}`); }
  catch (e) { $("#rfl").innerHTML = `<span class="warn">${esc(e.message)}</span>`; return; }
  const ready = p.online && p.state === "IDLE" && !p.pending;
  const up = folder.includes("/") ? folder.slice(0, folder.lastIndexOf("/")) : "";
  $("#rfl").className = "rfiles";
  $("#rfl").innerHTML = (up ? `<button type="button" data-dir="${esc(up)}">⬆ ${t("Cartella superiore")}</button>` : "")
    + data.dirs.map(d => `<button type="button" data-dir="${esc(folder + "/" + d)}">📁 ${esc(d)}</button>`).join("")
    + data.files.map(f => `<button type="button" data-file="${esc(folder + "/" + f)}" ${ready ? "" : "disabled"}>▶ ${esc(f)}</button>`).join("")
    + (!data.dirs.length && !data.files.length ? `<p class="muted">${t("Cartella vuota")}</p>` : "")
    + (ready ? "" : `<p class="warn small">${t("{p} non è pronta: i file si possono avviare solo con la stampante inattiva.", {p: esc(p.name)})}</p>`);
  $("#rfl").onclick = async e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.dir !== undefined) return remoteFilesDialog(p, b.dataset.dir);
    if (b.dataset.file && confirm(t("Avviare {f} su {p}?", {f: b.dataset.file.split("/").pop(), p: p.name}))) {
      if (await act(() => api(`/api/printers/${p.sn}/start_remote`, {method: "POST", body: {path: b.dataset.file}}), t("Stampa avviata"))) $("#dlg").close();
    }
  };
}
function tempDialog(p, target, label, current) {
  const max = target === "bed" ? 130 : target === "chamber" ? 80 : 320;
  openDialog(`<h3>${esc(p.name)} – ${label}</h3>
    <label>${t("Temperatura obiettivo (°C)")}<input type="number" name="v" min="0" max="${max}" step="1" value="${Math.round(current || 0)}"></label>
    <p class="sub" style="margin:0">${t("0 spegne il riscaldatore. Massimo {m} °C.", {m: max})}</p>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button type="button" id="toff">${t("Spegni")}</button><button class="primary" id="tok">${t("Imposta")}</button></div>`);
  const send = async v => { if (await act(() => api(`/api/printers/${p.sn}/temperature`, {method: "POST", body: {target, value: v}}), t("Comando inviato"))) $("#dlg").close(); };
  $("#tok").onclick = ev => { ev.preventDefault(); const v = +$("#dlgbody").elements.namedItem("v").value; if (v >= 0 && v <= max) send(v); };
  $("#toff").onclick = () => send(0);
}
function speedDialog(p, current) {
  openDialog(`<h3>${esc(p.name)} – ${t("Velocità")}</h3>
    <label>${t("Fattore di velocità: {v}%", {v: `<b id="spv">${current}</b>`})}<input type="range" name="v" min="50" max="200" step="5" value="${current}"></label>
    <div class="row">${[50, 75, 100, 125, 150].map(v => `<button type="button" data-v="${v}">${v}%</button>`).join("")}</div>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button class="primary" id="sok">${t("Imposta")}</button></div>`);
  const r = $("#dlgbody").elements.namedItem("v");
  r.oninput = () => $("#spv").textContent = r.value;
  $$("#dlgbody [data-v]").forEach(b => b.onclick = () => { r.value = b.dataset.v; r.oninput(); });
  $("#sok").onclick = async ev => { ev.preventDefault();
    if (await act(() => api(`/api/printers/${p.sn}/speed`, {method: "POST", body: {pct: +r.value}}), t("Comando inviato"))) $("#dlg").close(); };
}
function createCard(p) {
  const a = document.createElement("article");
  a.className = "pc"; a.dataset.sn = p.sn;
  a.innerHTML = `<div class="pc-top"></div>
    <div class="sec">${t("Stato")}</div>
    <div class="statusbox">
      <button type="button" class="cam" data-camzoom title="${esc(t("Ingrandisci"))}"><img alt="${esc(t("Camera di {name}", {name: p.name}))}" hidden><span class="camph">${t("Caricamento immagine…")}</span></button>
      <div class="st-info"></div>
    </div>
    <div class="pc-body"></div>`;
  if (isAdmin()) {   // menu "altre azioni" in alto a destra della scheda
    a.classList.add("has-menu");
    a.insertAdjacentHTML("afterbegin", `<div class="pc-menu">
      <button type="button" class="dots" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(t("Altre azioni"))}" title="${esc(t("Altre azioni"))}">⋮</button>
      <div class="menu" role="menu" hidden><button type="button" role="menuitem" data-rename>${t("Rinomina")}</button>
        <button type="button" role="menuitem" data-raw>${t("Dati tecnici")}</button></div></div>`);
  }
  refreshCam(a);
  return a;
}
function setHTML(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }
// ---------------------------------------------------------------- vista delle stampanti
const CARDS = new Map();          // schede già create, riusate tra le viste (la camera non si ricarica)
const EXPANDED = new Set();       // in elenco/tabella: stampanti con la scheda aperta
let PVIEW = localStorage.getItem("fb_pview") || "cards", PSORT = localStorage.getItem("fb_psort") || "name", PSEARCH = "";
const brandOf = p => p.brand || t("Altra marca");
const modelOf = p => p.printer_model || p.model || "";
const STATE_ORDER = s => ({RUNNING: 0, PREPARE: 0, PAUSE: 1, FINISH: 2, FAILED: 2, IDLE: 3}[s] ?? 4);
function sortPrinters(list) {
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: "base"});
  const cmp = {name: byName,
    brand: (a, b) => brandOf(a).localeCompare(brandOf(b)) || modelOf(a).localeCompare(modelOf(b)) || byName(a, b),
    model: (a, b) => modelOf(a).localeCompare(modelOf(b), undefined, {numeric: true}) || byName(a, b),
    state: (a, b) => (a.online ? STATE_ORDER(a.state) : 5) - (b.online ? STATE_ORDER(b.state) : 5) || byName(a, b),
    progress: (a, b) => (b.active ? b.progress : -1) - (a.active ? a.progress : -1) || byName(a, b)}[PSORT] || byName;
  return [...list].sort(cmp);
}
function cardFor(p) {
  let card = CARDS.get(p.sn);
  if (!card) { card = createCard(p); CARDS.set(p.sn, card); }
  card.classList.toggle("offline", !p.online);
  setHTML($(".pc-top", card), cardTop(p));
  setHTML($(".st-info", card), statusInfo(p));
  setHTML($(".pc-body", card), cardBody(p));
  return card;
}
const stateDot = p => `<i class="sdot ${!p.online ? "off" : p.active ? (p.state === "PAUSE" ? "pause" : "run") : ["FINISH", "FAILED"].includes(p.state) ? "warn" : "idle"}"></i>`;
const tempsShort = p => [p.nozzles.length ? p.nozzles.map(n => n.fault ? "⚠" : n.temp != null ? Math.round(n.temp) + "°" : "—").join("/") : "",
  p.bed != null ? `${t("Piatto|letto")} ${Math.round(p.bed)}°` : ""].filter(Boolean).join(" · ");
const progressCell = p => p.active ? `<div class="mini"><div class="strand" style="${activeColor(p) ? "--fc:" + activeColor(p) : ""}"><i style="width:${p.progress}%"></i></div><b>${p.progress}%</b></div>` : "";

RENDER.printers = function renderPrinters() {
  const el = $("#v-printers");
  if (!el._built) {
    el.innerHTML = `<div class="head"><div><h1>${t("Stampanti")}</h1><p class="sub" id="psub"></p></div>
        <div class="ptools"><input type="search" id="psearch" placeholder="${esc(t("Cerca per nome, marca, modello…"))}" value="${esc(PSEARCH)}">
        <label class="inline">${t("Ordina per")} <select id="psort">${[["name", t("Nome")], ["brand", t("Marca")], ["model", t("Modello")], ["state", t("Stato")], ["progress", t("Avanzamento")]]
          .map(([k, l]) => `<option value="${k}" ${k === PSORT ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <div class="seg" id="pview" role="group">${[["cards", t("Schede")], ["list", t("Elenco")], ["table", t("Tabella")]]
          .map(([k, l]) => `<button type="button" data-v="${k}" class="${k === PVIEW ? "on" : ""}" aria-pressed="${k === PVIEW}">${l}</button>`).join("")}</div></div></div>
      <div class="chips" id="gchips"></div><div id="pwrap"></div><div id="pempty"></div>`;
    el._built = true;
    $("#gchips").onclick = e => { const c = e.target.closest("[data-g]"); if (!c) return; groupFilter = c.dataset.g; localStorage.setItem("fb_group", groupFilter); RENDER.printers(); };
    $("#psearch").oninput = e => { PSEARCH = e.target.value; RENDER.printers(); };
    $("#psort").onchange = e => { PSORT = e.target.value; localStorage.setItem("fb_psort", PSORT); RENDER.printers(); };
    $("#pview").onclick = e => { const b = e.target.closest("[data-v]"); if (!b) return; PVIEW = b.dataset.v; localStorage.setItem("fb_pview", PVIEW);
      $$("#pview button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); }); $("#pwrap").innerHTML = ""; RENDER.printers(); };
  }
  const grp = GROUPS.find(g => String(g.id) === groupFilter);
  const q = PSEARCH.trim().toLowerCase();
  let list = grp ? S.printers.filter(p => grp.printers.includes(p.sn)) : S.printers;
  if (q) list = list.filter(p => [p.name, p.brand, modelOf(p), p.task_name, p.sn, p.kind_label].some(v => (v || "").toLowerCase().includes(q)));
  list = sortPrinters(list);
  const ready = list.filter(p => p.online && p.state === "IDLE").length, printing = list.filter(p => p.active).length;
  setHTML($("#psub"), t("{a} in stampa, {b} pronte", {a: printing, b: ready}) + (S.queue_paused ? " · " + t("coda in pausa") : ""));
  setHTML($("#gchips"), GROUPS.length ? [`<button class="chip ${groupFilter ? "" : "on"}" data-g="">${t("Tutte")} <small>${S.printers.length}</small></button>`,
    ...GROUPS.map(g => `<button class="chip ${String(g.id) === groupFilter ? "on" : ""}" data-g="${g.id}" style="--c:${esc(g.color)}"><i></i>${esc(g.name)} <small>${g.printers.filter(sn => S.printers.some(p => p.sn === sn)).length}</small></button>`)].join("") : "");
  S.printers.forEach(p => [...(p.print_error ? [p.print_error] : []), ...p.hms].forEach(c => loadHms(p.model, c)));
  const wrap = $("#pwrap");
  const brandHead = (p, i) => PSORT === "brand" && (i === 0 || brandOf(list[i - 1]) !== brandOf(p));
  if (PVIEW === "cards") {
    let grid = $("#pgrid");
    if (!grid) { wrap.innerHTML = `<div class="grid" id="pgrid"></div>`; grid = $("#pgrid"); }
    const nodes = [];
    list.forEach((p, i) => {
      if (brandHead(p, i)) { const h = document.createElement("h2"); h.className = "bhead"; h.textContent = brandOf(p); nodes.push(h); }
      nodes.push(cardFor(p));
    });
    [...grid.children].forEach(c => { if (!nodes.includes(c)) c.remove(); });
    nodes.forEach((n, i) => { if (grid.children[i] !== n) grid.insertBefore(n, grid.children[i] || null); });
  } else {
    const rows = list.map((p, i) => {
      const head = brandHead(p, i) ? (PVIEW === "table" ? `<tr class="bhead"><td colspan="7">${esc(brandOf(p))}</td></tr>` : `<h2 class="bhead">${esc(brandOf(p))}</h2>`) : "";
      const open = EXPANDED.has(p.sn);
      if (PVIEW === "table") return head + `<tr data-prow="${esc(p.sn)}" class="${open ? "open" : ""}">
          <td>${stateDot(p)} <b>${esc(p.name)}</b></td><td>${esc(brandOf(p))}<br><small class="muted">${esc(modelOf(p))}</small></td>
          <td>${p.online ? stateName(p.state) : t("Offline")}</td><td>${progressCell(p)}</td>
          <td class="ell" title="${esc(p.task_name)}">${esc(p.task_name || "")}</td><td>${p.active && p.remaining_min ? fmtDur(p.remaining_min * 60) : ""}</td>
          <td>${tempsShort(p)}</td></tr>${open ? `<tr class="pexp"><td colspan="7"><div class="slot" data-sn="${esc(p.sn)}"></div></td></tr>` : ""}`;
      return head + `<div class="prow ${open ? "open" : ""}" data-prow="${esc(p.sn)}">${stateDot(p)}
          <div class="pr-name"><b>${esc(p.name)}</b><small class="muted">${esc(brandOf(p))} ${esc(modelOf(p))}</small></div>
          <div class="pr-state">${p.online ? stateName(p.state) : t("Offline")}${p.task_name ? `<small class="muted ell">${esc(p.task_name)}</small>` : ""}</div>
          <div class="pr-prog">${progressCell(p)}${p.active && p.remaining_min ? `<small class="muted">${t("mancano {d}", {d: fmtDur(p.remaining_min * 60)})}</small>` : ""}</div>
          <div class="pr-temp muted small">${tempsShort(p)}</div><span class="pr-arrow">${open ? "▴" : "▾"}</span></div>
        ${open ? `<div class="slot" data-sn="${esc(p.sn)}"></div>` : ""}`;
    }).join("");
    const html = PVIEW === "table" ? `<div class="tablewrap"><table class="ptable"><thead><tr><th>${t("Stampante")}</th><th>${t("Marca")} / ${t("Modello")}</th><th>${t("Stato")}</th><th>${t("Avanzamento")}</th><th>${t("Lavoro")}</th><th>${t("Tempo rimanente")}</th><th>${t("Temperature")}</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="plist">${rows}</div>`;
    setHTML(wrap, html);
    list.forEach(p => { if (EXPANDED.has(p.sn)) { const slot = wrap.querySelector(`.slot[data-sn="${CSS.escape(p.sn)}"]`); const card = cardFor(p); if (slot && card.parentNode !== slot) slot.appendChild(card); } });
  }
  setHTML($("#pempty"), list.length ? "" : `<div class="empty">${q ? t("Nessuna stampante corrisponde alla ricerca.") : !S.hub_ok && !S.printers.length ? t("In attesa del Hub…") : S.printers.length ? t("Nessuna stampante in questo gruppo.") : isAdmin() ? t("Nessuna stampante collegata al Hub. Aggiungile da Amministrazione → Fleet Hub.") : t("Nessuna stampante assegnata al tuo utente. Chiedi all'amministratore.")}</div>`);
};
async function loadHms(model, code) {
  const key = model + code;
  if (key in hmsDesc) return;
  hmsDesc[key] = "";
  try { hmsDesc[key] = (await api(`/api/hms?model=${encodeURIComponent(model)}&code=${code}&kind=${code.length === 8 ? "error" : "hms"}`)).description; } catch {}
  if (hmsDesc[key] && view === "printers") RENDER.printers();
}
// Scarica la nuova istantanea in memoria e la sostituisce solo quando è pronta: niente sfarfallio.
async function refreshCam(card) {
  const img = $(".cam img", card), ph = $(".camph", card);
  if (card._camBusy) return; card._camBusy = true;
  try {
    const r = await fetch(`/api/printers/${encodeURIComponent(card.dataset.sn)}/snapshot?t=${Date.now()}`, {credentials: "same-origin"});
    if (!r.ok) { if (img.hidden) ph.textContent = t("Nessuna immagine disponibile"); return; }
    const kind = r.headers.get("X-Image-Kind") || "camera";
    if (IMGKIND[card.dataset.sn] !== kind) { IMGKIND[card.dataset.sn] = kind; card.querySelector(".st-info")._html = null; }
    card.classList.toggle("thumbimg", kind === "thumbnail");
    const url = URL.createObjectURL(await r.blob());
    const pre = new Image();
    pre.onload = () => { const old = img._url; img.src = url; img._url = url; img.hidden = false; if (old) setTimeout(() => URL.revokeObjectURL(old), 1000); };
    pre.onerror = () => URL.revokeObjectURL(url);
    pre.src = url;
  } catch { if (img.hidden) ph.textContent = t("Nessuna immagine disponibile"); }
  finally { card._camBusy = false; }
}
let camTimer = null;
function startCamTimer() {
  clearInterval(camTimer);
  const sec = (S.settings && S.settings.camera_refresh_s) || 60;
  camTimer = setInterval(() => { if (view === "printers" && !document.hidden) $$("#v-printers .pc").forEach(refreshCam); }, sec * 1000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && view === "printers") $$("#v-printers .pc").forEach(refreshCam); });

function closeMenus() {
  $$(".pc-menu .dots[aria-expanded=true]").forEach(d => { d.setAttribute("aria-expanded", "false"); d.nextElementSibling.hidden = true; });
}
document.addEventListener("click", e => { if (!e.target.closest(".pc-menu")) closeMenus(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenus(); });
$("#v-printers").addEventListener("click", async e => {
  const row = e.target.closest("[data-prow]");
  if (row && !e.target.closest(".pc")) { const sn = row.dataset.prow; EXPANDED.has(sn) ? EXPANDED.delete(sn) : EXPANDED.add(sn); RENDER.printers(); return; }
  const card = e.target.closest(".pc"); if (!card) return;
  const sn = card.dataset.sn, p = S.printers.find(x => x.sn === sn);
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.opt) {
    const ask = {stop: t("Interrompere la stampa in corso?"), bed_clean: t("Confermi che il piatto è stato liberato? La stampante tornerà disponibile per la coda.")};
    if (ask[b.dataset.opt] && !confirm(ask[b.dataset.opt])) return;
    b.disabled = true;
    await act(() => api(`/api/printers/${sn}/opt`, {method: "POST", body: {opt: b.dataset.opt}}), t("Comando inviato (lo stato si aggiorna in ~10 s)"));
  } else if (b.hasAttribute("data-remote")) {
    remoteFilesDialog(p, "Local");
  } else if (b.dataset.temp) {
    tempDialog(p, b.dataset.temp, b.dataset.label, +b.dataset.target);
  } else if (b.dataset.speed) {
    speedDialog(p, +b.dataset.speed);
  } else if (b.hasAttribute("data-camzoom")) {
    const img = $(".cam img", card);
    if (!img.hidden) openDialog(`<img src="${img.src}" alt="" style="width:100%;border-radius:8px"><p class="sub" style="margin:0">${esc(snapAge(p))}</p><div class="dlgfoot"><button value="cancel">${t("Chiudi")}</button></div>`);
  } else if (b.classList.contains("dots")) {
    const open = b.getAttribute("aria-expanded") === "true";
    closeMenus();
    if (!open) { b.setAttribute("aria-expanded", "true"); b.nextElementSibling.hidden = false; b.nextElementSibling.querySelector("button").focus(); }
  } else if (b.hasAttribute("data-raw")) {
    closeMenus();
    const raw = await act(() => api(`/api/printers/${sn}/raw`));
    if (raw) openDialog(`<h3>${t("Dati tecnici")} – ${esc(p.name)}</h3>
      <p class="sub" style="margin:0">${t("Dati così come arrivano dal Hub: utili per verificare quali informazioni invia la stampante, ad esempio la capacità residua delle bobine (campo remain).")}</p>
      <pre class="raw">${esc(JSON.stringify(raw, null, 2))}</pre>
      <div class="dlgfoot"><button value="ok">${t("Chiudi")}</button></div>`);
  } else if (b.hasAttribute("data-rename")) {
    closeMenus();
    const n = prompt(t("Nuovo nome della stampante"), p.name);
    if (n) await act(() => api(`/api/printers/${sn}/name`, {method: "PUT", body: {name: n}}), t("Nome aggiornato"));
  } else if (b.classList.contains("sp")) {
    if (canOperate()) editTray(p, +b.dataset.ams, +b.dataset.slot);
  }
});

async function editTray(p, ams, slot) {
  const tray = p.trays.find(x => x.ams_id === ams && x.slot_id === slot);
  const label = trayLabel(tray.label);
  if (tray.rfid) return toast(t("{slot}: bobina Bambu con RFID, i dati vengono letti dal tag e non sono modificabili", {slot: label}), "info");
  if (!presets.length) presets = await api("/api/filament_presets");
  const cur = presets.find(x => x.idx === tray.idx) || presets.find(x => x.type === tray.type) || presets[0];
  openDialog(`<h3>${esc(p.name)} – ${t("slot {s}", {s: esc(label)})}</h3>
    <div class="row"><label>${t("Materiale")}<select name="preset">${presets.map(x => `<option value="${x.idx}" ${x === cur ? "selected" : ""}>${x.type}</option>`).join("")}</select></label>
    <label>${t("Colore")}<input type="color" name="color" value="${tray.color || "#FFFFFF"}"></label>
    <label>${t("Temp. min")}<input type="number" name="min" value="${cur.min}" style="width:90px"></label>
    <label>${t("Temp. max")}<input type="number" name="max" value="${cur.max}" style="width:90px"></label></div>
    <p class="sub" style="margin:0">${t("Verifica la compatibilità del materiale con l'ugello montato ({n}).", {n: esc(`${p.nozzle_diameter} ${p.nozzle_type}`.trim())})}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="trayok">${t("Salva slot")}</button></div>`);
  const f = $("#dlgbody");
  f.preset.onchange = () => { const x = presets.find(y => y.idx === f.preset.value); f.min.value = x.min; f.max.value = x.max; };
  $("#trayok").onclick = async ev => {
    ev.preventDefault();
    const x = presets.find(y => y.idx === f.preset.value);
    const r = await act(() => api(`/api/printers/${p.sn}/filament`, {method: "POST", body: {ams_id: ams, slot_id: slot,
      tray_type: x.type, tray_info_idx: x.idx, tray_color: f.color.value, nozzle_temp_min: +f.min.value, nozzle_temp_max: +f.max.value}}), t("Slot aggiornato"));
    if (r) $("#dlg").close();
  };
}

// ------------------------------------------------------------------ archivio e progetti
let projFilter = localStorage.getItem("fb_proj") || "";   // "" = tutti, "none" = senza progetto, oppure id
RENDER.archive = async function renderArchive() {
  const el = $("#v-archive");
  await loadMeta();
  if (projFilter && projFilter !== "none" && !PROJECTS.some(p => String(p.id) === projFilter)) projFilter = "";
  const sel = projFilter === "none" ? PROJECTS.find(p => p.id === null) : PROJECTS.find(p => String(p.id) === projFilter);
  const uploadPid = sel ? sel.id : (!isAdmin() && PROJECTS.length === 1 ? PROJECTS[0].id : (isAdmin() ? null : undefined));
  const dropText = uploadPid === undefined ? t("Seleziona un progetto per caricare file")
    : (sel ? t("Trascina qui un file .gcode.3mf (Bambu) o .gcode (altre stampanti) nel progetto {p} oppure sceglilo dal computer", {p: `<b>${esc(projName(sel))}</b>`})
      : uploadPid === null ? t("Trascina qui un file .gcode.3mf (Bambu) o .gcode (altre stampanti), sarà senza progetto, oppure sceglilo dal computer")
      : t("Trascina qui un file .gcode.3mf (Bambu) o .gcode (altre stampanti) nel progetto {p} oppure sceglilo dal computer", {p: `<b>${esc(PROJECTS[0].name)}</b>`}));
  el.innerHTML = `<div class="head"><div><h1>${t("Archivio")}</h1><p class="sub">${t("I file sono organizzati in progetti. Ogni utente vede solo i progetti che l'amministratore gli ha assegnato.")}</p></div>
      <button class="admin-only primary" id="newproj">${t("Nuovo progetto")}</button></div>
    <div class="chips" id="pchips">
      <button class="chip ${projFilter ? "" : "on"}" data-p="">${t("Tutti")} <small>${PROJECTS.reduce((n, p) => n + (p.files || 0), 0)}</small></button>
      ${PROJECTS.map(p => { const key = p.id === null ? "none" : String(p.id);
        return `<button class="chip ${projFilter === key ? "on" : ""}" data-p="${key}" style="--c:${esc(p.color)}"><i></i>${esc(projName(p))} <small>${p.files}</small></button>`; }).join("")}
    </div>
    ${sel ? `<div class="projhead"><div><h2 style="margin:0">${esc(projName(sel))}</h2>${sel.id === null ? `<p>${t("Visibili solo agli amministratori")}</p>` : sel.description ? `<p>${esc(sel.description)}</p>` : ""}</div>
      ${sel.id !== null ? `<div class="row admin-only"><button id="editproj">${t("Modifica")}</button><button class="danger" id="delproj">${t("Elimina progetto")}</button></div>` : ""}</div>` : ""}
    ${canOperate() ? `<div class="drop" id="drop">${dropText}<input type="file" id="fpick" accept=".3mf,.gcode,.gco,.g" multiple hidden></div>` : ""}
    <div class="list" id="flist"><div class="empty">${t("Caricamento…")}</div></div>`;
  $("#pchips").onclick = e => { const c = e.target.closest("[data-p]"); if (!c) return; projFilter = c.dataset.p; localStorage.setItem("fb_proj", projFilter); RENDER.archive(); };
  if ($("#newproj")) $("#newproj").onclick = () => projectDialog();
  if ($("#editproj")) $("#editproj").onclick = () => projectDialog(sel);
  if ($("#delproj")) $("#delproj").onclick = async () => {
    if (!confirm(t("Eliminare il progetto \"{p}\"? I suoi {n} file non vengono cancellati: diventano \"senza progetto\".", {p: sel.name, n: sel.files}))) return;
    await act(() => api(`/api/projects/${sel.id}`, {method: "DELETE"}), t("Progetto eliminato")); projFilter = ""; RENDER.archive();
  };
  const drop = $("#drop");
  if (drop && uploadPid !== undefined) {
    const pick = $("#fpick");
    drop.onclick = () => pick.click();
    pick.onchange = () => uploadFiles(pick.files, uploadPid);
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); uploadFiles(e.dataTransfer.files, uploadPid); };
  }
  const files = await api(`/api/files${projFilter ? "?project=" + projFilter : ""}`).catch(e => (toast(e.message, "error"), []));
  $("#flist").innerHTML = files.length ? files.map(f => {
    const pl = f.meta.plates || [], p0 = pl[0] || {}, pr = projectOf(f.project_id);
    const canDel = isAdmin() || (canOperate() && f.uploaded_by === ME.id);
    return `<div class="item" data-id="${f.id}">
      <img class="thumb" loading="lazy" alt="" src="/api/files/${f.id}/thumb/${p0.index || 1}">
      <div><div class="t" title="${esc(f.filename)}">${esc(f.filename)}</div>
        <div class="m"><span class="tag" style="--c:${esc(pr.color)}"><i></i>${esc(pr.name)}</span> · <span class="bdg">${f.meta.kind === "gcode" ? "G-code" : "Bambu 3MF"}</span> · ${pl.length > 1 ? t("{n} piatti", {n: pl.length}) + " · " : ""}${fmtDur(p0.prediction_s)} · ${p0.weight_g || 0} g ${dots(p0.filaments)}</div>
        <div class="m">${t("Caricato il {d}", {d: fmtDate(f.uploaded_at)})} · ${t("stampato {n} volte", {n: f.prints})}${f.prints ? " " + t("({n} riuscite)", {n: f.ok}) : ""}</div></div>
      <div class="a">${canOperate() ? `<button class="primary" data-print>${t("Stampa")}</button><button data-move>${t("Sposta")}</button>` : ""}
        <a class="btn" href="/api/files/${f.id}/download">${t("Scarica")}</a>${canDel ? `<button class="danger" data-del>${t("Elimina")}</button>` : ""}</div></div>`;
  }).join("") : `<div class="empty">${PROJECTS.length || isAdmin() ? t("Nessun file qui. Esporta dallo slicer con \"Esporta G-code del piatto\" e trascinalo nell'area qui sopra.") : t("Nessun progetto assegnato al tuo utente. Chiedi all'amministratore.")}</div>`;
  $("#flist").onclick = async e => {
    const it = e.target.closest(".item"); if (!it) return;
    const f = files.find(x => x.id === +it.dataset.id);
    if (e.target.closest("[data-print]")) printDialog(f);
    if (e.target.closest("[data-move]")) moveDialog(f);
    if (e.target.closest("[data-del]") && confirm(t("Eliminare {f} dall'archivio?", {f: f.filename}))) { await act(() => api(`/api/files/${f.id}`, {method: "DELETE"})); RENDER.archive(); }
  };
};
function projectDialog(p) {
  openDialog(`<h3>${p ? t("Modifica progetto") : t("Nuovo progetto")}</h3>
    <div class="row"><label style="flex:1">${t("Nome")}<input name="pname" value="${esc(p ? p.name : "")}" maxlength="60"></label>
      <label>${t("Colore")}<input type="color" name="color" value="${esc(p ? p.color : "#2B63C6")}"></label></div>
    <label>${t("Descrizione")}<textarea name="description" rows="3" maxlength="500">${esc(p ? p.description : "")}</textarea></label>
    <p class="sub" style="margin:0">${t("Dopo averlo creato, assegna il progetto agli utenti da Amministrazione → Utenti → Permessi.")}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="projok">${p ? t("Salva") : t("Crea progetto")}</button></div>`);
  $("#projok").onclick = async ev => {
    ev.preventDefault(); const f = $("#dlgbody");
    const body = {name: f.pname.value, color: f.color.value, description: f.description.value};
    const r = await act(() => p ? api(`/api/projects/${p.id}`, {method: "PATCH", body}) : api("/api/projects", {method: "POST", body}), p ? t("Progetto aggiornato") : t("Progetto creato"));
    if (r) { $("#dlg").close(); if (!p && r.id) { projFilter = String(r.id); localStorage.setItem("fb_proj", projFilter); } RENDER.archive(); }
  };
}
function moveDialog(f) {
  const opts = PROJECTS.filter(p => p.id !== null || isAdmin());
  openDialog(`<h3>${t("Sposta {f}", {f: esc(f.filename)})}</h3>
    <label>${t("Progetto di destinazione")}<select name="pid">${opts.map(p => `<option value="${p.id === null ? "none" : p.id}" ${p.id === f.project_id ? "selected" : ""}>${esc(projName(p))}</option>`).join("")}</select></label>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="mvok">${t("Sposta")}</button></div>`);
  $("#mvok").onclick = async ev => { ev.preventDefault();
    if (await act(() => api(`/api/files/${f.id}`, {method: "PATCH", body: {project_id: $("#dlgbody").pid.value}}), t("File spostato"))) { $("#dlg").close(); RENDER.archive(); } };
}
async function uploadFiles(list, pid) {
  for (const file of list) {
    toast(t("Caricamento di {f}…", {f: file.name}));
    const fd = new FormData(); fd.append("file", file); fd.append("project_id", pid === null || pid === undefined ? "" : String(pid));
    const r = await act(() => api("/api/files", {method: "POST", body: fd}));
    if (r) toast(r.duplicate ? t("{f} era già in archivio (progetto: {p})", {f: file.name, p: projectOf(r.project_id).name})
      : r.renamed ? t("Esiste già un file {f}: il nuovo è stato aggiunto come {n}", {f: file.name, n: r.filename})
      : t("{f} aggiunto all'archivio", {f: file.name}), "ok");
  }
  RENDER.archive();
}

async function printDialog(f, presetPrinter) {
  const plates = f.meta.plates;
  const isG = f.meta.kind === "gcode";   // .gcode → stampanti dirette (Duet); .gcode.3mf → Bambu
  const online = S.printers.filter(p => p.online && !!p.direct === isG && (p.caps || {}).upload !== false);
  const myGroups = GROUPS.filter(g => isAdmin() || (g.printers.length && g.printers.every(sn => S.printers.some(p => p.sn === sn))));
  openDialog(`<h3>${t("Stampa {f}", {f: esc(f.filename)})}</h3>
    <div class="printhead"><img class="platethumb" id="platethumb" alt="${esc(t("Anteprima del piatto"))}" src="/api/files/${f.id}/thumb/${plates[0].index}">
    <div class="printinfo"><div class="row">
      ${plates.length > 1 ? `<label>${t("Piatto")}<select name="plate">${plates.map(p => `<option value="${p.index}">${t("Piatto {n}", {n: p.index})} · ${fmtDur(p.prediction_s)}</option>`).join("")}</select></label>` : `<input type="hidden" name="plate" value="${plates[0].index}">`}
      <label>${t("Destinazione")}<select name="dest">
        ${isAdmin() ? `<option value="">${t("Qualsiasi stampante libera (solo coda)")}</option>` : ""}
        ${myGroups.length ? `<optgroup label="${esc(t("Gruppi (solo coda)"))}">${myGroups.map(g => `<option value="g:${g.id}">${t("Gruppo {g}", {g: esc(g.name)})}</option>`).join("")}</optgroup>` : ""}
        <optgroup label="${esc(t("Stampanti"))}">${online.map(p => `<option value="p:${p.sn}" ${p.sn === presetPrinter ? "selected" : ""}>${esc(p.name)} – ${stateName(p.state)}</option>`).join("")}</optgroup>
      </select></label>
    </div><div class="muted small" id="plateinfo"></div></div></div>
    <div id="mapping"></div>
    <div class="warn" id="busynote"></div>
    <div class="row">
      <span id="popts" class="row"></span>
    </div>
    <div class="dlgfoot">
      <label class="inline">${t("Copie")} <input type="number" name="copies" value="1" min="1" max="99" style="width:70px"></label>
      ${btnCancel()}<button id="toqueue">${t("Aggiungi in coda")}</button><button class="primary" id="printnow">${t("Stampa ora")}</button>
    </div>`);
  const fm = $("#dlgbody");
  let slots = null;
  const plateOf = () => plates.find(p => p.index === +fm.plate.value);
  const target = () => { const v = fm.dest.value; return {sn: v.startsWith("p:") ? v.slice(2) : null, gid: v.startsWith("g:") ? +v.slice(2) : null}; };
  async function remap() {
    const {sn, gid} = target(), pl = plateOf();
    const pr0 = S.printers.find(x => x.sn === sn);
    const ready = !!pr0 && pr0.online && pr0.mqtt === 2 && pr0.state === "IDLE" && !pr0.pending;
    $("#printnow").disabled = !sn || !ready;
    $("#toqueue").classList.toggle("primary", !!sn && !ready); $("#printnow").classList.toggle("primary", !sn || ready);
    $("#busynote").innerHTML = !sn || ready ? "" : !pr0.online ? t("{p} è offline: puoi solo aggiungere la stampa alla sua coda.", {p: esc(pr0.name)})
      : pr0.state === "FINISH" || pr0.state === "FAILED" ? t("{p} ha il piatto da liberare: la stampa partirà dalla coda dopo la conferma.", {p: esc(pr0.name)})
      : pr0.pending ? t("{p} sta già ricevendo una stampa: puoi solo aggiungerla alla sua coda.", {p: esc(pr0.name)})
      : t("{p} è occupata ({s}): puoi solo aggiungere la stampa alla sua coda.", {p: esc(pr0.name), s: stateName(pr0.state)});
    if (!sn) { slots = null; $("#mapping").innerHTML = `<p class="sub" style="margin:0">${t("Filamenti richiesti: {list}.", {list: pl.filaments.map(x => esc(x.type)).join(", ")})} ${dots(pl.filaments)} ${gid ? t("La coda sceglierà la prima stampante libera del gruppo che li ha caricati.") : t("La coda sceglierà la prima stampante libera che li ha caricati.")}</p>`; return; }
    if (isG) {   // G-code già preparato per la stampante: nessuno slot da scegliere
      slots = null;
      $("#mapping").innerHTML = `<p class="sub" style="margin:0">${t("Filamenti richiesti: {list}.", {list: pl.filaments.map(x => esc(x.type)).join(", ")})} ${dots(pl.filaments)}</p>`;
      return;
    }
    const r = await act(() => api(`/api/files/${f.id}/automap`, {method: "POST", body: {printer_sn: sn, plate: pl.index}}));
    if (!r) return;
    // filamento non caricato: assegnazione automatica, la coda la farà quando sarà caricato
    const missing = !r.slots;
    slots = r.slots ? [...r.slots] : Array.from({length: Math.max(1, ...pl.filaments.map(x => x.id))}, () => ({auto: true}));
    if (missing) {
      $("#printnow").disabled = true; $("#toqueue").classList.add("primary"); $("#printnow").classList.remove("primary");
      $("#busynote").innerHTML = t("Uno o più filamenti non sono caricati su {p}: aggiungi la stampa in coda con l'assegnazione automatica, partirà quando li avrai caricati.", {p: esc(pr0.name)});
    }
    const loaded = r.trays.filter(x => x.loaded);
    const pr = S.printers.find(x => x.sn === sn) || {nozzles: [], ams_units: []};
    const dual = (pr.nozzles || []).length > 1;
    const sideOfTray = x => {   // L/R dell'ugello a cui è collegata la bobina (doppio estrusore)
      if (!dual) return "";
      const nid = x.kind === "external" ? x.nozzle : ((pr.ams_units || []).find(u => u.ams_id === x.ams_id) || {}).nozzle;
      const n = (pr.nozzles || []).find(z => z.id === nid);
      return n ? sideLetter(n.side) : "";
    };
    const optHtml = x => `<span class="sw" style="--c:${esc(x.color || "#888")}"></span><b>${esc(trayLabel(x.label))}</b> ${esc(x.type)}${x.sub_brand ? ` <span class="muted">${esc(x.sub_brand)}</span>` : ""}${sideOfTray(x) ? ` <span class="side">${sideOfTray(x)}</span>` : ""}`;
    const findTray = c => loaded.find(x => x.ams_id === c.ams_id && x.slot_id === c.slot_id);
    const autoHtml = `<span class="sw auto"></span><b>${t("Automatica")}</b> <span class="muted">${t("assegnata all'avvio")}</span>`;
    const curHtml = c => c && c.auto ? autoHtml : (findTray(c || {}) ? optHtml(findTray(c)) : `<span class="muted">${t("— scegli uno slot —")}</span>`);
    $("#mapping").innerHTML = `<div class="maprow">${r.filaments.map(fl => {
        return `<div class="fl"><i style="--c:${esc(fl.color)}"></i>${fl.id}. ${esc(fl.type)} · ${fl.used_g} g${fl.extruder ? ` <span class="side">${sideLetter(fl.extruder === 1 ? "left" : "right")}</span>` : ""}</div>
        <div class="slotpick" data-fid="${fl.id}"><button type="button" class="spk-cur">${curHtml(slots[fl.id - 1])}<span class="spk-arrow">▾</span></button>
          <div class="spk-list" role="listbox" hidden><button type="button" role="option" data-v="auto">${autoHtml}</button>${loaded.map(x => `<button type="button" role="option" data-v="${x.ams_id}:${x.slot_id}">${optHtml(x)}</button>`).join("")}</div></div>`;
      }).join("")}</div>${r.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join("")}`;
    $$("#mapping .slotpick").forEach(box => {
      const list = $(".spk-list", box);
      $(".spk-cur", box).onclick = () => { const open = list.hidden; $$("#mapping .spk-list").forEach(l => l.hidden = true); list.hidden = !open; };
      list.onclick = e => {
        const o = e.target.closest("[data-v]"); if (!o) return;
        while (slots.length < +box.dataset.fid) slots.push({ams_id: 255, slot_id: 255});
        if (o.dataset.v === "auto") slots[+box.dataset.fid - 1] = {auto: true};
        else { const [a, b] = o.dataset.v.split(":").map(Number); slots[+box.dataset.fid - 1] = {ams_id: a, slot_id: b}; }
        $(".spk-cur", box).innerHTML = curHtml(slots[+box.dataset.fid - 1]) + `<span class="spk-arrow">▾</span>`;
        list.hidden = true;
      };
    });
  }
  const opts = () => Object.fromEntries($$("#popts input").map(i => [i.name, i.checked]));
  // opzioni proposte solo se la stampante (o il tipo di file, per gruppi e "qualsiasi") le supporta
  const OPT_LABELS = {bed_leveling: [t("Livellamento piatto"), true], flow_calibration: [t("Calibrazione flusso"), true], timelapse: [t("Timelapse"), false]};
  const showOptions = () => {
    const sn = target().sn, pr = sn ? S.printers.find(x => x.sn === sn) : null;
    const list = pr ? ((pr.caps || {}).print_options || []) : (isG ? [] : ["bed_leveling", "flow_calibration", "timelapse"]);
    const prev = opts();
    $("#popts").innerHTML = list.length ? list.map(k => `<label class="inline"><input type="checkbox" name="${k}" ${(k in prev ? prev[k] : OPT_LABELS[k][1]) ? "checked" : ""}> ${OPT_LABELS[k][0]}</label>`).join("")
      : `<span class="muted small">${t("Nessuna opzione di stampa per questa stampante: valgono le impostazioni del file.")}</span>`;
  };
  const showPlate = () => {
    const pl = plateOf();
    $("#platethumb").src = `/api/files/${f.id}/thumb/${pl.index}`;
    $("#plateinfo").innerHTML = `${fmtDur(pl.prediction_s)} · ${pl.weight_g || 0} g ${dots(pl.filaments)}`;
  };
  showPlate();
  fm.dest.onchange = () => { showOptions(); remap(); };
  showOptions();
  if (fm.plate.tagName === "SELECT") fm.plate.onchange = () => { showPlate(); remap(); };
  remap();
  const valid = () => !slots || plateOf().filaments.every(fl => slots[fl.id - 1] && (slots[fl.id - 1].auto || slots[fl.id - 1].slot_id !== 255)) || (toast(t("Assegna uno slot a ogni filamento"), "error"), false);
  $("#printnow").onclick = async ev => {
    ev.preventDefault(); if (!valid()) return;
    ev.target.disabled = true; ev.target.textContent = t("Invio al Hub…");
    const r = await act(() => api("/api/print", {method: "POST", body: {file_id: f.id, plate: +fm.plate.value, printer_sn: target().sn, slots, options: opts()}}));
    if (r) { toast(r.cached ? t("Stampa avviata (file già nella cache del Hub)") : t("File caricato sul Hub, stampa avviata"), "ok"); $("#dlg").close(); }
    else { ev.target.disabled = false; ev.target.textContent = t("Stampa ora"); }
  };
  $("#toqueue").onclick = async ev => {
    ev.preventDefault(); if (!valid()) return;
    const {sn, gid} = target();
    const r = await act(() => api("/api/queue", {method: "POST", body: {file_id: f.id, plate: +fm.plate.value, printer_sn: sn, group_id: gid,
      slots: sn ? slots : null, options: opts(), copies: +fm.copies.value}}), t("Aggiunto in coda"));
    if (r) $("#dlg").close();
  };
}

// ------------------------------------------------------------------ coda
// Filamenti richiesti da una stampa in coda, divisi per ugello (SX/DX); quelli non caricati sulla
// stampante di destinazione sono evidenziati
function needHtml(q) {
  const fils = q.filaments || [];
  if (!fils.length) return "";
  const pr = q.printer_sn ? S.printers.find(x => x.sn === q.printer_sn) : null;
  const loaded = pr ? new Set(pr.trays.filter(x => x.loaded).map(x => x.type.toUpperCase())) : null;
  const groups = [[1, sideLetter("left")], [2, sideLetter("right")], [null, ""]]
    .map(([ex, label]) => [label, fils.filter(f => (f.extruder || null) === ex)]).filter(([, l]) => l.length);
  const one = f => { const miss = loaded && !loaded.has(f.type.toUpperCase());
    return `<span class="nf ${miss ? "miss" : ""}" title="${esc(f.type)} ${esc(f.color)} · ${f.used_g} g${miss ? " – " + esc(t("non caricato")) : ""}"><i style="--c:${esc(f.color)}"></i>${esc(f.type)}</span>`; };
  return `<div class="needs">${groups.map(([label, l]) => `<span class="need">${label ? `<b class="side">${label}</b>` : ""}${l.map(one).join("")}</span>`).join("")}</div>`;
}
RENDER.queue = async function renderQueue() {
  const el = $("#v-queue");
  const items = await api("/api/queue").catch(e => (toast(e.message, "error"), []));
  const dest = q => q.printer_sn ? esc(printerName(q.printer_sn)) : q.group_name ? t("gruppo {g}", {g: esc(q.group_name)}) : t("qualsiasi stampante");
  el.innerHTML = `<div class="head"><div><h1>${t("Coda di stampa")}</h1><p class="sub">${t("Le stampe partono da sole quando la stampante è inattiva ed escono dalla coda appena iniziano (restano nello storico). Dopo ogni stampa conferma \"Segna il piatto come liberato\" sulla scheda della stampante.")}</p></div>
    <button id="qpause" class="admin-only ${S.queue_paused ? "primary" : ""}">${S.queue_paused ? t("Riprendi la coda") : t("Metti in pausa la coda")}</button></div>
    <div class="list">${items.length ? items.map(q => `<div class="item" data-id="${q.id}">
      <img class="thumb" loading="lazy" alt="" src="/api/files/${q.file_id}/thumb/${q.plate}">
      <div><div class="t">${esc(q.filename || t("File rimosso"))}${q.copies > 1 ? ` × ${q.copies}` : ""}</div>
        <div class="m"><span class="st ${q.status}">${jobName(q.status)}</span> · ${dest(q)} · ${t("piatto {n}", {n: q.plate})} · ${fmtDur(q.prediction_s)} · ${q.weight_g} g</div>
        ${needHtml(q)}
        <div class="m">${q.created_by_name ? t("Aggiunta da {u}", {u: esc(q.created_by_name)}) : ""}${q.message ? ` · ${esc(qMsg(q.message))}` : ""}</div></div>
      <div class="a operator-only">${q.status === "queued" ? `<button data-mv="up" aria-label="${esc(t("Sposta su"))}">↑</button><button data-mv="down" aria-label="${esc(t("Sposta giù"))}">↓</button>` : ""}
        ${q.status === "error" ? `<button data-retry>${t("Riprova")}</button>` : ""}<button class="danger" data-del>${t("Rimuovi")}</button></div></div>`).join("")
      : `<div class="empty">${t("La coda è vuota. Dall'Archivio scegli \"Stampa\" e poi \"Aggiungi in coda\".")}</div>`}</div>`;
  $("#qpause").onclick = async () => { await act(() => api("/api/queue/pause", {method: "POST", body: {paused: !S.queue_paused}})); S.queue_paused = !S.queue_paused; RENDER.queue(); };
  el.querySelector(".list").onclick = async e => {
    const it = e.target.closest(".item"); if (!it) return; const id = it.dataset.id; const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.mv) await act(() => api(`/api/queue/${id}/move`, {method: "POST", body: {dir: b.dataset.mv}}));
    if (b.hasAttribute("data-retry")) await act(() => api(`/api/queue/${id}/retry`, {method: "POST"}));
    if (b.hasAttribute("data-del")) await act(() => api(`/api/queue/${id}`, {method: "DELETE"}));
    RENDER.queue();
  };
};

// ------------------------------------------------------------------ storico
RENDER.history = async function renderHistory() {
  const el = $("#v-history");
  const jobs = await api("/api/jobs?limit=300").catch(e => (toast(e.message, "error"), []));
  el.innerHTML = `<div class="head"><div><h1>${t("Storico")}</h1><p class="sub">${t("Ogni stampa vista dal Hub, anche quelle avviate da Bambu Studio o dalla stampante.")}</p></div></div>` +
    (jobs.length ? `<div class="tablewrap"><table><thead><tr><th></th><th>${t("Avvio")}</th><th>${t("Stampante")}</th><th>${t("Lavoro")}</th><th>${t("Utente")}</th><th>${t("Durata")}</th><th>${t("Filamento")}</th><th>${t("Esito")}</th><th></th></tr></thead><tbody>
    ${jobs.map(j => `<tr data-id="${j.id}" data-file="${j.file_id || ""}">
      <td>${j.snapshot ? `<img class="snap" loading="lazy" alt="${esc(t("Foto a fine stampa"))}" src="/api/jobs/${j.id}/snapshot">` : ""}</td>
      <td>${fmtDate(j.started_at)}</td><td>${esc(j.printer_name)}</td><td>${esc(j.task_name)}</td><td>${esc(j.started_by_name || "—")}</td>
      <td>${fmtDur((j.ended_at || Date.now() / 1000) - j.started_at)}</td><td>${filCell(j)}</td>
      <td><span class="st ${j.status}">${jobName(j.status)}</span>${j.status !== "finished" && j.status !== "running" ? ` · ${j.progress}%` : ""}${j.error ? `<br><small>${esc(j.error)}</small>` : ""}</td>
      <td style="white-space:nowrap">${j.file_id && canOperate() ? `<button data-reprint>${t("Ristampa")}</button>` : ""} ${isAdmin() ? `<button class="danger" data-del aria-label="${esc(t("Elimina"))}">✕</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">${t("Ancora nessuna stampa registrata.")}</div>`);
  el.onclick = async e => {
    const tr = e.target.closest("tr"); if (!tr) return;
    if (e.target.classList.contains("snap")) { openDialog(`<img src="${e.target.src}" alt="" style="width:100%;border-radius:8px"><div class="dlgfoot"><button value="ok">${t("Chiudi")}</button></div>`); return; }
    if (e.target.closest("[data-del]") && confirm(t("Eliminare questa voce dallo storico?"))) { await act(() => api(`/api/jobs/${tr.dataset.id}`, {method: "DELETE"})); RENDER.history(); }
    if (e.target.closest("[data-reprint]")) { const f = (await api("/api/files")).find(x => x.id === +tr.dataset.file); f ? printDialog(f) : toast(t("Il file non è più in archivio o non hai accesso al suo progetto"), "error"); }
  };
};

// ------------------------------------------------------------------ statistiche
// Utilizzo delle stampanti: situazione attuale e andamento nel tempo (grafico SVG, nessuna libreria esterna)
function utilization(ut, grp) {
  const g = GROUPS.find(x => String(x.id) === grp);
  const list = g ? S.printers.filter(p => g.printers.includes(p.sn)) : S.printers;
  const count = st => list.filter(p => p.online && st.includes(p.state)).length;
  const printing = count(["RUNNING", "PREPARE", "SLICING"]), paused = count(["PAUSE", "PAUSED"]);
  const finished = count(["FINISH", "FAILED"]), idle = count(["IDLE"]), offline = list.filter(p => !p.online).length;
  const live = list.length ? Math.round(100 * (printing + paused) / list.length) : 0;
  const kpis = `<div class="kpis">
    <div class="kpi big"><b>${live}%</b><span>${t("Utilizzo in tempo reale")}</span></div>
    <div class="kpi"><b>${printing}</b><span>${t("In stampa")}</span></div>
    <div class="kpi"><b>${paused}</b><span>${t("In pausa")}</span></div>
    <div class="kpi"><b>${finished}</b><span>${t("Stampa terminata")}</span></div>
    <div class="kpi"><b>${idle}</b><span>${t("Inattive")}</span></div>
    ${offline ? `<div class="kpi"><b>${offline}</b><span>${t("Offline")}</span></div>` : ""}
    <div class="kpi"><b>${list.length}</b><span>${t("Stampanti totali")}</span></div></div>`;
  if (!ut) return kpis;
  const W = 900, H = 260, L = 44, R = 12, T = 14, B = 30, pts = ut.buckets;
  const maxPct = Math.max(20, Math.ceil(Math.max(...pts.map(b => b.pct || 0)) / 10) * 10);
  const x = i => L + (W - L - R) * (pts.length > 1 ? i / (pts.length - 1) : 0);
  const y = v => T + (H - T - B) * (1 - v / maxPct);
  const valid = pts.map((b, i) => [i, b.pct]).filter(([, v]) => v != null);
  const line = valid.map(([i, v], k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const area = valid.length ? `${line}L${x(valid[valid.length - 1][0]).toFixed(1)},${y(0)}L${x(valid[0][0]).toFixed(1)},${y(0)}Z` : "";
  const fmt = ts => axisDate(ts, ut.step);
  const every = Math.max(1, Math.ceil(pts.length / 8));
  const grid = [0, .25, .5, .75, 1].map(f => { const v = Math.round(maxPct * f), yy = y(v).toFixed(1);
    return `<line class="gridl" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text class="axis" x="${L - 6}" y="${(+yy + 4).toFixed(1)}" text-anchor="end">${v}%</text>`; }).join("");
  const xl = pts.map((b, i) => i % every === 0 || i === pts.length - 1 ? `<text class="axis" x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${fmt(b.t)}</text>` : "").join("");
  const dotsSvg = valid.map(([i, v]) => `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="9" fill="transparent"><title>${fmt(pts[i].t)}: ${v}%</title></circle>`).join("");
  return `${kpis}
    <div class="util"><div class="head" style="margin-bottom:6px"><div><h2 style="margin:0">${t("Tasso di utilizzo")}</h2>
      <p class="sub" style="margin:0">${t("Media del periodo: {p}% · {n} stampanti", {p: ut.average, n: ut.printers})}</p></div>
      </div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("Tasso di utilizzo"))}">${grid}<path class="area" d="${area}"/><path class="line" d="${line}"/>${dotsSvg}${xl}</svg>
      ${ut.per_printer.length ? `<div style="margin-top:12px">${ut.per_printer.map(p => `<div class="ubar"><span>${esc(p.name)}</span><span class="track"><i style="width:${Math.min(100, p.pct)}%"></i></span><b>${p.pct}%</b></div>`).join("")}</div>` : ""}
      <p class="sub" style="margin:8px 0 0">${t("Percentuale del tempo in cui le stampanti hanno stampato (pause comprese), calcolata dallo storico.")}</p></div>`;
}
function axisDate(ts, step) {
  return new Date(ts * 1000).toLocaleString(LOCALES[LANG], step >= 86400 ? {day: "2-digit", month: "2-digit"}
    : step >= 4 * 3600 ? {day: "2-digit", month: "2-digit", hour: "2-digit"} : {hour: "2-digit", minute: "2-digit"});
}
// Stampe nel tempo: barre sottili impilate (riuscite, non riuscite, in corso) con asse temporale
function printsChart(series) {
  const pts = (series && series.buckets) || [];
  if (!pts.some(b => b.ok + b.ko + b.run)) return `<p class="sub">${t("Nessun dato nel periodo")}</p>`;
  const W = 900, H = 200, L = 34, R = 12, T = 10, B = 28;
  const max = Math.max(1, ...pts.map(b => b.ok + b.ko + b.run));
  const top = max <= 4 ? max : Math.ceil(max / 4) * 4;
  const slot = (W - L - R) / pts.length, bw = Math.max(2, Math.min(12, slot * 0.55));
  const y = v => T + (H - T - B) * (1 - v / top);
  const every = Math.max(1, Math.ceil(pts.length / 8));
  const grid = [0, .5, 1].map(f => { const v = Math.round(top * f), yy = y(v).toFixed(1);
    return `<line class="gridl" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text class="axis" x="${L - 6}" y="${(+yy + 4).toFixed(1)}" text-anchor="end">${v}</text>`; }).join("");
  const bars = pts.map((b, i) => {
    const cx = L + slot * (i + .5), x0 = (cx - bw / 2).toFixed(1);
    let acc = 0, out = "";
    for (const [k, cls] of [["ok", "b-ok"], ["ko", "b-ko"], ["run", "b-run"]]) {
      if (!b[k]) continue;
      const y1 = y(acc + b[k]), y0 = y(acc);
      out += `<rect class="${cls}" x="${x0}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, y0 - y1).toFixed(1)}" rx="1.5"/>`;
      acc += b[k];
    }
    const label = i % every === 0 || i === pts.length - 1 ? `<text class="axis" x="${cx.toFixed(1)}" y="${H - 8}" text-anchor="middle">${axisDate(b.t, series.step)}</text>` : "";
    const tip = `${axisDate(b.t, series.step)}: ${t("{n} riuscite", {n: b.ok})}, ${t("{n} non riuscite", {n: b.ko})}${b.run ? ", " + t("{n} in corso", {n: b.run}) : ""}`;
    return `<g><title>${esc(tip)}</title><rect x="${(cx - slot / 2).toFixed(1)}" y="${T}" width="${slot.toFixed(1)}" height="${H - T - B}" fill="transparent"/>${out}</g>${label}`;
  }).join("");
  return `<div class="util"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("Stampe"))}">${grid}${bars}</svg>
    <div class="legend"><span><i class="b-ok"></i>${t("Riuscite")}</span><span><i class="b-ko"></i>${t("Non riuscite")}</span><span><i class="b-run"></i>${t("In corso")}</span></div></div>`;
}
function downloadMenu(q) {
  const items = [[`/api/stats/export.json?${q}`, t("JSON (tutti i dati)")],
    [`/api/stats/export.csv?kind=jobs&${q}`, t("CSV – dettaglio stampe")],
    [`/api/stats/export.csv?kind=filaments&${q}`, t("CSV – filamenti")],
    [`/api/stats/export.csv?kind=printers&${q}`, t("CSV – per stampante")],
    [`/api/stats/export.csv?kind=daily&${q}`, t("CSV – per giorno")],
    [`/api/stats/export.xlsx?${q}`, t("Excel (tutte le tabelle)")]];
  return `<details class="dlmenu"><summary class="btn">${t("Scarica")} ▾</summary>
    <div class="menu">${items.map(([h, l]) => `<a href="${h}" download>${l}</a>`).join("")}</div></details>`;
}
// Un solo periodo per tutta la pagina: utilizzo, grafico delle stampe, riepilogo ed esportazioni
const PERIOD_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365};
const periodLabel = p => p === "24h" ? t("Ultime 24 ore") : t("Ultimi {n} giorni", {n: PERIOD_DAYS[p]});
RENDER.stats = async function renderStats() {
  let period = localStorage.getItem("fb_period");
  if (!PERIOD_DAYS[period]) period = {7: "7d", 30: "30d", 90: "90d", 365: "365d"}[localStorage.getItem("fb_days")] || "30d";
  const days = PERIOD_DAYS[period];
  const grp = localStorage.getItem("fb_stats_group") || "";
  const el = $("#v-stats");
  const [s, ut] = await Promise.all([
    api(`/api/stats?days=${days}&period=${period}${grp ? "&group=" + grp : ""}`).catch(e => (toast(e.message, "error"), null)),
    api(`/api/stats/utilization?range=${period}${grp ? "&group=" + grp : ""}`).catch(() => null)]);
  if (!s) return;
  const tot = s.total, rate = tot.n ? Math.round(100 * (tot.ok || 0) / tot.n) : 0;
  el.innerHTML = `<div class="head"><div><h1>${t("Statistiche")}</h1><p class="sub">${periodLabel(period)}</p></div>
    <div class="row">${GROUPS.length ? `<select id="sgroup"><option value="">${t("Tutte le stampanti")}</option>${GROUPS.map(g => `<option value="${g.id}" ${String(g.id) === grp ? "selected" : ""}>${t("Gruppo {g}", {g: esc(g.name)})}</option>`).join("")}</select>` : ""}
    <select id="period">${Object.keys(PERIOD_DAYS).map(k => `<option value="${k}" ${k === period ? "selected" : ""}>${periodLabel(k)}</option>`).join("")}</select>
    ${downloadMenu(`days=${days}${grp ? "&group=" + grp : ""}&lang=${LANG}`)}</div></div>
    ${utilization(ut, grp)}
    <h2>${t("Riepilogo del periodo")}</h2>
    <div class="kpis"><div class="kpi"><b>${tot.n}</b><span>${t("stampe")}</span></div><div class="kpi"><b>${rate}%</b><span>${t("riuscite")}</span></div>
      <div class="kpi"><b>${Math.round(tot.secs / 3600)} h</b><span>${t("di stampa")}</span></div><div class="kpi"><b>${(tot.grams / 1000).toFixed(2)} kg</b><span>${t("filamento")}</span></div>
      <div class="kpi"><b>${tot.ko || 0}</b><span>${t("fallite")}</span></div></div>
    <h2>${t("Stampe")}</h2>
    ${printsChart(s.series)}
    <h2>${t("Filamento consumato")}</h2>
    ${s.filaments && s.filaments.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Tipo")}</th><th>${t("Marca")}</th><th>${t("Colore")}</th><th>${t("Filamento (g)")}</th><th>${t("Metri")}</th><th>${t("Stampe")}</th></tr></thead><tbody>
    ${s.filaments.map(f => `<tr><td>${esc(f.type)}</td><td>${esc(f.brand || "—")}</td><td><span class="sw" style="--c:${esc(f.color)}"></span>${esc(f.color)}</td><td><b>${f.grams >= 1000 ? (f.grams / 1000).toFixed(2) + " kg" : Math.round(f.grams) + " g"}</b></td><td>${(f.meters || 0).toFixed(1)}</td><td>${f.prints}</td></tr>`).join("")}
    </tbody></table></div><p class="sub" style="margin-top:8px">${t("Il consumo per tipo e colore è noto per le stampe avviate da FleetBuddy o il cui file è nell'archivio.")}</p>`
      : `<p class="sub">${t("Nessun consumo registrato nel periodo. Il dettaglio è disponibile per le stampe avviate da FleetBuddy o il cui file è nell'archivio.")}</p>`}
    <h2>${t("Per stampante")}</h2>
    <div class="tablewrap"><table><thead><tr><th>${t("Stampante")}</th><th>${t("Stampe")}</th><th>${t("Riuscite")}</th><th>${t("Fallite")}</th><th>${t("Ore")}</th><th>${t("Filamento")}</th></tr></thead><tbody>
    ${s.per_printer.map(p => `<tr><td>${esc(p.name)}</td><td>${p.n}</td><td>${p.ok || 0}</td><td>${p.ko || 0}</td><td>${(p.secs / 3600).toFixed(1)}</td><td>${Math.round(p.grams)} g</td></tr>`).join("") || `<tr><td colspan="6">${t("Nessun dato")}</td></tr>`}
    </tbody></table></div>`;
  $("#period").onchange = e => { localStorage.setItem("fb_period", e.target.value); RENDER.stats(); };
  if ($("#sgroup")) $("#sgroup").onchange = e => { localStorage.setItem("fb_stats_group", e.target.value); RENDER.stats(); };
};

// ------------------------------------------------------------------ hub
RENDER.hub = async function renderHub() {
  const el = $("#v-hub");
  el.innerHTML = `<div class="head"><div><h1>${t("Amministrazione")}</h1></div></div>${adminTabs("hub")}
    <p class="sub">${t("Collegamento stampanti, firmware e impostazioni del box.")}</p><div id="hubinfo" class="cards2"><div class="box">${t("Lettura dal Hub…")}</div></div>
    <h2>${t("Accesso al Web Client del Hub")}</h2>
    <div class="box"><p class="sub" style="margin:0 0 10px">${t("Facoltativo: abilita l'interfaccia di manutenzione del Hub su https://IP-del-Hub. Senza questo account la porta 443 resta chiusa. Usa una password di almeno 12 caratteri, diversa da quella dell'account API.")}</p>
      <div class="row"><label>${t("Nome utente")}<input id="wu" autocomplete="off" size="14"></label><label>${t("Password")}<input id="wp" type="password" autocomplete="new-password" size="18"></label>
      <button id="wadd">${t("Crea account web")}</button><button id="wdel" class="danger">${t("Elimina account web")}</button></div></div>
    <h2>${t("Aggiungi stampanti")}</h2>
    <div class="box"><div class="row"><button id="disc" class="primary">${t("Cerca nella rete del Hub")}</button>
      <label>${t("Oppure scansiona la sottorete")}<input id="pref" placeholder="192.168.1" size="12"></label><label>${t("Da")}<input id="sb" type="number" value="1" min="1" max="255" style="width:70px"></label>
      <label>${t("A")}<input id="se" type="number" value="254" min="1" max="255" style="width:70px"></label><button id="scan">${t("Scansiona")}</button></div>
      <div id="found" style="margin-top:12px"></div></div>
    <h2>${t("Stampanti collegate")}</h2><div id="bound"></div>
    <h2>${t("Firmware stampanti")}</h2>
    <div class="box"><div class="row"><label>${t("Carica il file offline-ota-….zip scaricato da bambulab.com")}<input type="file" id="fw" accept=".zip"></label><button id="fwup">${t("Carica sul Hub")}</button></div><div id="fwlist" style="margin-top:12px"></div></div>
    <h2>${t("File nella cache del Hub")}</h2><div id="cache"></div>`;
  api("/api/hub").then(h => {
    const pct = (u, a) => (u + a) ? Math.round(100 * u / (u + a)) : 0;
    const ntpOn = h.ntp?.ntp_switch === "open";
    $("#hubinfo").innerHTML = `<div class="box"><dl><dt>${t("Nome")}</dt><dd>${esc(h.name)}</dd><dt>${t("Seriale")}</dt><dd>${esc(h.hub_sn)}</dd>
      <dt>${t("Attivazione")}</dt><dd>${h.activate_state === "Activate" ? t("Attivo") : t("Non attivato")}</dd><dt>${t("Firmware")}</dt><dd>${esc(h.version)}</dd>
      <dt>${t("Stampanti")}</dt><dd>${t("{n} di {max}", {n: S.printers.length, max: h.max_bind_num})}</dd></dl></div>
      <div class="box"><dl><dt>${t("Memoria interna")}</dt><dd>${t("{u} GB usati, {a} GB liberi", {u: h.internal?.used ?? 0, a: h.internal?.available ?? 0})}</dd></dl>
      <div class="meter"><i style="width:${pct(h.internal?.used || 0, h.internal?.available || 0)}%"></i></div>
      <p class="sub" style="margin:10px 0 0">${t("Sotto i 20 GB liberi il Hub elimina da solo i file non usati da 30 giorni.")}</p></div>
      <div class="box"><dl><dt>${t("Ora del Hub")}</dt><dd>${esc(h.ntp?.date || "—")} (${esc(h.ntp?.time_zone || "")})</dd><dt>NTP</dt><dd>${ntpOn ? t("Attivo") : t("Disattivo")}</dd></dl>
      <div class="row" style="margin-top:10px"><button id="ntpon">${ntpOn ? t("Disattiva NTP") : t("Attiva NTP")}</button><button id="synct">${t("Allinea all'ora di questo dispositivo")}</button></div></div>`;
    $("#ntpon").onclick = async () => { await act(() => api("/api/hub/ntp", {method: "PUT", body: {ntp_switch: ntpOn ? "close" : "open"}}), t("NTP aggiornato")); RENDER.hub(); };
    $("#synct").onclick = async () => {
      const d = new Date(), off = -d.getTimezoneOffset(), tz = `UTC${off >= 0 ? "+" : "-"}${Math.floor(Math.abs(off) / 60)}:${String(Math.abs(off) % 60).padStart(2, "0")}`;
      const pad = n => String(n).padStart(2, "0");
      const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      await act(() => api("/api/hub/ntp", {method: "PUT", body: {time_zone: tz, date}}), t("Ora del Hub aggiornata")); RENDER.hub();
    };
  }).catch(e => $("#hubinfo").innerHTML = `<div class="box">${esc(e.message)}</div>`);

  $("#wadd").onclick = async () => {
    const pwd = $("#wp").value;
    if (pwd.length < 12 && !confirm(t("La password ha meno di 12 caratteri, la lunghezza consigliata dal produttore. Continuare?"))) return;
    const r = await act(() => api("/api/hub/web_user", {method: "POST", body: {username: $("#wu").value, password: pwd}}), t("Account web creato"));
    if (r) $("#wp").value = "";
  };
  $("#wdel").onclick = async () => { if (confirm(t("Eliminare l'account del Web Client? L'interfaccia web del Hub non sarà più accessibile."))) await act(() => api("/api/hub/web_user", {method: "DELETE"}), t("Account web eliminato")); };
  const showFound = list => {
    $("#found").innerHTML = list.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Nome")}</th><th>${t("Modello")}</th><th>IP</th><th>${t("Seriale")}</th><th>${t("Stato")}</th><th></th></tr></thead><tbody>${list.map(p => {
      const can = p.farm !== false && p.bind !== "occupied" && p.connect !== "lan" && !p.fw_min;
      const why = p.fw_min ? t("firmware {v} troppo vecchio (minimo {min})", {v: p.version || "", min: p.fw_min}) : p.farm === false ? t("firmware troppo vecchio")
        : p.connect === "lan" ? t("in modalità solo LAN") : p.bind === "occupied" ? (p.server_ip ? t("collegata a {ip}", {ip: p.server_ip}) : t("già associata")) : "";
      return `<tr><td>${esc(p.name)}</td><td>${esc(p.model)}</td><td>${esc(p.location)}</td><td>${esc(p.usn)}</td><td>${can ? t("Disponibile") : esc(why)}</td>
        <td>${can ? `<button data-bind="${esc(p.usn)}" data-ip="${esc(p.location)}">${t("Collega al Hub")}</button>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div>` : `<p class="sub">${t("Nessuna stampante trovata. Le stampanti devono avere un firmware recente e non essere in modalità solo LAN né associate a Bambu Handy.")}</p>`;
  };
  $("#disc").onclick = async () => { $("#found").textContent = t("Ricerca…"); const r = await act(() => api("/api/hub/discover")); if (r) showFound(r); };
  $("#scan").onclick = async () => { $("#found").textContent = t("Scansione in corso (può richiedere un minuto)…");
    const r = await act(() => api(`/api/hub/scan?prefix=${encodeURIComponent($("#pref").value)}&begin=${$("#sb").value}&end=${$("#se").value}`)); if (r) showFound(r); };
  $("#found").onclick = async e => { const b = e.target.closest("[data-bind]"); if (!b) return; b.disabled = true;
    await act(() => api("/api/hub/bind", {method: "POST", body: {dev_id: b.dataset.bind, dev_ip: b.dataset.ip}}), t("Stampante collegata al Hub")); };

  $("#bound").innerHTML = S.printers.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Nome")}</th><th>${t("Modello")}</th><th>IP</th><th>${t("Firmware")}</th><th>${t("Aggiornamento")}</th><th></th></tr></thead><tbody>
    ${S.printers.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.model)}</td><td>${esc(p.ip)}</td><td>${esc(p.firmware)}</td><td>${esc(p.upgrade_state || "—")}</td>
    <td><button class="danger" data-unbind="${p.sn}">${t("Scollega")}</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="sub">${t("Nessuna stampante collegata.")}</p>`;
  $("#bound").onclick = async e => { const b = e.target.closest("[data-unbind]"); if (!b || !confirm(t("Scollegare la stampante dal Hub?"))) return;
    const r = await act(() => api("/api/hub/unbind", {method: "POST", body: {dev_ids: [b.dataset.unbind]}}));
    if (r) (r.unbind_results || []).forEach(x => toast(x.code ? `${x.dev_id}: ${x.message}` : t("{sn} scollegata", {sn: x.dev_id}), x.code ? "error" : "ok")); };

  const loadFw = async () => {
    const fws = await api("/api/hub/firmwares").catch(() => []);
    $("#fwlist").innerHTML = fws.length ? `<div class="tablewrap"><table><thead><tr><th>${t("File")}</th><th>${t("Modello")}</th><th>${t("Versione")}</th><th></th></tr></thead><tbody>${fws.map(f => `<tr><td>${esc(f.name)}</td><td>${esc(f.model)}</td><td>${esc(f.version)}</td>
      <td style="white-space:nowrap"><button data-up="${esc(f.name)}">${t("Aggiorna stampanti")}</button> <button class="danger" data-delfw="${esc(f.name)}">${t("Elimina")}</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="sub">${t("Nessun firmware caricato.")}</p>`;
  };
  loadFw();
  $("#fwup").onclick = async () => { const f = $("#fw").files[0]; if (!f) return toast(t("Scegli prima il file del firmware"), "error");
    const fd = new FormData(); fd.append("file", f); toast(t("Caricamento firmware sul Hub…"));
    await act(() => api("/api/hub/firmwares", {method: "POST", body: fd}), t("Firmware caricato")); loadFw(); };
  $("#fwlist").onclick = async e => {
    const up = e.target.closest("[data-up]"), del = e.target.closest("[data-delfw]");
    if (del && confirm(t("Eliminare il firmware dal Hub?"))) { await act(() => api("/api/hub/firmwares", {method: "DELETE", body: {firmwares: [del.dataset.delfw]}})); loadFw(); }
    if (up) {
      const cands = S.printers.filter(p => !p.active);
      openDialog(`<h3>${t("Aggiorna con {f}", {f: esc(up.dataset.up)})}</h3><p class="sub" style="margin:0">${t("Seleziona le stampanti inattive da aggiornare. Il Hub verifica la compatibilità del modello.")}</p>
        ${cands.map(p => `<label class="inline"><input type="checkbox" value="${p.sn}"> ${esc(p.name)} (${esc(p.model)}, ${esc(p.firmware)})</label>`).join("") || t("Nessuna stampante inattiva.")}
        <div class="dlgfoot">${btnCancel()}<button class="primary" id="doup">${t("Avvia aggiornamento")}</button></div>`);
      $("#doup").onclick = async ev => { ev.preventDefault();
        const ids = $$("#dlgbody input:checked").map(i => i.value); if (!ids.length) return;
        const r = await act(() => api("/api/hub/upgrade", {method: "POST", body: {dev_ids: ids, firmware_name: up.dataset.up}}));
        if (r) { (r.init_upgrade_results || []).forEach(x => toast(x.code ? `${x.dev_id}: ${x.message}` : t("{sn}: aggiornamento avviato", {sn: x.dev_id}), x.code ? "error" : "ok")); $("#dlg").close(); } };
    }
  };
  api("/api/hub/cache").then(c => $("#cache").innerHTML = c.length ? `<div class="tablewrap"><table><thead><tr><th>${t("File")}</th><th>MD5</th><th>${t("Ultimo uso")}</th></tr></thead><tbody>${c.map(f =>
    `<tr><td>${esc(f.filename || t("(non in archivio)"))}</td><td><code>${esc(f.file_hash)}</code></td><td>${esc(f.last_use_time)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="sub">${t("Cache vuota.")}</p>`).catch(() => {});
};

// ------------------------------------------------------------------ amministrazione
// ---------------------------------------------------------------- stampanti collegate direttamente
// campi: host sempre; api = chiave API (obbligatoria se "api!"), user/pass = utente e password, slug = stampante su Repetier
let CATALOG = {brands: {}};
fetch("/static/catalog.json").then(r => r.json()).then(c => { CATALOG = c; }).catch(() => {});
const DIRECT_KINDS = {
  octoprint: {label: "OctoPrint", fields: ["api!"], host: "192.168.1.50"},
  moonraker: {label: "Klipper (Moonraker)", fields: ["api"], host: "192.168.1.60:7125"},
  prusalink: {label: "PrusaLink", fields: ["user", "pass", "api"], host: "192.168.1.70"},
  duet: {label: "Duet / RepRapFirmware 3", fields: ["pass"], host: "192.168.1.179"},
  repetier: {label: "Repetier-Server", fields: ["api!", "slug"], host: "192.168.1.80:3344"},
  elegoo: {label: "Elegoo (SDCP)", fields: [], host: "192.168.1.90"},
  raise3d: {label: "Raise3D (Raise Touch)", fields: ["pass"], host: "192.168.1.100"},
};
// suggerimenti mostrati nel modulo (frasi del catalogo traduzioni)
const DIRECT_HINTS = {
  octoprint: "Chiave API: OctoPrint → Impostazioni → Application Keys (o API).",
  moonraker: "Mainsail/Fluidd usano Moonraker: indirizzo con porta 7125, oppure quello di Mainsail/Fluidd. Chiave API solo se richiesta.",
  prusalink: "Utente maker e password mostrati sulla stampante (Impostazioni → Rete → PrusaLink). Firmware 5 o successivo.",
  duet: "Lo stesso indirizzo di Duet Web Control; password solo se impostata con M551.",
  repetier: "Chiave API: Repetier-Server → Impostazioni globali → Connettività. Stampante: lo slug, se il server ne gestisce più di una.",
  raise3d: "Attiva sulla stampante Impostazioni → Remote Access API e usa la password impostata lì. L'API non permette di caricare file: si avviano quelli già presenti nella stampante.",
  elegoo: "Centauri Carbon e resine Elegoo recenti (protocollo SDCP): basta l'indirizzo IP. Le porte 3000 (UDP) e 3030 devono essere raggiungibili.",
};
RENDER.direct = async function renderDirect() {
  const el = $("#v-direct");
  const rows = await api("/api/direct").catch(e => (toast(e.message, "error"), []));
  el.innerHTML = `<div class="head"><div><h1>${t("Amministrazione")}</h1></div></div>${adminTabs("direct")}
    <p class="sub">${t("Stampanti collegate direttamente a FleetBuddy, senza passare dal Fleet Hub. Compaiono nell'elenco delle stampanti insieme alle Bambu, con storico, statistiche, coda, gruppi e permessi.")}</p>
    <div class="row" style="margin-bottom:12px"><button class="primary" id="dadd">${t("Aggiungi stampante")}</button></div>
    ${rows.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Nome")}</th><th>${t("Tipo")}</th><th>${t("Indirizzo")}</th><th>${t("Stato")}</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr data-id="${r.id}"><td><b>${esc(r.name)}</b>${r.enabled ? "" : ` <small class="muted">· ${t("disattivata")}</small>`}</td>
        <td>${esc((DIRECT_KINDS[r.kind] || {}).label || r.kind)}${r.board ? `<br><small class="muted">${esc(r.board)} · ${esc(r.firmware || "")}${r.mode ? " · " + (r.mode === "sbc" ? "SBC" : t("autonoma")) : ""}</small>` : ""}</td>
        <td>${esc(r.host)}${r.brand || r.printer_model ? `<br><small class="muted">${esc([r.brand, r.printer_model].filter(Boolean).join(" "))}</small>` : ""}</td>
        <td>${r.online ? `<span class="st finished">${t("Connessa")}</span> · ${stateName(r.state)}` : `<span class="st failed">${t("Non raggiungibile")}</span>${r.error ? `<br><small class="muted">${esc(r.error)}</small>` : ""}`}</td>
        <td style="white-space:nowrap"><button data-edit>${t("Modifica")}</button> <button class="danger" data-del>${t("Elimina")}</button></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">${t("Nessuna stampante collegata direttamente. Con \"Aggiungi stampante\" puoi collegare per esempio una stampante con scheda Duet.")}</div>`}
    <h2>${t("Tipi supportati")}</h2>
    <div class="tablewrap"><table><thead><tr><th>${t("Tipo")}</th><th>${t("Stato")}</th><th>${t("Pausa / stop")}</th><th>${t("Invio file")}</th><th>${t("Temperature")}</th><th>${t("Velocità")}</th><th>${t("Camera")}</th></tr></thead><tbody>
      ${[["OctoPrint", 1, 1, ".gcode", 1, 1, t("automatica")], ["Klipper (Moonraker)", 1, 1, ".gcode", 1, 1, t("automatica")],
         ["PrusaLink", 1, 1, ".bgcode / .gcode", 0, 0, t("manuale")], ["Duet / RepRapFirmware 3", 1, 1, ".gcode", 1, 1, t("manuale")],
         ["Repetier-Server", 1, 1, ".gcode", 1, 1, t("manuale")], ["Elegoo (SDCP)", 1, 1, ".gcode", 0, 0, t("automatica")], ["Raise3D (Raise Touch)", 1, 1, t("solo file già sulla stampante"), 1, 1, t("automatica")]].map(([n, a, b, c, d, e2, f]) =>
        `<tr><td><b>${n}</b></td><td>${a ? "✓" : "—"}</td><td>${b ? "✓" : "—"}</td><td>${c}</td><td>${d ? "✓" : "—"}</td><td>${e2 ? "✓" : "—"}</td><td>${f}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="sub" style="margin-top:8px">${t("PrusaLink e SDCP (Elegoo) non hanno comandi di rete per temperature e velocità: si cambiano dalla stampante. \"Automatica\": l'indirizzo della webcam viene letto dalla stampante; \"manuale\": va indicato nel modulo. Senza camera, la scheda mostra l'anteprima della stampa.")}</p>`;
  $("#dadd").onclick = () => directDialog();
  const tb = el.querySelector("table");
  if (tb) tb.onclick = async e => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    const r = rows.find(x => x.id === +tr.dataset.id);
    if (e.target.closest("[data-edit]")) directDialog(r);
    if (e.target.closest("[data-del]") && confirm(t("Eliminare la stampante {p} da FleetBuddy? Lo storico delle sue stampe resta.", {p: r.name}))) {
      await act(() => api(`/api/direct/${r.id}`, {method: "DELETE"}), t("Stampante eliminata")); RENDER.direct();
    }
  };
};
function directDialog(r) {
  openDialog(`<h3>${r ? t("Modifica {p}", {p: esc(r.name)}) : t("Aggiungi stampante")}</h3>
    <div class="row"><label style="flex:1">${t("Nome")}<input name="dname" value="${esc(r ? r.name : "")}" maxlength="64" placeholder="Elemento-v21"></label>
      <label>${t("Tipo")}<select name="kind" ${r ? "disabled" : ""}>${Object.entries(DIRECT_KINDS).map(([k, d]) => `<option value="${k}" ${r && r.kind === k ? "selected" : ""}>${d.label}</option>`).join("")}</select></label></div>
    <p class="sub small" id="khint" style="margin:0"></p>
    <div class="row"><label style="flex:1">${t("Marca")}<input name="brand" list="dl-brands" value="${esc(r ? r.brand || "" : "")}" autocomplete="off"></label>
      <label style="flex:1">${t("Modello")}<input name="pmodel" list="dl-models" value="${esc(r ? r.printer_model || "" : "")}" autocomplete="off"></label></div>
    <datalist id="dl-brands">${Object.keys(CATALOG.brands || {}).map(b => `<option value="${esc(b)}">`).join("")}</datalist><datalist id="dl-models"></datalist>
    <label>${t("Indirizzo")}<input name="host" value="${esc(r ? r.host : "")}"></label>
    <label data-f="user">${t("Utente")}<input name="user" value="${esc(r ? r.username || "" : "")}" placeholder="maker" autocomplete="off"></label>
    <label data-f="pass"><span data-pl></span><input name="pw" type="password" autocomplete="new-password"></label>
    <label data-f="api"><span data-al></span><input name="api" type="password" autocomplete="off"></label>
    <label data-f="slug">${t("Stampante su Repetier-Server (slug, facoltativo)")}<input name="slug" value="${esc(r ? r.extra || "" : "")}" placeholder="ender3"></label>
    <label>${t("Indirizzo della foto della webcam (facoltativo)")}<input name="snap" value="${esc(r ? r.snapshot_url : "")}" placeholder="http://192.168.1.179:8080/?action=snapshot"></label>
    ${r ? `<label class="inline"><input type="checkbox" name="en" ${r.enabled ? "checked" : ""}> ${t("Attiva")}</label>` : ""}
    <div id="dtest" class="small"></div>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button type="button" id="dtry">${t("Prova connessione")}</button><button class="primary" id="dsave">${t("Salva")}</button></div>`);
  const fm = $("#dlgbody"), val = n => fm.elements.namedItem(n);
  const showFields = () => {   // solo i campi che servono al tipo scelto
    const d = DIRECT_KINDS[val("kind").value], f = d.fields.map(x => x.replace("!", ""));
    $$("#dlgbody [data-f]").forEach(l => l.hidden = !f.includes(l.dataset.f));
    $("#khint").textContent = t(DIRECT_HINTS[val("kind").value]);
    val("host").placeholder = d.host;
    $("#dlgbody [data-pl]").textContent = r && r.has_password ? t("Password (lascia vuoto per non cambiarla)") : t("Password");
    $("#dlgbody [data-al]").textContent = r && r.has_api_key ? t("Chiave API (lascia vuoto per non cambiarla)")
      : d.fields.includes("api!") ? t("Chiave API") : t("Chiave API (facoltativa)");
  };
  const KIND_BRAND = {prusalink: "Prusa", elegoo: "Elegoo", raise3d: "Raise3D"};
  const fillModels = () => { $("#dl-models").innerHTML = ((CATALOG.brands || {})[val("brand").value] || []).map(m => `<option value="${esc(m)}">`).join(""); };
  val("brand").oninput = fillModels; fillModels();
  val("kind").onchange = () => { showFields(); if (!r && (!val("brand").value || Object.values(KIND_BRAND).includes(val("brand").value))) { val("brand").value = KIND_BRAND[val("kind").value] || ""; fillModels(); } };
  showFields(); if (!r && !val("brand").value) { val("brand").value = KIND_BRAND[val("kind").value] || ""; fillModels(); }
  const body = () => ({id: r ? r.id : undefined, name: val("dname").value || val("host").value, kind: val("kind").value, host: val("host").value,
    password: val("pw").value, api_key: val("api").value, username: val("user").value, extra: val("slug").value,
    brand: val("brand").value.trim(), printer_model: val("pmodel").value.trim(),
    snapshot_url: val("snap").value, ...(r ? {enabled: val("en").checked} : {})});
  $("#dtry").onclick = async () => {
    $("#dtest").innerHTML = `<span class="muted">${t("Collegamento in corso…")}</span>`;
    try {
      const x = await api("/api/direct/test", {method: "POST", body: body()});
      $("#dtest").innerHTML = `<span class="st finished">✓ ${t("Collegata")}</span> · ${esc(x.board || "")} · ${esc(x.firmware || "?")}${x.mode ? " · " + esc(x.mode === "sbc" ? "SBC" : x.mode === "standalone" ? t("autonoma") : x.mode) : ""} · ${t("{n} utensili", {n: x.tools})}${x.snapshot_configured ? " · " + (x.snapshot ? t("foto OK") : `<span class="warn">${t("foto non disponibile")}</span>`) : ""}`;
    } catch (e) { $("#dtest").innerHTML = `<span class="warn">✗ ${esc(e.message)}</span>`; }
  };
  $("#dsave").onclick = async ev => {
    ev.preventDefault();
    const ok = await act(() => r ? api(`/api/direct/${r.id}`, {method: "PATCH", body: body()}) : api("/api/direct", {method: "POST", body: body()}), t("Stampante salvata"));
    if (ok) { $("#dlg").close(); RENDER.direct(); }
  };
}
RENDER.admin = async function renderAdmin() {
  const el = $("#v-admin");
  await loadMeta();
  const [users, allGroups] = await Promise.all([api("/api/users").catch(e => (toast(e.message, "error"), [])), api("/api/groups").catch(() => [])]);
  const projs = PROJECTS.filter(p => p.id !== null);
  const refreshOpts = [[15, t("ogni 15 secondi")], [30, t("ogni 30 secondi")], [60, t("ogni minuto")], [120, t("ogni 2 minuti")], [300, t("ogni 5 minuti")]];
  el.innerHTML = `<div class="head"><div><h1>${t("Amministrazione")}</h1></div></div>${adminTabs("admin")}
    <p class="sub">${t("Utenti, gruppi di stampanti e permessi di accesso.")}</p>
    <h2>${t("Utenti")}</h2>
    <div class="row" style="margin-bottom:12px"><button class="primary" id="uadd">${t("Nuovo utente")}</button></div>
    <div class="tablewrap"><table><thead><tr><th>${t("Nome utente")}</th><th>${t("Nome e cognome")}</th><th>${t("Ruolo")}</th><th>${t("Lingua")}</th><th>${t("Può vedere")}</th><th>${t("Ultimo accesso")}</th><th></th></tr></thead><tbody>
    ${users.map(u => {
      const sees = u.role === "admin" ? t("Tutto") : [
        ...u.groups.map(id => { const g = allGroups.find(x => x.id === id); return g ? t("gruppo {g}", {g: esc(g.name)}) : ""; }),
        ...u.printers.map(sn => esc(printerName(sn))),
        ...u.projects.map(id => { const p = projs.find(x => x.id === id); return p ? t("progetto {p}", {p: esc(p.name)}) : ""; })].filter(Boolean).join(", ") || `<span class="muted">${t("Niente (da configurare)")}</span>`;
      return `<tr data-id="${u.id}" class="${u.active ? "" : "muted"}"><td><b>${esc(u.username)}</b>${u.active ? "" : ` <small class="muted">· ${t("disattivato")}</small>`}</td><td>${esc(fullName(u)) || `<span class="muted">—</span>`}</td>
        <td><span class="role ${u.role}">${roleName(u.role)}</span></td><td>${esc(LANGS[u.language] || "—")}</td><td style="max-width:360px">${sees}</td><td>${fmtDate(u.last_login)}</td>
        <td style="white-space:nowrap">${u.role !== "admin" ? `<button data-acc>${t("Permessi")}</button>` : ""} <button data-edit>${t("Modifica")}</button> ${u.id !== ME.id ? `<button class="danger" data-del>${t("Elimina")}</button>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div>
    <p class="sub" style="margin-top:8px">${t("Amministratore: tutto. Operatore: comanda le stampanti e stampa i file dei progetti assegnati. Osservatore: solo visualizzazione. Le modifiche ai permessi si applicano entro un minuto.")}</p>

    <h2>${t("Gruppi di stampanti")}</h2>
    <div class="row" style="margin-bottom:12px"><button class="primary" id="gadd">${t("Nuovo gruppo")}</button></div>
    ${allGroups.length ? `<div class="list">${allGroups.map(g => `<div class="item" style="grid-template-columns:1fr auto" data-id="${g.id}">
      <div><div class="t"><span class="tag" style="--c:${esc(g.color)};font-size:15px;color:var(--ink)"><i></i>${esc(g.name)}</span></div>
      <div class="m">${g.printers.length ? g.printers.map(printerName).map(esc).join(", ") : t("Nessuna stampante")}</div></div>
      <div class="a"><button data-gedit>${t("Modifica")}</button><button class="danger" data-gdel>${t("Elimina")}</button></div></div>`).join("")}</div>`
      : `<p class="sub">${t("Nessun gruppo. I gruppi servono a filtrare le stampanti, a mettere in coda \"sulla prima libera del gruppo\" e a dare accesso a più stampanti in un colpo solo.")}</p>`}

    <h2>${t("Impostazioni")}</h2>
    <div class="box"><div class="row">
      <label>${t("Aggiornamento camera nelle schede")}<select id="camref">${refreshOpts.map(([v, l]) => `<option value="${v}" ${v === S.settings.camera_refresh_s ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>${t("Lingua delle notifiche")}<select id="notlang">${Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${k === (S.settings.notify_lang || "it") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    </div><p class="sub" style="margin:10px 0 0">${t("La lingua delle notifiche vale per ntfy, Telegram e webhook, che non sono legati a un utente.")}</p></div>

    <h2>${t("Informazioni")}</h2>
    <div class="box"><dl><dt>${t("Applicazione")}</dt><dd>${esc(APP.name)} v${esc(APP.version)}</dd>
      <dt>${t("Aggiornamenti")}</dt><dd id="updbox">${updateBox(UPDATE)}</dd>
      ${APP.repo ? `<dt>${t("Repository")}</dt><dd><a href="${esc(APP.repo)}" target="_blank" rel="noopener">${esc(APP.repo)}</a></dd>` : ""}
      <dt>${t("Utenti")}</dt><dd>${users.length}</dd><dt>${t("Stampanti collegate")}</dt><dd>${S.printers.length}</dd></dl></div>`;

  $("#uadd").onclick = () => userDialog();
  $("#gadd").onclick = () => groupDialog();
  const saveSetting = async body => { const r = await act(() => api("/api/settings", {method: "PUT", body}), t("Impostazione salvata")); if (r) { S.settings = r; startCamTimer(); } };
  $("#camref").onchange = e => saveSetting({camera_refresh_s: +e.target.value});
  wireUpdateBox();
  $("#notlang").onchange = e => saveSetting({notify_lang: e.target.value});
  el.querySelector("table").onclick = async e => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return; const u = users.find(x => x.id === +tr.dataset.id);
    if (e.target.closest("[data-acc]")) accessDialog(u, allGroups, projs);
    if (e.target.closest("[data-edit]")) userDialog(u);
    if (e.target.closest("[data-del]") && confirm(t("Eliminare l'utente {u}?", {u: u.username}))) { await act(() => api(`/api/users/${u.id}`, {method: "DELETE"}), t("Utente eliminato")); RENDER.admin(); }
  };
  $$("[data-gedit],[data-gdel]", el).forEach(b => b.onclick = async () => {
    const g = allGroups.find(x => x.id === +b.closest(".item").dataset.id);
    if (b.hasAttribute("data-gedit")) return groupDialog(g);
    if (confirm(t("Eliminare il gruppo {g}? Le stampanti restano collegate; le stampe in coda per questo gruppo andranno in errore.", {g: g.name}))) { await act(() => api(`/api/groups/${g.id}`, {method: "DELETE"}), t("Gruppo eliminato")); RENDER.admin(); }
  });
};
const filCell = j => j.filaments && j.filaments.length
  ? `<div class="fils">${j.filaments.map(f => { const est = f.source === "ams";
      return `<span title="${esc([f.type, f.brand, f.color].filter(Boolean).join(" · ") + (est ? " – " + t("Stima dal calo del residuo delle bobine AMS") : ""))}"><i style="--c:${esc(f.color)}"></i>${esc(f.type)} ${est ? "≈" : ""}${f.grams} g</span>`; }).join("")}</div>`
  : (j.filament_g ? `${j.filament_g} g` : `<span class="muted" title="${esc(t("Consumo non disponibile: la stampa non è partita da FleetBuddy e il file non è nell'archivio"))}">—</span>`);
const fullName = u => [u.first_name, u.last_name].filter(Boolean).join(" ");
// Password casuale di 14 caratteri (lettere, numeri e simboli), senza caratteri che si confondono (0/O, 1/l/I)
function generatePassword(len = 14) {
  const sets = ["abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "!@#%&*?-+="];
  const all = sets.join(""), rnd = n => { const a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] % n; };
  const out = sets.map(s => s[rnd(s.length)]);              // almeno uno per tipo
  while (out.length < len) out.push(all[rnd(all.length)]);
  for (let i = out.length - 1; i > 0; i--) { const j = rnd(i + 1); [out[i], out[j]] = [out[j], out[i]]; }
  return out.join("");
}
// ---------------------------------------------------------------- aggiornamenti dal browser
function updateBox(st) {
  const when = st ? `<br><small class="muted">${t("Ultimo controllo: {d}", {d: fmtDate(st.checked_at)})}</small>` : "";
  const msg = !st ? `<span class="muted">${t("Non ancora controllati")}</span>`
    : st.available ? `<b>${t("Disponibile la versione {v}", {v: esc(st.latest)})}</b>`
    : `${t("FleetBuddy è aggiornato")} <span class="muted">(${t("ultima versione pubblicata: {v}", {v: esc(st.latest || APP.version)})})</span>`;
  return `${msg}${when}<div class="row" style="margin-top:8px"><button type="button" id="updcheck">${t("Cerca aggiornamenti")}</button>
    ${st && st.available ? `<button type="button" class="primary" id="updinstall">${t("Installa la versione {v}", {v: esc(st.latest)})}</button>` : ""}</div>`;
}
function wireUpdateBox(changelog) {
  const box = $("#updbox"); if (!box) return;
  $("#updcheck").onclick = async ev => {
    ev.target.disabled = true; ev.target.textContent = t("Controllo in corso…");
    const r = await act(() => api("/api/admin/update/check", {method: "POST"}));
    if (r) { UPDATE = r.status; showVersion(); box.innerHTML = updateBox(r.status); wireUpdateBox(r.changelog);
      toast(r.status && r.status.available ? t("Disponibile la versione {v}", {v: r.status.latest}) : t("FleetBuddy è aggiornato"), "ok"); }
    else { ev.target.disabled = false; ev.target.textContent = t("Cerca aggiornamenti"); }
  };
  if ($("#updinstall")) $("#updinstall").onclick = () => installDialog(UPDATE.latest, changelog);
}
function installDialog(version, changelog) {
  openDialog(`<h3>${t("Installa la versione {v}", {v: esc(version)})}</h3>
    ${changelog ? `<pre class="chlog">${esc(changelog)}</pre>` : ""}
    <p class="sub" style="margin:0">${t("Prima dell'installazione viene fatto un backup. FleetBuddy si riavvierà per circa un minuto (le stampe in corso continuano); se la nuova versione non parte, si torna da soli a quella attuale.")}</p>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button class="primary" id="doinstall">${t("Installa ora")}</button></div>`);
  $("#doinstall").onclick = async ev => {
    ev.preventDefault();
    if (!await act(() => api("/api/admin/update/install", {method: "POST"}))) return;
    progressDialog(version);
  };
}
// Segue l'aggiornamento: durante il riavvio il server non risponde per qualche secondo, quindi si riprova
function progressDialog(version) {
  openDialog(`<h3>${t("Aggiornamento in corso…")}</h3>
    <p class="sub" style="margin:0" id="updstate">${t("Backup e download della nuova versione…")}</p>
    <pre class="chlog" id="updlog"></pre>
    <div class="dlgfoot"><button value="cancel" id="updclose" disabled>${t("Chiudi")}</button></div>`);
  const started = Date.now();
  const timer = setInterval(async () => {
    let info = null, ver = null;
    try { info = await api("/api/admin/update"); ver = info.version; } catch { /* riavvio in corso */ }
    if (info) $("#updlog").textContent = info.log || "";
    const failed = info && /ripristino|ERRORE/.test(info.log || "") && !info.running;
    if (ver === version) {
      clearInterval(timer);
      $("#updstate").textContent = t("FleetBuddy aggiornato alla versione {v}. La pagina si ricarica…", {v: version});
      setTimeout(() => location.reload(), 2500);
    } else if (failed) {
      clearInterval(timer);
      $("#updstate").innerHTML = `<span class="warn">${t("Aggiornamento non riuscito: la versione precedente è stata ripristinata. Dettagli qui sotto.")}</span>`;
      $("#updclose").disabled = false;
    } else if (!info) {
      $("#updstate").textContent = t("Riavvio di FleetBuddy…");
    } else if (Date.now() - started > 6 * 60 * 1000) {
      clearInterval(timer);
      $("#updstate").textContent = t("L'aggiornamento sta impiegando più del previsto: controlla sul Raspberry Pi con journalctl -u fleetbuddy-update-now");
      $("#updclose").disabled = false;
    }
  }, 3000);
}
function userDialog(u) {
  openDialog(`<h3>${u ? t("Modifica {u}", {u: esc(u.username)}) : t("Nuovo utente")}</h3>
    <div class="row">
      <label>${t("Nome")}<input name="first_name" value="${esc(u ? u.first_name || "" : "")}" maxlength="64" autocomplete="off"></label>
      <label>${t("Cognome")}<input name="last_name" value="${esc(u ? u.last_name || "" : "")}" maxlength="64" autocomplete="off"></label>
      <label>${t("Nome utente")}<input name="username" ${u ? `value="${esc(u.username)}" disabled` : ""} autocomplete="off" maxlength="32"></label>
    </div>
    <div class="row">
      <label>${t("Ruolo")}<select name="role">${Object.keys(ROLES).map(k => `<option value="${k}" ${(u ? u.role : "operator") === k ? "selected" : ""}>${roleName(k)}</option>`).join("")}</select></label>
      <label>${t("Lingua")}<select name="language">${Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${(u ? u.language || LANG : LANG) === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    </div>
    <label>${u ? t("Nuova password (lascia vuoto per non cambiarla)") : t("Password")}
      <span class="pwrow"><input name="password" type="password" autocomplete="new-password">
      <button type="button" id="pwgen">${t("Genera password")}</button><button type="button" id="pwcopy" hidden>${t("Copia credenziali")}</button></span></label>
    ${u && u.id === ME.id ? "" : `<label class="inline"><input type="checkbox" name="must_change" checked> ${t("Proponi di cambiarla al primo accesso")}</label>`}
    ${u && u.id !== ME.id ? `<label class="inline"><input type="checkbox" name="active" ${u.active ? "checked" : ""}> ${t("Utente attivo (se disattivato non può accedere)")}</label>` : ""}
    <p class="sub" style="margin:0">${t("Password: almeno 8 caratteri, con lettere e numeri.")}${u ? "" : " " + t("Dopo la creazione assegna i permessi con il pulsante \"Permessi\".")}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="uok">${u ? t("Salva") : t("Crea utente")}</button></div>`);
  $("#pwgen").onclick = () => {
    const f = $("#dlgbody");
    f.password.value = generatePassword(); f.password.type = "text"; f.password.select();
    $("#pwcopy").hidden = false;
  };
  $("#pwcopy").onclick = async () => {
    const fm2 = $("#dlgbody");
    const v = `${t("Nome utente")}: ${u ? u.username : fm2.username.value}\n${t("Password")}: ${fm2.password.value}`;
    try { await navigator.clipboard.writeText(v); toast(t("Nome utente e password copiati"), "ok"); }
    catch { $("#dlgbody").password.select(); toast(t("Selezionata: copiala con Ctrl+C"), "info"); }
  };
  $("#uok").onclick = async ev => {
    ev.preventDefault(); const f = $("#dlgbody");
    const body = {first_name: f.first_name.value, last_name: f.last_name.value, role: f.role.value, language: f.language.value};
    if (f.must_change) body.must_change = f.must_change.checked;
    if (f.password.value) body.password = f.password.value;
    if (f.active) body.active = f.active.checked;
    let r;
    if (u) r = await act(() => api(`/api/users/${u.id}`, {method: "PATCH", body}), t("Utente aggiornato"));
    else { body.username = f.username.value; body.password = f.password.value; r = await act(() => api("/api/users", {method: "POST", body}), t("Utente creato")); }
    if (r) { $("#dlg").close(); if (u && u.id === ME.id && body.language !== LANG) { await setLang(body.language, false); reRender(); } else RENDER.admin(); }
  };
}
function accessDialog(u, groups, projs) {
  const box = (name, items, checked) => items.length ? `<div class="checks">${items.map(([v, label]) =>
    `<label class="inline"><input type="checkbox" name="${name}" value="${esc(v)}" ${checked.includes(v) ? "checked" : ""}> ${label}</label>`).join("")}</div>` : `<p class="sub" style="margin:0">${t("Nessun elemento.")}</p>`;
  openDialog(`<h3>${t("Permessi di {u}", {u: esc(fullName(u) ? `${fullName(u)} (${u.username})` : u.username)})}</h3>
    <fieldset><legend>${t("Gruppi di stampanti")}</legend>${box("groups", groups.map(g => [g.id, `${esc(g.name)} <small class="muted">(${g.printers.length})</small>`]), u.groups)}</fieldset>
    <fieldset><legend>${t("Singole stampanti")}</legend>${box("printers", S.printers.map(p => [p.sn, `${esc(p.name)} <small class="muted">${esc(p.model)}</small>`]), u.printers)}</fieldset>
    <fieldset><legend>${t("Progetti dell'archivio")}</legend>${box("projects", projs.map(p => [p.id, esc(p.name)]), u.projects)}</fieldset>
    <p class="sub" style="margin:0">${t("Dare accesso a un gruppo include anche le stampanti aggiunte al gruppo in futuro.")}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="accok">${t("Salva permessi")}</button></div>`);
  $("#accok").onclick = async ev => {
    ev.preventDefault();
    const vals = n => $$(`#dlgbody input[name="${n}"]:checked`).map(i => i.value);
    const r = await act(() => api(`/api/users/${u.id}/access`, {method: "PUT", body: {groups: vals("groups").map(Number), printers: vals("printers"), projects: vals("projects").map(Number)}}), t("Permessi salvati"));
    if (r) { $("#dlg").close(); RENDER.admin(); }
  };
}
function groupDialog(g) {
  openDialog(`<h3>${g ? t("Modifica gruppo") : t("Nuovo gruppo")}</h3>
    <div class="row"><label style="flex:1">${t("Nome")}<input name="gname" value="${esc(g ? g.name : "")}" maxlength="40"></label>
      <label>${t("Colore")}<input type="color" name="color" value="${esc(g ? g.color : "#0E7C6B")}"></label></div>
    <fieldset><legend>${t("Stampanti del gruppo")}</legend><div class="checks">${S.printers.map(p =>
      `<label class="inline"><input type="checkbox" name="pr" value="${esc(p.sn)}" ${g && g.printers.includes(p.sn) ? "checked" : ""}> ${esc(p.name)} <small class="muted">${esc(p.model)}</small></label>`).join("") || t("Nessuna stampante collegata.")}</div></fieldset>
    <p class="sub" style="margin:0">${t("Una stampante può far parte di più gruppi.")}</p>
    <div class="dlgfoot">${btnCancel()}<button class="primary" id="gok">${g ? t("Salva") : t("Crea gruppo")}</button></div>`);
  $("#gok").onclick = async ev => {
    ev.preventDefault(); const f = $("#dlgbody");
    const body = {name: f.gname.value, color: f.color.value, printers: $$('#dlgbody input[name="pr"]:checked').map(i => i.value)};
    const r = await act(() => g ? api(`/api/groups/${g.id}`, {method: "PATCH", body}) : api("/api/groups", {method: "POST", body}), g ? t("Gruppo aggiornato") : t("Gruppo creato"));
    if (r) { $("#dlg").close(); await loadMeta(); RENDER.admin(); }
  };
}

$$("select.langsel").forEach(s => { s.innerHTML = langOptions(); });
document.body.classList.add("locked");
startCamTimer();
boot();
