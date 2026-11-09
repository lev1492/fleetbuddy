"use strict";
// ======================================================================== Amministrazione → Generali
// Tema dell'interfaccia e calendario lavorativo (orari per giorno con pause, paese e festività, fuso orario,
// chiusure programmate) usato dalla Produzione. Usa le funzioni comuni di app.js.

const CAL_MODES = {
  start: "Nuove stampe solo in orario lavorativo (possono finire fuori orario)",
  full: "Stampe interamente in orario lavorativo (iniziano e finiscono in orario)",
  always: "Sempre (24 ore su 24, 7 giorni su 7: il calendario è ignorato)",
};
const THEMES = {auto: "Automatico (come il sistema)", light: "Chiaro", dark: "Scuro"};
const weekdayName = d => new Date(2024, 0, 1 + d).toLocaleDateString(LOCALES[LANG], {weekday: "long"});   // 1/1/2024 = lunedì
const regionName = code => { try { return new Intl.DisplayNames([LOCALES[LANG]], {type: "region"}).of(code) || code; } catch { return code; } };
let GEN = null;

RENDER.general = async function renderGeneral() {
  const el = $("#v-general");
  try { GEN = await api("/api/settings/general"); }
  catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const cfg = JSON.parse(JSON.stringify(GEN.calendar));
  let theme = GEN.theme || "auto";
  const country = () => GEN.countries.find(c => c.code === cfg.country) || {subdivs: []};
  el.innerHTML = `<div class="head"><div><h1>${t("Amministrazione")}</h1></div></div>${adminTabs("general")}
    <div class="gen">
      <section class="box"><h2>${t("Aspetto")}</h2>
        <div class="seg" id="gtheme">${Object.entries(THEMES).map(([k, l]) => `<button type="button" data-v="${k}" class="${k === theme ? "on" : ""}">${t(l)}</button>`).join("")}</div>
        <p class="sub small">${t("Vale per tutti gli utenti di FleetBuddy.")}</p></section>

      <section class="box"><h2>${t("Calendario lavorativo")}</h2>
        <p class="sub" style="margin-top:0">${t("La Produzione pianifica e avvia i pezzi tenendo conto di questi orari, delle festività e delle chiusure. Il cambio piatto richiede una persona, quindi avviene sempre in orario lavorativo.")}</p>
        <label>${t("Modalità")}<select id="gmode">${Object.entries(CAL_MODES).map(([k, l]) => `<option value="${k}" ${k === cfg.mode ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label>
        <div class="row">
          <label style="flex:1">${t("Paese")}<select id="gcountry">${GEN.countries.map(c => `<option value="${c.code}" ${c.code === cfg.country ? "selected" : ""}>${esc(regionName(c.code))} (${c.code})</option>`).join("")}</select></label>
          <label style="flex:1">${t("Regione o provincia (festività locali)")}<select id="gsubdiv"></select></label>
          <label style="flex:1">${t("Fuso orario")}<input id="gtz" list="gtzl" value="${esc(cfg.timezone)}"><datalist id="gtzl">${GEN.timezones.map(z => `<option value="${esc(z)}">`).join("")}</datalist></label>
        </div>
        <label class="inline"><input type="checkbox" id="ghol" ${cfg.use_holidays ? "checked" : ""}> ${t("Considera le festività nazionali come giorni di chiusura")}</label>
        <p class="sub small" id="gtzinfo"></p>
      </section>

      <section class="box"><h2>${t("Orari settimanali")}</h2>
        <p class="sub" style="margin-top:0">${t("Per ogni giorno, uno o più intervalli (es. 09:00–13:00 e 14:00–18:00 con la pausa pranzo). Un giorno senza intervalli è di chiusura.")}</p>
        <div id="gweek" class="gweek"></div>
        <div class="row small"><button type="button" class="linkbtn" id="gcopy">${t("Copia gli orari di lunedì su tutti i giorni feriali")}</button></div>
      </section>

      <section class="box"><h2>${t("Chiusure programmate")}</h2>
        <p class="sub" style="margin-top:0">${t("Ferie, inventario, ponti: giorni interi in cui la produzione non avvia stampe.")}</p>
        <div id="gclos"></div>
        <button type="button" id="gaddclos">＋ ${t("Aggiungi chiusura")}</button>
      </section>

      <section class="box"><h2>${t("Prossimi giorni di chiusura")}</h2><div id="gdays" class="gdays"></div></section>
      <div class="gsave"><span id="gmsg" class="small"></span><button class="primary" id="gsavebtn">${t("Salva le impostazioni")}</button></div>
    </div>`;

  const drawSubdiv = () => {
    const subs = country().subdivs || [];
    $("#gsubdiv").innerHTML = `<option value="">${t("Nessuna (solo festività nazionali)")}</option>` +
      subs.map(s => `<option value="${esc(s)}" ${s === cfg.subdiv ? "selected" : ""}>${esc(s)}</option>`).join("");
    $("#gsubdiv").disabled = !subs.length;
  };
  const drawTzInfo = () => {
    try {
      const fmt = new Intl.DateTimeFormat(LOCALES[LANG], {timeZone: cfg.timezone, timeZoneName: "long", hour: "2-digit", minute: "2-digit"});
      $("#gtzinfo").textContent = t("Ora attuale nel fuso scelto: {t}. Il passaggio tra ora legale e solare è automatico.", {t: fmt.format(new Date())});
    } catch { $("#gtzinfo").textContent = t("Fuso orario non valido"); }
  };
  const drawWeek = () => {
    $("#gweek").innerHTML = [0, 1, 2, 3, 4, 5, 6].map(d => {
      const iv = cfg.hours[String(d)] || [];
      return `<div class="gday ${iv.length ? "" : "off"}" data-d="${d}">
        <label class="inline gdn"><input type="checkbox" data-k="on" ${iv.length ? "checked" : ""}> <b>${esc(weekdayName(d))}</b></label>
        <div class="givs">${iv.length ? iv.map(([a, b], i) => `<span class="giv" data-i="${i}"><input type="time" data-k="a" value="${a}" step="300"> – <input type="time" data-k="b" value="${b === "24:00" ? "23:59" : b}" step="300">
          <button type="button" class="linkbtn" data-k="del" title="${esc(t("Rimuovi"))}">✕</button></span>`).join(`<span class="gpause">${t("pausa")}</span>`)
          : `<span class="muted">${t("Chiuso")}</span>`}
          ${iv.length ? `<button type="button" class="linkbtn" data-k="add">＋ ${t("intervallo")}</button>` : ""}</div>
        <span class="muted small gtot">${iv.length ? fmtDur(iv.reduce((n, [a, b]) => n + (toMin(b) - toMin(a)) * 60, 0)) : ""}</span></div>`;
    }).join("");
  };
  const toMin = h => { const [x, y] = h.split(":").map(Number); return x * 60 + y; };
  const drawClosures = () => {
    $("#gclos").innerHTML = cfg.closures.length ? cfg.closures.map((c, i) => `<div class="gclo" data-i="${i}">
      <label class="inline">${t("dal")} <input type="date" data-k="from" value="${c.from}"></label>
      <label class="inline">${t("al")} <input type="date" data-k="to" value="${c.to}"></label>
      <input data-k="note" value="${esc(c.note || "")}" placeholder="${esc(t("es. Ferie estive"))}" maxlength="80" style="flex:1;min-width:140px">
      <button type="button" class="linkbtn" data-k="del">${t("Rimuovi")}</button></div>`).join("")
      : `<p class="muted small">${t("Nessuna chiusura programmata.")}</p>`;
  };
  let ptm;
  const preview = () => { clearTimeout(ptm); ptm = setTimeout(async () => {
    try {
      const r = await api("/api/settings/general/preview", {method: "POST", body: {calendar: cfg}});
      $("#gdays").innerHTML = r.days_off.length ? r.days_off.slice(0, 40).map(d => `<div class="gdo ${d.kind}"><b>${new Date(d.date + "T12:00:00").toLocaleDateString(LOCALES[LANG], {weekday: "short", day: "numeric", month: "long", year: "numeric"})}</b>
        <span>${esc(d.name)}</span><small class="muted">${d.kind === "closure" ? t("chiusura programmata") : t("festività")}</small></div>`).join("")
        : `<p class="muted small">${t("Nessun giorno di chiusura nei prossimi 12 mesi.")}</p>`;
      $("#gmsg").textContent = "";
    } catch (e) { $("#gmsg").innerHTML = `<span class="warn">${esc(e.message)}</span>`; }
  }, 300); };
  drawSubdiv(); drawTzInfo(); drawWeek(); drawClosures(); preview();

  $("#gtheme").onclick = e => { const b = e.target.closest("[data-v]"); if (!b) return; theme = b.dataset.v;
    $$("#gtheme button").forEach(x => x.classList.toggle("on", x === b)); applyTheme(theme); };
  $("#gmode").onchange = e => { cfg.mode = e.target.value; };
  $("#gcountry").onchange = e => {
    cfg.country = e.target.value; cfg.subdiv = "";
    const tz = country().timezone; if (tz) { cfg.timezone = tz; $("#gtz").value = tz; drawTzInfo(); }
    drawSubdiv(); preview();
  };
  $("#gsubdiv").onchange = e => { cfg.subdiv = e.target.value; preview(); };
  $("#gtz").onchange = e => { cfg.timezone = e.target.value.trim(); drawTzInfo(); };
  $("#ghol").onchange = e => { cfg.use_holidays = e.target.checked; preview(); };
  $("#gweek").onclick = e => {
    const k = e.target.dataset.k, day = e.target.closest("[data-d]"); if (!day || !["add", "del"].includes(k)) return;
    const iv = cfg.hours[day.dataset.d];
    if (k === "add") { const last = iv[iv.length - 1]; const s = last ? last[1] : "09:00"; iv.push([s, toMin(s) + 240 < 1440 ? `${String(Math.floor((toMin(s) + 240) / 60)).padStart(2, "0")}:${String((toMin(s) + 240) % 60).padStart(2, "0")}` : "23:59"]); }
    if (k === "del") iv.splice(+e.target.closest("[data-i]").dataset.i, 1);
    drawWeek();
  };
  $("#gweek").onchange = e => {
    const k = e.target.dataset.k, day = e.target.closest("[data-d]"); if (!day) return;
    const d = day.dataset.d;
    if (k === "on") cfg.hours[d] = e.target.checked ? [["09:00", "13:00"], ["14:00", "18:00"]] : [];
    else if (k === "a" || k === "b") cfg.hours[d][+e.target.closest("[data-i]").dataset.i][k === "a" ? 0 : 1] = e.target.value === "23:59" && k === "b" ? "24:00" : e.target.value;
    drawWeek();
  };
  $("#gcopy").onclick = () => { for (const d of [1, 2, 3, 4]) cfg.hours[String(d)] = JSON.parse(JSON.stringify(cfg.hours["0"])); drawWeek(); };
  $("#gaddclos").onclick = () => { const today = new Date().toISOString().slice(0, 10); cfg.closures.push({from: today, to: today, note: ""}); drawClosures(); preview(); };
  $("#gclos").oninput = $("#gclos").onchange = e => {
    const row = e.target.closest("[data-i]"); if (!row) return;
    const c = cfg.closures[+row.dataset.i], k = e.target.dataset.k;
    if (k === "del") return;
    c[k] = e.target.value;
    if (e.type === "change") preview();
  };
  $("#gclos").onclick = e => { if (e.target.dataset.k !== "del") return; cfg.closures.splice(+e.target.closest("[data-i]").dataset.i, 1); drawClosures(); preview(); };
  $("#gsavebtn").onclick = async () => {
    const r = await act(() => api("/api/settings/general", {method: "PUT", body: {calendar: cfg, theme}}), t("Impostazioni salvate"));
    if (r) { GEN.calendar = r.calendar; S.settings = {...(S.settings || {}), theme}; }
  };
};
