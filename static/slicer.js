"use strict";
// Collega slicer: chiavi API personali, cartella predefinita, indirizzo della destinazione e istruzioni per ogni slicer.
// Lo slicer vede Fabbrix Farm Manager come una stampante OctoPrint (app/octoprint.py).

const SLICER_NAMES = {prusaslicer: "PrusaSlicer", superslicer: "SuperSlicer", orcaslicer: "OrcaSlicer", bambustudio: "Bambu Studio",
  ideamaker: "ideaMaker", cura: "Cura", slicer: "Slicer"};
// i testi sono chiamate a t() con la frase scritta per intero: così tools/i18n_check.py li trova
const slicerGuide = k => ({
  prusaslicer: [t("In Impostazioni stampante, accanto al profilo della stampante, premi l'icona «Aggiungi stampante fisica»."),
    t("Tipo di host: OctoPrint. In «Nome host, IP o URL» incolla l'indirizzo qui sopra e in «Chiave API» la tua chiave."),
    t("Premi «Test»: deve rispondere che la connessione funziona. Poi OK."),
    t("Dopo lo slicing premi «Invia alla stampante». Scrivi la cartella davanti al nome (es. Progetto/pezzo.gcode) oppure lascia solo il nome per usare la cartella predefinita."),
    t("«Carica» mette il file nei Files; «Carica e stampa» crea anche un ordine di produzione sulle stampanti compatibili della destinazione.")],
  orcaslicer: [t("Accanto al profilo della stampante premi l'icona di connessione (Wi-Fi) per aprire la stampante fisica."),
    t("Tipo di host: OctoPrint. In «Nome host, IP o URL» incolla l'indirizzo qui sopra e in «Chiave API» la tua chiave."),
    t("Premi «Test»: deve rispondere che la connessione funziona. Poi OK."),
    t("Dopo lo slicing usa «Stampa piatto» / «Invia»: scrivi la cartella davanti al nome (es. Progetto/pezzo.gcode) oppure lascia solo il nome per usare la cartella predefinita."),
    t("«Carica» mette il file nei Files; «Carica e stampa» crea anche un ordine di produzione sulle stampanti compatibili della destinazione.")],
  orcabambu: [t("Con un profilo Bambu Lab apri Impostazioni stampante → Base e attiva «Usa host di stampa di terze parti»."),
    t("Premi l'icona di connessione accanto al profilo: tipo di host OctoPrint, l'indirizzo qui sopra e la tua chiave API, poi «Test» e OK."),
    t("Dopo lo slicing usa «Stampa piatto»: il G-code viene impacchettato in .gcode.3mf e si stampa dal Fleet Hub come i file di Bambu Studio."),
    t("«Carica» mette il file nei Files; «Carica e stampa» crea anche un ordine di produzione sulle stampanti compatibili della destinazione.")],
  curaplugin: [t("Scarica il plugin per Cura qui sopra e trascina il file .curapackage nella finestra di Cura, poi riavvia Cura."),
    t("Dopo lo slicing apri il menu accanto a «Salva su disco» e scegli «Invia al Farm Manager»."),
    t("La prima volta inserisci l'indirizzo del Farm Manager e premi «Collega»: approva nel browser il codice mostrato da Cura."),
    t("Poi scegli cartella, destinazione, azione, copie, priorità e note e premi «Invia». Funziona con tutte le stampanti, UltiMaker comprese (ricevono un file .ufp).")],
  orcaplugin: [t("Serve OrcaSlicer 2.5 o successivo. Scarica il plugin per OrcaSlicer qui sopra."),
    t("In OrcaSlicer: File → Plugin → freccia accanto a «Sfoglia plugin» → «Installa plugin locale», scegli il file e spunta «Attiva»."),
    t("Esporta il G-code o il file del piatto: compare l'avviso «Invia al Farm Manager». In alternativa premi la barra spaziatrice e scegli «Invia a Fabbrix Farm Manager»."),
    t("La prima volta inserisci l'indirizzo del Farm Manager e approva nel browser il codice mostrato da OrcaSlicer; OrcaSlicer chiede una volta il permesso di leggere il file e di collegarsi in rete.")],
  ideamaker: [t("In ideaMaker aggiungi un server OctoPrint dalle impostazioni della stampante (Stampanti → Gestisci stampanti → OctoPrint)."),
    t("Indica l'indirizzo qui sopra e la tua chiave API."),
    t("Dopo lo slicing scegli «Carica su OctoPrint»: il file va nella cartella predefinita."),
    t("Se ideaMaker non accetta un indirizzo con il percorso /octoprint/, segnalacelo: è uno dei casi ancora da verificare.")],
  bambustudio: [t("Bambu Studio non può inviare file ad altri server."),
    t("Esporta il piatto con «Esporta file del piatto sezionato» (.gcode.3mf) e trascinalo nella pagina Files, oppure usa OrcaSlicer con «Usa host di stampa di terze parti».")],
})[k] || [];
const slicerChoices = () => [["curaplugin", t("Cura – plugin Fabbrix")], ["orcaplugin", t("OrcaSlicer 2.5+ – plugin Fabbrix")],
  ["prusaslicer", "PrusaSlicer"], ["orcaslicer", t("OrcaSlicer – come stampante OctoPrint")],
  ["orcabambu", t("OrcaSlicer – stampanti Bambu Lab")], ["ideamaker", "ideaMaker"], ["bambustudio", "Bambu Studio"]];
let SLICER_STATE = {slicer: localStorage.getItem("fb_slicer") || "curaplugin", dest: "", newKey: null};
if (!slicerGuide(SLICER_STATE.slicer).length) SLICER_STATE.slicer = "curaplugin";

function slicerUrl(dest) {
  const base = location.origin + "/octoprint/";
  if (!dest) return base;
  const [k, v] = dest.split(":");
  return `${base}${k}/${encodeURIComponent(v)}/`;
}

async function openSlicerDialog() {
  let d;
  try { d = await api("/api/me/slicer"); } catch (e) { return toast(e.message, "error"); }
  const st = SLICER_STATE;
  const dests = [["", t("Tutte le stampanti che vedi")],
    ...d.groups.map(g => [`g:${g.id}`, t("Gruppo {g}", {g: g.name})]),
    ...d.printers.map(p => [`p:${p.sn}`, t("Stampante {p}", {p: p.name})])];
  if (!dests.some(([v]) => v === st.dest)) st.dest = "";
  const url = slicerUrl(st.dest);
  const projs = PROJECTS.filter(p => p.id !== null);
  const keyRows = d.keys.map(k => `<tr><td>${esc(k.name)}${isAdmin() && k.user_id !== ME.id ? ` <span class="muted">· ${esc(k.username || "?")}</span>` : ""}</td>
      <td><code>${esc(k.prefix)}…</code></td><td>${fmtDate(k.created_at)}</td>
      <td>${k.last_used_at ? `${fmtDate(k.last_used_at)}${k.last_agent ? ` · ${esc(k.last_agent)}` : ""}` : `<span class="muted">${t("mai")}</span>`}</td>
      <td><button type="button" class="danger small" data-delkey="${k.id}">${t("Elimina")}</button></td></tr>`).join("");
  const guide = slicerGuide(st.slicer);
  const reopen = $("#dlg").open && $("#dlgbody").dataset.dlg === "slicer";
  const html = `<h3>${t("Collega slicer")}</h3>
    <p class="sub" style="margin:0">${t("Cura e OrcaSlicer hanno un plugin Fabbrix che si collega con un codice e fa scegliere tutto al momento dell'invio. PrusaSlicer, ideaMaker e anche OrcaSlicer possono inviare come a una stampante OctoPrint, con una chiave API.")}</p>
    ${!d.can_upload ? `<p class="warn small">${t("Il tuo ruolo non consente di caricare file: chiedi all'amministratore.")}</p>` : `
    <h4>${t("Plugin per Cura e OrcaSlicer")}</h4>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <a class="btn" href="/api/slicer/plugin/FabbrixFarmManager.curapackage" download>${t("Scarica il plugin per Cura")}</a>
      <a class="btn" href="/api/slicer/plugin/fabbrix_farm_manager.py" download>${t("Scarica il plugin per OrcaSlicer")}</a></div>
    <div style="display:flex;gap:8px"><input id="paircode" placeholder="${esc(t("Codice mostrato dal plugin, es. K7M2QX"))}" style="flex:1;text-transform:uppercase" maxlength="8">
      <button type="button" id="pairbtn">${t("Approva il codice")}</button></div>
    <h4>${t("1. La tua chiave API")}</h4>
    ${st.newKey ? `<div class="box" style="padding:10px"><p class="small"><b>${t("Copia la chiave adesso: non verrà più mostrata.")}</b></p>
      <div class="row" style="display:flex;gap:8px"><input readonly id="newkey" value="${esc(st.newKey)}" style="flex:1;font-family:monospace">
      <button type="button" id="copykey">${t("Copia")}</button></div></div>` : ""}
    ${d.keys.length ? `<div class="tablewrap"><table><thead><tr><th>${t("Nome")}</th><th>${t("Chiave")}</th><th>${t("Creata")}</th><th>${t("Ultimo uso")}</th><th></th></tr></thead><tbody>${keyRows}</tbody></table></div>`
      : `<p class="small muted">${t("Non hai ancora chiavi.")}</p>`}
    <div style="display:flex;gap:8px"><input id="keyname" placeholder="${esc(t("Nome della chiave, es. PC ufficio – PrusaSlicer"))}" style="flex:1" maxlength="60">
      <button type="button" class="primary" id="newkeybtn">${t("Crea chiave")}</button></div>

    <h4>${t("2. Cartella predefinita")}</h4>
    <label>${t("Dove vanno i file inviati senza cartella nel nome")}<select id="defproj">
      <option value="">${t("Nessuna: la cartella va indicata nel nome")}</option>
      ${projs.map(p => `<option value="${p.id}" ${p.id === d.default_project ? "selected" : ""}>${esc(projPath(p))}</option>`).join("")}</select></label>

    <h4>${t("3. Indirizzo da inserire nello slicer")}</h4>
    <label>${t("Destinazione di «Carica e stampa»")}<select id="sldest">${dests.map(([v, l]) => `<option value="${esc(v)}" ${v === st.dest ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
    <div style="display:flex;gap:8px"><input readonly id="slurl" value="${esc(url)}" style="flex:1;font-family:monospace"><button type="button" id="copyurl">${t("Copia")}</button></div>
    ${d.can_order ? "" : `<p class="small muted">${t("Il tuo ruolo non crea ordini di produzione: usa solo «Carica».")}</p>`}
    <p class="small muted">${t("Per ogni destinazione crea una stampante fisica diversa nello slicer.")}</p>

    <h4>${t("4. Istruzioni")}</h4>
    <label>${t("Slicer")}<select id="slsel">${slicerChoices().map(([v, l]) => `<option value="${v}" ${v === st.slicer ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
    <ol class="small">${guide.map(g => `<li>${esc(g)}</li>`).join("")}</ol>
    ${location.protocol === "https:" ? `<p class="small muted">${t("Con l'HTTPS e un certificato autofirmato lo slicer deve fidarsi del certificato: su Windows importalo tra le «Autorità di certificazione radice attendibili», su Linux e macOS indicalo nel campo del certificato (CA) dello slicer.")}</p>` : ""}`}
    <div class="dlgfoot"><button type="button" value="cancel">${t("Chiudi")}</button></div>`;
  if (reopen) $("#dlgbody").innerHTML = html;
  else {
    openDialog(html);
    $("#dlg").addEventListener("close", () => { st.newKey = null; delete $("#dlgbody").dataset.dlg; }, {once: true});
  }
  $("#dlgbody").dataset.dlg = "slicer";
  const redraw = () => openSlicerDialog();
  const on = (sel, ev, fn) => { const el = $(sel); if (el) el[ev] = fn; };
  on("#copykey", "onclick", () => copyText($("#newkey")));
  on("#pairbtn", "onclick", () => { const c = $("#paircode").value.trim(); if (c) openPairDialog(c); });
  on("#copyurl", "onclick", () => copyText($("#slurl")));
  on("#newkeybtn", "onclick", async () => {
    const r = await act(() => api("/api/me/apikeys", {method: "POST", body: {name: $("#keyname").value}}));
    if (r) { st.newKey = r.key; redraw(); }
  });
  $$("[data-delkey]").forEach(b => b.onclick = async () => {
    if (!confirm(t("Eliminare la chiave? Gli slicer che la usano non potranno più inviare file."))) return;
    if (await act(() => api(`/api/apikeys/${b.dataset.delkey}`, {method: "DELETE"}), t("Chiave eliminata"))) { st.newKey = null; redraw(); }
  });
  on("#defproj", "onchange", e => act(() => api("/api/me", {method: "PATCH", body: {default_project: e.target.value || null}}), t("Cartella predefinita salvata")));
  on("#sldest", "onchange", e => { st.dest = e.target.value; redraw(); });
  on("#slsel", "onchange", e => { st.slicer = e.target.value; localStorage.setItem("fb_slicer", st.slicer); redraw(); });
}

async function copyText(input) {
  try { await navigator.clipboard.writeText(input.value); toast(t("Copiato"), "ok"); }
  catch { input.select(); toast(t("Selezionata: copiala con Ctrl+C"), "info"); }
}

// Abbinamento di un plugin (Cura, OrcaSlicer): il plugin apre /#pair=CODICE, oppure il codice si scrive in "Collega slicer"
async function openPairDialog(code) {
  let p;
  try { p = await api(`/api/slicer/pair/${encodeURIComponent(code)}`); } catch (e) { return toast(e.message, "error"); }
  openDialog(`<h3>${t("Collegare il plugin?")}</h3>
    <p>${t("{app} su {device} chiede di inviare file a Fabbrix Farm Manager a nome tuo ({user}).", {app: `<b>${esc(p.app)}</b>`, device: `<b>${esc(p.device || "?")}</b>`, user: esc(ME.username)})}</p>
    <p class="small muted">${t("Codice")}: <code>${esc(p.code)}</code> · ${t("Indirizzo")}: ${esc(p.ip)} · ${fmtDate(p.created)}</p>
    <p class="small muted">${t("Approva solo se il codice è quello mostrato dal tuo slicer. La chiave creata compare in «Collega slicer» e si può revocare.")}</p>
    <div class="dlgfoot"><button type="button" id="pairno">${t("Rifiuta")}</button><button type="button" class="primary" id="pairyes">${t("Approva")}</button></div>`);
  $("#pairno").onclick = async () => { await act(() => api(`/api/slicer/pair/${encodeURIComponent(p.code)}`, {method: "POST", body: {approve: false}})); $("#dlg").close(); };
  $("#pairyes").onclick = async () => {
    if (await act(() => api(`/api/slicer/pair/${encodeURIComponent(p.code)}`, {method: "POST", body: {approve: true}}),
        t("Plugin collegato: torna allo slicer"))) $("#dlg").close();
  };
}
function checkPairHash() {
  const m = /^#pair=([A-Za-z0-9-]+)/.exec(location.hash);
  if (!m || !ME || !slicerAllowed()) return false;
  history.replaceState(null, "", location.pathname + location.search + "#printers");
  openPairDialog(m[1]);
  return true;
}

$("#meslicer").onclick = () => openSlicerDialog();
// "Collega slicer": per ora solo amministratori (Generali → Files → "Invio dagli slicer solo per gli amministratori")
const slicerAllowed = () => !!ME && (isAdmin() || (S.settings && S.settings.slicer_admin_only === false && can("files")));
setInterval(() => { const b = $("#meslicer"); if (b) b.hidden = !slicerAllowed(); }, 1000);
