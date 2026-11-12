// Libreria Materiali: inventario di bobine, filamenti e produttori (sul modello di Spoolman).
// Il consumo di ogni stampa viene scalato dalla bobina assegnata allo slot della stampante.
"use strict";

let MAT = null, MTAB = localStorage.getItem("fb_mtab") || "spools", MSEARCH = "", MARCH = false;
const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PA-CF", "PC", "PP", "PVA", "HIPS", "PLA-CF", "PETG-CF", "PEEK", "PEKK", "PEI", "PPS"];
const kg = g => g >= 1000 ? `${(g / 1000).toLocaleString(LOCALES[LANG], {maximumFractionDigits: 1})} kg` : `${Math.round(g)} g`;
const swatch = c => `<span class="msw" style="--c:${esc(c || "#888")}"></span>`;
const filLabel = f => [f.vendor, f.material, f.name || f.filament_name].filter(Boolean).join(" ");
const num = v => (v === "" || v == null) ? null : Number(v);

RENDER.materials = async function renderMaterials() {
  const el = $("#v-materials");
  try { MAT = await api("/api/materials"); }
  catch (e) { el.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
  const s = MAT.summary, edit = can("materials");
  el.innerHTML = `<div class="head"><div><h1>${t("Libreria Materiali")}</h1>
      <p class="sub">${t("Inventario di bobine, filamenti e produttori. Il consumo di ogni stampa viene scalato dalla bobina assegnata allo slot della stampante.")}</p></div></div>
    <div class="kpis mkpis">
      <div class="kpi"><span>${t("Bobine attive")}</span><b>${s.spools}</b></div>
      <div class="kpi"><span>${t("Filamento disponibile")}</span><b>${kg(s.grams)}</b></div>
      <div class="kpi ${s.low ? "warnk" : ""}"><span>${t("Quasi finite (sotto il 15%)")}</span><b>${s.low}</b></div>
      <div class="kpi"><span>${t("Assegnate alle stampanti")}</span><b>${s.assigned}</b></div>
    </div>
    <div class="tabs" role="tablist">${[["spools", t("Bobine")], ["filaments", t("Filamenti")], ["vendors", t("Produttori")]]
      .map(([k, l]) => `<a href="#materials" data-mtab="${k}" class="${MTAB === k ? "on" : ""}">${l}</a>`).join("")}</div>
    <div id="mbody"></div>`;
  $$("#v-materials [data-mtab]").forEach(a => a.onclick = e => { e.preventDefault(); MTAB = a.dataset.mtab; localStorage.setItem("fb_mtab", MTAB); RENDER.materials(); });
  ({spools: drawSpools, filaments: drawFilaments, vendors: drawVendors})[MTAB](edit);
};

// ---------------------------------------------------------------- bobine
function drawSpools(edit) {
  const box = $("#mbody");
  const q = MSEARCH.toLowerCase();
  const rows = MAT.spools.filter(s => (MARCH || !s.archived) && (!q || [filLabel(s), s.location, s.lot_nr, s.slot_label, printerName(s.printer_sn)]
    .join(" ").toLowerCase().includes(q)));
  box.innerHTML = `<div class="row mtools">
      <input type="search" id="msearch" placeholder="${esc(t("Cerca per materiale, colore, posizione, lotto…"))}" value="${esc(MSEARCH)}">
      <label class="inline"><input type="checkbox" id="march" ${MARCH ? "checked" : ""}> ${t("Mostra archiviate")}</label>
      <span class="grow"></span>${edit ? `<button class="primary" id="madd" ${MAT.filaments.length ? "" : "disabled"} title="${MAT.filaments.length ? "" : esc(t("Crea prima un filamento"))}">${t("Nuova bobina")}</button>` : ""}</div>
    ${!MAT.filaments.length ? `<div class="empty">${t("Per iniziare crea un produttore e un filamento, poi aggiungi le bobine.")}</div>` :
    rows.length ? `<div class="tablewrap"><table class="mtable"><thead><tr><th>#</th><th>${t("Filamento")}</th><th>${t("Residuo")}</th><th>${t("Stampante")}</th>
      <th>${t("Posizione")}</th><th>${t("Lotto")}</th><th>${t("Ultimo uso")}</th><th></th></tr></thead><tbody>
      ${rows.map(s => `<tr data-id="${s.id}" class="${s.archived ? "march" : ""}"><td class="muted">${s.id}</td>
        <td>${swatch(s.color_hex)} <b>${esc(filLabel(s))}</b>${s.diameter && s.diameter !== 1.75 ? ` <small class="muted">${s.diameter} mm</small>` : ""}</td>
        <td class="mrem"><span class="mbar ${s.pct != null && s.pct < 15 ? "low" : ""}"><i style="width:${Math.max(0, Math.min(100, s.pct ?? 0))}%"></i></span>
          <span>${kg(s.remaining)}${s.pct != null ? ` <small class="muted">${s.pct}%</small>` : ""}</span></td>
        <td>${s.printer_sn ? `${esc(printerName(s.printer_sn))}${s.slot_label ? ` <span class="side">${esc(s.slot_label)}</span>` : ""}` : `<span class="muted">—</span>`}</td>
        <td>${esc(s.location || "—")}</td><td>${esc(s.lot_nr || "—")}</td><td>${s.last_used ? fmtDate(s.last_used) : "—"}</td>
        <td class="macts">${edit && !s.archived ? `<button data-a="assign">${t("Assegna")}</button><button data-a="weigh">${t("Pesa")}</button>` : ""}
          <button data-a="usage">${t("Storico")}</button>
          ${edit ? `<button data-a="edit">${t("Modifica")}</button><button data-a="arch">${s.archived ? t("Ripristina") : t("Archivia")}</button>` : ""}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">${t("Nessuna bobina.")}</div>`}`;
  $("#msearch") && ($("#msearch").oninput = e => { MSEARCH = e.target.value; const p = e.target.selectionStart; drawSpools(edit); $("#msearch").focus(); $("#msearch").setSelectionRange(p, p); });
  $("#march") && ($("#march").onchange = e => { MARCH = e.target.checked; drawSpools(edit); });
  $("#madd") && ($("#madd").onclick = () => spoolDialog());
  box.onclick = async e => {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    const s = MAT.spools.find(x => x.id === +b.closest("tr").dataset.id);
    if (b.dataset.a === "edit") spoolDialog(s);
    else if (b.dataset.a === "assign") assignDialog(s);
    else if (b.dataset.a === "weigh") weighDialog(s);
    else if (b.dataset.a === "usage") usageDialog(s);
    else if (b.dataset.a === "arch" && await act(() => api(`/api/materials/spools/${s.id}/archive`, {method: "POST", body: {archived: !s.archived}}),
      s.archived ? t("Bobina ripristinata") : t("Bobina archiviata"))) RENDER.materials();
  };
}

function spoolDialog(s) {
  const fopts = MAT.filaments.map(f => `<option value="${f.id}" data-w="${f.weight || ""}" data-sw="${f.spool_weight ?? ""}" ${s && s.filament_id === f.id ? "selected" : ""}>${esc(filLabel(f))}</option>`).join("");
  openDialog(`<h3>${s ? t("Bobina #{n}", {n: s.id}) : t("Nuova bobina")}</h3>
    <label>${t("Filamento")}<select name="filament_id">${fopts}</select></label>
    <div class="row">
      <label>${t("Peso netto iniziale (g)")}<input name="initial_weight" type="number" min="0" step="1" value="${s ? s.initial_weight ?? "" : ""}"></label>
      <label>${t("Peso bobina vuota (g)")}<input name="spool_weight" type="number" min="0" step="1" value="${s ? s.spool_weight ?? "" : ""}"></label>
      <label>${t("Prezzo (€)")}<input name="price" type="number" min="0" step="0.01" value="${s ? s.price ?? "" : ""}"></label></div>
    <div class="row">
      <label>${t("Posizione")}<input name="location" maxlength="64" value="${esc(s ? s.location : "")}" placeholder="${esc(t("es. Scaffale A2, essiccatore"))}"></label>
      <label>${t("Lotto")}<input name="lot_nr" maxlength="64" value="${esc(s ? s.lot_nr : "")}"></label>
      ${s ? "" : `<label>${t("Quantità")}<input name="count" type="number" min="1" max="50" value="1"></label>`}</div>
    <label>${t("Note")}<textarea name="comment" rows="2" maxlength="1024">${esc(s ? s.comment : "")}</textarea></label>
    <p class="sub small">${t("Se il peso iniziale resta vuoto vale il peso netto del filamento; il peso della bobina vuota serve per la pesata con la bilancia.")}</p>
    <div class="dlgfoot">${s ? `<button type="button" class="danger" id="mdel">${t("Elimina")}</button><span class="grow"></span>` : ""}<button value="cancel">${t("Annulla")}</button><button class="primary" id="msave">${t("Salva")}</button></div>`);
  const f = $("#dlgbody");
  const fill = () => { const o = f.elements.namedItem("filament_id").selectedOptions[0];
    if (!s && o) { f.elements.namedItem("initial_weight").placeholder = o.dataset.w || ""; f.elements.namedItem("spool_weight").placeholder = o.dataset.sw || ""; } };
  f.elements.namedItem("filament_id").onchange = fill; fill();
  $("#msave").onclick = async e => {
    e.preventDefault();
    const v = n => f.elements.namedItem(n) ? f.elements.namedItem(n).value : "";
    const body = {filament_id: +v("filament_id"), initial_weight: num(v("initial_weight")), spool_weight: num(v("spool_weight")), price: num(v("price")),
                  location: v("location").trim(), lot_nr: v("lot_nr").trim(), comment: v("comment").trim(), count: +v("count") || 1};
    if (await act(() => s ? api(`/api/materials/spools/${s.id}`, {method: "PUT", body}) : api("/api/materials/spools", {method: "POST", body}), t("Bobina salvata"))) {
      $("#dlg").close(); RENDER.materials();
    }
  };
  if (s) $("#mdel").onclick = async e => {
    e.preventDefault();
    if (await uiConfirm(t("Eliminare la bobina #{n} e il suo storico? Per conservarla nello storico usa Archivia.", {n: s.id}), {danger: true}))
      if (await act(() => api(`/api/materials/spools/${s.id}`, {method: "DELETE"}), t("Bobina eliminata"))) { $("#dlg").close(); RENDER.materials(); }
  };
}

// slot di una stampante a cui si può assegnare una bobina
function printerSlots(p) {
  if (!p) return [];
  const trays = (p.trays || []).map(x => ({ams_id: x.ams_id ?? null, slot_id: x.slot_id ?? null, label: x.label || ""}));
  if (trays.length) return trays;
  return (p.nozzles || []).map((n, i) => ({ams_id: null, slot_id: i, label: n.label || sideLetter(n.side) || `T${i}`}));
}

function assignDialog(s) {
  const printers = S.printers.slice().sort((a, b) => a.name.localeCompare(b.name));
  openDialog(`<h3>${t("Assegna la bobina #{n}", {n: s.id})}</h3><p class="sub" style="margin:0">${swatch(s.color_hex)} ${esc(filLabel(s))} · ${kg(s.remaining)}</p>
    <label>${t("Stampante")}<select name="printer"><option value="">${t("Nessuna (in magazzino)")}</option>
      ${printers.map(p => `<option value="${esc(p.sn)}" ${p.sn === s.printer_sn ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
    <label id="mslotl">${t("Slot")}<select name="slot"></select></label>
    <p class="sub small">${t("Il consumo delle stampe su questo slot verrà scalato da questa bobina. Una bobina già assegnata allo stesso slot torna in magazzino.")}</p>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button class="primary" id="masg">${t("Assegna")}</button></div>`);
  const f = $("#dlgbody"), sel = f.elements.namedItem("printer"), slot = f.elements.namedItem("slot");
  const fill = () => {
    const slots = printerSlots(printers.find(p => p.sn === sel.value));
    $("#mslotl").hidden = !sel.value || !slots.length;
    slot.innerHTML = slots.map((x, i) => `<option value="${i}" ${s.printer_sn === sel.value && x.ams_id === s.ams_id && x.slot_id === s.slot_id ? "selected" : ""}>${esc(x.label || t("Slot {n}", {n: i + 1}))}</option>`).join("");
    slot._slots = slots;
  };
  sel.onchange = fill; fill();
  $("#masg").onclick = async e => {
    e.preventDefault();
    const x = sel.value && slot._slots.length ? slot._slots[+slot.value] : {ams_id: null, slot_id: null, label: ""};
    if (await act(() => api(`/api/materials/spools/${s.id}/assign`, {method: "POST", body: {printer_sn: sel.value || null, ams_id: x.ams_id, slot_id: x.slot_id, label: x.label}}),
      sel.value ? t("Bobina assegnata") : t("Bobina in magazzino"))) { $("#dlg").close(); RENDER.materials(); }
  };
}

function weighDialog(s) {
  openDialog(`<h3>${t("Pesa la bobina #{n}", {n: s.id})}</h3><p class="sub" style="margin:0">${swatch(s.color_hex)} ${esc(filLabel(s))} · ${t("residuo calcolato {g}", {g: kg(s.remaining)})}</p>
    <label>${t("Peso sulla bilancia, bobina compresa (g)")}<input name="gross" type="number" min="0" step="1"></label>
    <p class="sub small" style="margin:-4px 0 6px">${t("oppure")}</p>
    <label>${t("Filamento rimasto (g)")}<input name="remaining" type="number" min="0" step="1"></label>
    <p class="sub small">${t("Il residuo viene corretto con il valore pesato; le stampe successive continuano a scalare da lì.")}</p>
    <div class="dlgfoot"><button value="cancel">${t("Annulla")}</button><button class="primary" id="mw">${t("Salva")}</button></div>`);
  $("#mw").onclick = async e => {
    e.preventDefault();
    const f = $("#dlgbody"), g = f.elements.namedItem("gross").value, r = f.elements.namedItem("remaining").value;
    if (await act(() => api(`/api/materials/spools/${s.id}/weigh`, {method: "POST", body: {gross: g === "" ? null : +g, remaining: r === "" ? null : +r}}), t("Residuo aggiornato"))) {
      $("#dlg").close(); RENDER.materials();
    }
  };
}

async function usageDialog(s) {
  const rows = await act(() => api(`/api/materials/spools/${s.id}/usage`));
  if (!rows) return;
  openDialog(`<h3>${t("Storico della bobina #{n}", {n: s.id})}</h3><p class="sub" style="margin:0">${swatch(s.color_hex)} ${esc(filLabel(s))} · ${kg(s.remaining)}</p>
    ${rows.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Data")}</th><th>${t("Stampa")}</th><th>${t("Stampante")}</th><th>${t("Consumo")}</th></tr></thead><tbody>
      ${rows.map(u => `<tr><td>${fmtDate(u.ts)}</td><td>${u.note === "weigh" ? `<i>${t("Pesata: residuo corretto")}</i>` : esc(u.task_name || "—")}</td>
        <td>${esc(u.printer_name || "")}</td><td>${u.note === "weigh" ? "" : `${u.grams} g`}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">${t("Nessuna stampa registrata su questa bobina.")}</p>`}
    <div class="dlgfoot"><button value="cancel">${t("Chiudi")}</button></div>`);
}

// ---------------------------------------------------------------- filamenti
function drawFilaments(edit) {
  const cnt = id => MAT.spools.filter(s => s.filament_id === id && !s.archived).length;
  $("#mbody").innerHTML = `<div class="row mtools"><span class="grow"></span>${edit ? `<button class="primary" id="fadd">${t("Nuovo filamento")}</button>` : ""}</div>
    ${MAT.filaments.length ? `<div class="tablewrap"><table class="mtable"><thead><tr><th>${t("Produttore")}</th><th>${t("Materiale")}</th><th>${t("Nome")}</th>
      <th>${t("Diametro")}</th><th>${t("Peso netto")}</th><th>${t("Densità")}</th><th>${t("Temperature")}</th><th>${t("Prezzo")}</th><th>${t("Bobine")}</th><th></th></tr></thead><tbody>
      ${MAT.filaments.map(f => `<tr data-id="${f.id}"><td>${esc(f.vendor || "—")}</td><td>${swatch(f.color_hex)} <b>${esc(f.material)}</b></td><td>${esc(f.name || "")}</td>
        <td>${f.diameter} mm</td><td>${kg(f.weight || 0)}</td><td>${f.density} g/cm³</td>
        <td>${[f.nozzle_temp ? `${t("ugello")} ${f.nozzle_temp}°` : "", f.bed_temp ? `${t("piatto")} ${f.bed_temp}°` : ""].filter(Boolean).join(" · ") || "—"}</td>
        <td>${f.price != null ? `${f.price.toLocaleString(LOCALES[LANG], {style: "currency", currency: "EUR"})}` : "—"}</td><td>${cnt(f.id)}</td>
        <td class="macts">${edit ? `<button data-a="edit">${t("Modifica")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${t("Nessun filamento. Un filamento descrive un prodotto (es. PLA nero 1 kg di un produttore); le bobine sono gli esemplari in magazzino.")}</div>`}`;
  $("#fadd") && ($("#fadd").onclick = () => filamentDialog());
  $("#mbody").onclick = e => { const b = e.target.closest("button[data-a=edit]"); if (b) filamentDialog(MAT.filaments.find(x => x.id === +b.closest("tr").dataset.id)); };
}

function filamentDialog(f) {
  openDialog(`<h3>${f ? t("Filamento") : t("Nuovo filamento")}</h3>
    <div class="row">
      <label>${t("Produttore")}<select name="vendor_id"><option value="">—</option>${MAT.vendors.map(v => `<option value="${v.id}" ${f && f.vendor_id === v.id ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
      <label>${t("Materiale")}<input name="material" list="mmats" required value="${esc(f ? f.material : "")}" placeholder="PLA"></label>
      <datalist id="mmats">${MATERIALS.map(m => `<option value="${m}">`).join("")}</datalist>
      <label>${t("Nome")}<input name="name" maxlength="64" value="${esc(f ? f.name : "")}" placeholder="${esc(t("es. Nero opaco"))}"></label></div>
    <div class="row">
      <label>${t("Colore")}<input name="color_hex" type="color" value="${esc(f && f.color_hex ? f.color_hex.slice(0, 7) : "#000000")}"></label>
      <label>${t("Diametro (mm)")}<select name="diameter">${[1.75, 2.85].map(d => `<option ${f && f.diameter === d ? "selected" : ""}>${d}</option>`).join("")}</select></label>
      <label>${t("Peso netto (g)")}<input name="weight" type="number" min="0" step="1" value="${f ? f.weight ?? "" : 1000}"></label>
      <label>${t("Densità (g/cm³)")}<input name="density" type="number" min="0.5" max="3" step="0.01" value="${f ? f.density : ""}"></label></div>
    <div class="row">
      <label>${t("Peso bobina vuota (g)")}<input name="spool_weight" type="number" min="0" step="1" value="${f ? f.spool_weight ?? "" : ""}"></label>
      <label>${t("Prezzo (€)")}<input name="price" type="number" min="0" step="0.01" value="${f ? f.price ?? "" : ""}"></label>
      <label>${t("Ugello (°C)")}<input name="nozzle_temp" type="number" min="0" max="500" value="${f ? f.nozzle_temp ?? "" : ""}"></label>
      <label>${t("Piatto (°C)")}<input name="bed_temp" type="number" min="0" max="200" value="${f ? f.bed_temp ?? "" : ""}"></label></div>
    <label>${t("Codice articolo")}<input name="article_number" maxlength="64" value="${esc(f ? f.article_number : "")}"></label>
    <div class="dlgfoot">${f ? `<button type="button" class="danger" id="fdel">${t("Elimina")}</button><span class="grow"></span>` : ""}<button value="cancel">${t("Annulla")}</button><button class="primary" id="fsave">${t("Salva")}</button></div>`);
  const fm = $("#dlgbody"), v = n => fm.elements.namedItem(n).value;
  fm.elements.namedItem("material").onchange = e => { const d = MAT.densities[e.target.value.trim().toUpperCase()]; if (d && !v("density")) fm.elements.namedItem("density").value = d; };
  $("#fsave").onclick = async e => {
    e.preventDefault();
    const body = {vendor_id: v("vendor_id") ? +v("vendor_id") : null, material: v("material").trim(), name: v("name").trim(), color_hex: v("color_hex"),
                  diameter: +v("diameter"), weight: num(v("weight")), density: num(v("density")), spool_weight: num(v("spool_weight")), price: num(v("price")),
                  nozzle_temp: num(v("nozzle_temp")), bed_temp: num(v("bed_temp")), article_number: v("article_number").trim()};
    if (await act(() => f ? api(`/api/materials/filaments/${f.id}`, {method: "PUT", body}) : api("/api/materials/filaments", {method: "POST", body}), t("Filamento salvato"))) {
      $("#dlg").close(); RENDER.materials();
    }
  };
  if (f) $("#fdel").onclick = async e => {
    e.preventDefault();
    if (await uiConfirm(t("Eliminare il filamento {f}?", {f: filLabel(f)}), {danger: true}))
      if (await act(() => api(`/api/materials/filaments/${f.id}`, {method: "DELETE"}), t("Filamento eliminato"))) { $("#dlg").close(); RENDER.materials(); }
  };
}

// ---------------------------------------------------------------- produttori
function drawVendors(edit) {
  const cnt = id => MAT.filaments.filter(f => f.vendor_id === id).length;
  $("#mbody").innerHTML = `<div class="row mtools"><span class="grow"></span>${edit ? `<button class="primary" id="vadd">${t("Nuovo produttore")}</button>` : ""}</div>
    ${MAT.vendors.length ? `<div class="tablewrap"><table class="mtable"><thead><tr><th>${t("Produttore")}</th><th>${t("Peso bobina vuota")}</th><th>${t("Filamenti")}</th><th>${t("Note")}</th><th></th></tr></thead><tbody>
      ${MAT.vendors.map(v => `<tr data-id="${v.id}"><td><b>${esc(v.name)}</b></td><td>${v.empty_spool_weight != null ? `${v.empty_spool_weight} g` : "—"}</td>
        <td>${cnt(v.id)}</td><td class="muted">${esc(v.comment || "")}</td><td class="macts">${edit ? `<button data-a="edit">${t("Modifica")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${t("Nessun produttore.")}</div>`}`;
  $("#vadd") && ($("#vadd").onclick = () => vendorDialog());
  $("#mbody").onclick = e => { const b = e.target.closest("button[data-a=edit]"); if (b) vendorDialog(MAT.vendors.find(x => x.id === +b.closest("tr").dataset.id)); };
}

function vendorDialog(v) {
  openDialog(`<h3>${v ? esc(v.name) : t("Nuovo produttore")}</h3>
    <label>${t("Nome")}<input name="name" maxlength="64" required value="${esc(v ? v.name : "")}"></label>
    <label>${t("Peso bobina vuota (g)")}<input name="empty_spool_weight" type="number" min="0" step="1" value="${v ? v.empty_spool_weight ?? "" : ""}"></label>
    <label>${t("Note")}<textarea name="comment" rows="2" maxlength="1024">${esc(v ? v.comment : "")}</textarea></label>
    <div class="dlgfoot">${v ? `<button type="button" class="danger" id="vdel">${t("Elimina")}</button><span class="grow"></span>` : ""}<button value="cancel">${t("Annulla")}</button><button class="primary" id="vsave">${t("Salva")}</button></div>`);
  const f = $("#dlgbody"), val = n => f.elements.namedItem(n).value;
  $("#vsave").onclick = async e => {
    e.preventDefault();
    const body = {name: val("name").trim(), empty_spool_weight: num(val("empty_spool_weight")), comment: val("comment").trim()};
    if (await act(() => v ? api(`/api/materials/vendors/${v.id}`, {method: "PUT", body}) : api("/api/materials/vendors", {method: "POST", body}), t("Produttore salvato"))) {
      $("#dlg").close(); RENDER.materials();
    }
  };
  if (v) $("#vdel").onclick = async e => {
    e.preventDefault();
    if (await uiConfirm(t("Eliminare il produttore {v}?", {v: v.name}), {danger: true}))
      if (await act(() => api(`/api/materials/vendors/${v.id}`, {method: "DELETE"}), t("Produttore eliminato"))) { $("#dlg").close(); RENDER.materials(); }
  };
}

// bobine assegnate, nella scheda della stampante (sotto i filamenti)
function spoolsLine(p) {
  if (!(p.spools || []).length) return "";
  return `<div class="mspools">${p.spools.map(s => `<span class="mspool ${s.pct != null && s.pct < 15 ? "low" : ""}" title="${esc(t("Bobina #{n} della Libreria Materiali", {n: s.id}))}">
    ${swatch(s.color)}${s.slot_label ? `<b>${esc(s.slot_label)}</b> ` : ""}${esc([s.material, s.name].filter(Boolean).join(" "))} · ${kg(s.remaining)}</span>`).join("")}</div>`;
}
