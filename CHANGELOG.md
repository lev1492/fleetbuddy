# Changelog

Tutte le modifiche rilevanti a FleetBuddy. Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e la numerazione segue il [Semantic Versioning](https://semver.org/lang/it/).

## [0.19.5] – 2026-11-11

### Novità
- Storico: se non c'è la foto di fine stampa compare l'anteprima del file (bordo tratteggiato): la miniatura del file
  in archivio, oppure, per le stampe avviate direttamente sulla stampante, l'anteprima che la stampante ricava dal
  G-code, salvata da FleetBuddy all'avvio (Klipper, Duet, OctoPrint, PrusaLink, UltiMaker con Connect…). Un clic la
  ingrandisce, come la foto.

## [0.19.4] – 2026-11-11

### Modifiche
- Il numero accanto a "Coda" conta le stampe da fare, comprese le copie (es. un file con 5 copie e uno con 2 → 7),
  invece del numero di file in coda; passando il mouse si vede anche il numero di file.

## [0.19.3] – 2026-11-10

### Novità
- All'avvio di ogni stampa FleetBuddy registra la durata prevista (dal file, oppure tempo trascorso più tempo
  rimanente comunicato dalla stampante) e il consumo previsto: dal file in archivio, oppure dalla stampante per
  Klipper, OctoPrint, Duet, PrusaLink (metadati o G-code) e UltiMaker (lavoro di Connect). Se all'avvio i dati non
  sono ancora disponibili si riprova per 15 minuti. Lo storico mostra la durata prevista accanto a quella reale.
- Stampante offline oltre il tempo previsto: la stampa diventa "Da confermare" (non più conclusa automaticamente).
  Nello storico e negli ordini di produzione i pulsanti "Riuscita" (il consumo previsto va nelle statistiche) e
  "Fallita" (conta solo il fallimento, senza consumo). Il pezzo di produzione resta in attesa della conferma; se
  fallita torna da rilanciare. Se la stampante torna online e comunica l'esito, vale come conferma. Un esito
  confermato non viene più modificato dai riallineamenti automatici.

### Correzioni
- Al ritorno online, una nuova stampa dello stesso file (es. il pezzo successivo di un ordine) poteva essere
  scambiata per la continuazione della stampa precedente, che veniva riaperta perdendone l'esito: ora la riapertura
  avviene solo se non è appena partita una stampa da FleetBuddy e il tempo trascorso è compatibile.
- Fine stimata più precisa: "0 minuti" rimanenti a fine stampa è considerato meno di un minuto, e in mancanza del
  tempo rimanente si usa la durata prevista registrata all'avvio.

## [0.19.2] – 2026-11-10

### Novità
- Produzione e calendario lavorativo: fuori orario la produzione non avvia pezzi da sola, e una stampante la cui
  stampa finisce fuori orario (es. alle 17:45 o in un giorno festivo) attende la conferma dell'operatore, perché il
  pezzo potrebbe essere ancora sul piatto. Nuova opzione in Amministrazione → Generali → Calendario lavorativo:
  "Consenti l'avvio delle stampe fuori orario su conferma dell'operatore". Attiva: un operatore presente avvia subito
  il pezzo successivo; disattiva: il pezzo parte con l'orario lavorativo, dopo la conferma.
- Pagina Produzione: riquadro "In attesa dell'operatore" con le stampanti pronte e il pulsante "Avvia il prossimo
  pezzo"; nel Gantt l'indicazione "attende OK". Sulle Bambu anche "Piatto liberato" vale come conferma.

### Modifiche
- Statistiche → Qualità della connessione: visibile solo agli amministratori.

## [0.19.1] – 2026-11-10

### Novità
- Telecamere RTSP: il campo "Indirizzo della foto della webcam" accetta anche flussi video rtsp://, per esempio la
  Prusa Buddy3D Camera (rtsp://IP-della-camera/live). FleetBuddy estrae i fotogrammi dal flusso con ffmpeg sul
  Raspberry Pi e li mostra nella scheda come per le altre webcam (i browser non riproducono RTSP). Funziona con
  qualsiasi stampante e qualsiasi telecamera RTSP. La prova di connessione indica se manca ffmpeg o se dal flusso
  non arriva nessun fotogramma.

### Installazione
- Serve il pacchetto di sistema ffmpeg: le nuove installazioni lo installano da sole; su quelle esistenti va
  installato una volta con `sudo apt install -y ffmpeg`.

## [0.19.0] – 2026-11-10

### Novità
- Stampante offline durante una stampa: il lavoro non resta aperto all'infinito. Viene considerato concluso all'ora
  di fine stimata (ultimo contatto + tempo rimanente di allora), con il consumo previsto, ed è segnato "(stima)"
  nello storico; il pezzo di produzione risulta completato. In pausa la fine non si può stimare e il lavoro resta
  aperto.
- Ritorno online:
  - se sta ancora stampando lo stesso lavoro, la chiusura stimata viene annullata e il lavoro riaperto;
  - se sta stampando un altro lavoro, l'inizio di questo viene ricostruito a ritroso dal tempo già trascorso e il
    lavoro precedente si chiude al più tardi a quell'ora: se prima della sua fine stimata (oltre 15 minuti), risulta
    interrotto, con avanzamento e consumo stimati a quell'ora, e il pezzo di produzione torna da rilanciare (anche se
    l'ordine risultava completato);
  - se è ferma e comunica che il lavoro era annullato o fallito, l'esito viene corretto.
- Ricostruzione a ritroso più precisa: il tempo trascorso è quello comunicato dalla stampante (Klipper, OctoPrint,
  Duet, PrusaLink, Repetier, UltiMaker, Raise3D, INTAMSYS), altrimenti è ricavato da avanzamento e tempo rimanente.
- Pianificazione: scorrimento avanti e indietro nel tempo (◀ Adesso ▶), con le stampe già fatte dallo storico
  (riuscite, fallite, annullate, interrotte, concluse per stima).

## [0.18.0] – 2026-11-09

### Novità – connessione più stabile
- Tolleranza alle disconnessioni: una stampante che non risponde resta com'era (ultimo stato noto) e diventa offline
  solo dopo il tempo scelto in Amministrazione → Generali (predefinito 30 secondi, dall'ultima risposta ricevuta).
  Le brevi interruzioni del Wi-Fi non cambiano più schede, coda, produzione e storico. Vale anche per le Bambu se il
  Fleet Hub non risponde (prima restavano ferme sull'ultimo stato).
- Secondo tentativo immediato quando una lettura non riesce.
- Lettura indipendente di ogni stampante diretta: una stampante lenta o che non risponde non ferma più gli
  aggiornamenti di tutte le altre (prima il ciclo aspettava la più lenta, fino a 13 secondi).
- Lettura rallentata per le stampanti offline (10 s, 20 s, 40 s, poi ogni minuto), per non occupare la rete.
- Statistiche → Qualità della connessione (ultime 24 ore): per ogni stampante interruzioni brevi, volte offline,
  tempo offline e segnale Wi-Fi, con una valutazione (stabile, instabile, problematica).

## [0.17.0] – 2026-11-09

### Novità
- Amministrazione → Generali raccoglie ora anche le impostazioni (aggiornamento della camera, lingua delle notifiche)
  e le informazioni (versione, aggiornamenti, utenti, stampanti collegate), prima nella pagina Utenti.
- Statistiche → materiali: accanto al pallino compare il nome del colore più vicino al codice esadecimale (nero,
  grigio antracite, beige, marrone scuro, fucsia…, calcolato nello spazio colore CIELAB), oppure "Generico" se il
  colore non è indicato; il codice esadecimale compare passando il mouse.
- Le conferme e le richieste di testo (eliminazioni, stop di una stampa, rinomina…) sono finestre dell'interfaccia
  al posto degli avvisi del browser, con il pulsante rosso per le azioni distruttive.

## [0.16.2] – 2026-11-09

### Correzioni
- UltiMaker: il consumo dallo storico era circa 6 volte troppo alto (es. 20,3 g invece di 3,18 g). `material_N_amount`
  è un volume in mm³ (lo stesso valore di ;MATERIAL.VOLUME_USED e di material_used), non una lunghezza: ora
  grammi = volume × densità del materiale. Le stampe UltiMaker già registrate vengono ricalcolate una volta all'avvio.
- UltiMaker: per una stampa interrotta lo storico indica la quantità prevista dell'intero lavoro: ora si conta la parte
  stampata, in proporzione al tempo trascorso, ed è segnata come stima (≈) invece che come misura (✓).
- Una stampa interrotta che lascia la stampante in attesa di liberare il piatto (UltiMaker: wait_cleanup con
  result "Aborted") era registrata come riuscita al 100%: ora fa fede l'esito comunicato.
- UltiMaker: durante la preparazione (pre_print) tempo totale e avanzamento sono presi dal lavoro del cluster;
  le ristampe dal display ("Untitled") mostrano il nome della stampa originale; un estrusore vuoto non appare più come
  "empty empty empty"; il consumo previsto delle stampe avviate fuori da FleetBuddy arriva da material_used del
  lavoro; le bobine della Material Station mostrano il residuo in grammi.

### Novità
- Storico: le stampe in corso mostrano i materiali in uso (il peso si registra a fine stampa).

## [0.16.1] – 2026-11-09

### Correzioni
- File UltiMaker .ufp: il materiale ora viene riconosciuto. Il GUID indicato per ogni estrusore nell'intestazione
  del G-code viene cercato tra i profili dei materiali inclusi nel pacchetto (/Materials/*.fdm_material): tipo,
  marca, colore, densità e diametro. Il peso è quello calcolato da Cura (/Cura/slicemetadata.json), altrimenti il
  volume per la densità reale del materiale.
- I G-code già in archivio vengono rianalizzati automaticamente all'avvio quando la lettura dei metadati migliora,
  senza doverli ricaricare.
- Coda: un materiale veniva segnalato "non caricato" sulle stampanti che non hanno bobine AMS o Material Station
  (es. UltiMaker Factor 4). Ora contano anche i materiali negli estrusori e, se la stampante non comunica i materiali,
  non viene mostrato alcun avviso.

## [0.16.0] – 2026-11-09

### Novità
- Archivio: sottocartelle nei progetti, a qualsiasi profondità. Percorso di navigazione (Progetto › Cartella › …),
  riquadri delle sottocartelle con il numero di file, caricamento direttamente nella cartella aperta, "Sposta" con
  l'albero completo, cartelle rinominabili e spostabili. Il nome deve essere unico solo nella stessa cartella.
  Chi ha accesso a un progetto vede tutte le sue sottocartelle; gli operatori possono creare e gestire cartelle nei
  progetti a cui hanno accesso (i progetti principali restano degli amministratori). Eliminando una cartella, file e
  sottocartelle passano a quella superiore.
- UltiMaker: consumo reale di ogni stampa dallo storico della stampante (`/history/print_jobs`): lunghezza estrusa
  per estrusore e materiale (GUID), con marca, tipo, colore, densità e diametro letti dal profilo del materiale
  (`/materials/{guid}`) e convertiti in grammi. Se lo storico viene scritto dopo la fine della stampa, FleetBuddy lo
  recupera nei minuti successivi e sostituisce la stima.

## [0.15.0] – 2026-11-09

### Novità
- Amministrazione → Generali: tema dell'interfaccia (automatico, chiaro, scuro) per tutti gli utenti e calendario
  lavorativo: orari per ogni giorno con una o più pause (es. 9–13 e 14–18), paese e regione o provincia per le
  festività nazionali e locali (oltre 150 paesi), fuso orario con passaggio automatico tra ora legale e solare,
  chiusure programmate (ferie, inventario, ponti) e anteprima dei prossimi giorni di chiusura.
- Produzione con calendario: modalità "nuove stampe solo in orario" (possono finire fuori orario), "stampe interamente
  in orario" o "24/7". Il cambio piatto avviene in orario lavorativo. L'assegnazione non avvia pezzi fuori orario e il
  Gantt ombreggia le ore non lavorative con il nome delle festività e delle chiusure.
- Pianificazione indipendente dallo stato di rete: una stampante offline resta nel Gantt con la stampa in corso
  (stimata dall'ultimo stato noto, segnata "offline") e i pezzi pianificati; al ritorno online FleetBuddy si
  riallinea e, se la stampa è andata persa, rilancia il pezzo.

### Dipendenze
- Nuove librerie: `holidays` (festività) e `tzdata` (fusi orari), installate automaticamente dall'aggiornamento.

## [0.14.0] – 2026-11-07

### Novità
- Produzione: ordini di produzione con uno o più modelli dell'archivio, numero di copie, stampanti abilitate e
  priorità (bassa, normale, alta, urgente). I pezzi vengono assegnati automaticamente alla prima stampante abilitata
  che si libera, nell'ordine priorità → posizione dell'ordine → sequenza; la coda manuale mantiene la precedenza.
- Pianificazione a Gantt: per ogni stampante la stampa in corso (riempita secondo l'avanzamento), la coda manuale,
  i pezzi pianificati e i cambi piatto; saturazione delle stampanti, linea "adesso", fine stimata di ogni ordine,
  zoom (12 ore, 24 ore, 3 giorni, 7 giorni) ed evidenziazione di un ordine. Calcolata dallo stato reale delle
  stampanti con lo stesso criterio dell'assegnazione.
- Pezzi falliti o annullati rilanciati automaticamente in testa al loro ordine (disattivabile per ordine): il piano
  slitta. Un ordine più urgente passa davanti senza interrompere le stampe in corso.
- Ordini scambiabili con ↑ e ↓, priorità modificabile, pausa, ripresa, annullamento; pezzi rilanciabili o saltabili.
- Nuovo ordine con anteprima immediata: pezzi, ore macchina, distribuzione sulle stampanti e fine stimata.
- Tempo di cambio piatto configurabile (predefinito 10 minuti).

## [0.13.1] – 2026-11-07

### Novità
- Nella scheda della stampante, dopo il tempo rimanente e lo strato, compaiono i materiali con cui si sta stampando
  (con il colore): dal file in archivio, dai dati letti dalla stampante (es. Klipper), dalla bobina in uso sulle Bambu
  (`ams.tray_now`) o dai materiali caricati negli estrusori (UltiMaker).

### Correzioni
- Raise3D: "File sulla stampante" dava l'errore "max_num out of the range (1~24)": la cartella si legge ora a pagine
  da 24 elementi, il massimo accettato dall'API.
- Lo strato non viene mostrato quando la stampante non lo comunica (prima appariva "strato 0/0").

## [0.13.0] – 2026-11-07

### Novità
- Consumo di filamento anche per le stampe non avviate dall'archivio, ricavato da ciò che ogni stampante espone:
  - Klipper (Moonraker): consumo reale da `print_stats.filament_used`, convertito in grammi con i metadati del file
    (tipo e colore compresi);
  - INTAMSYS: consumo reale dal calo di peso misurato dalle bilance delle camere filamento;
  - Duet e OctoPrint: dati del G-code in stampa, scaricandone solo inizio e fine (richieste Range); in alternativa
    la lunghezza prevista indicata dalla stampante;
  - PrusaLink: metadati del file in stampa.
  Per le stampe fallite o annullate si registra il consumo reale dove misurato, altrimenti quello previsto in
  proporzione all'avanzamento. Nello storico il consumo misurato è segnato con ✓.
- Amministrazione → Altre stampanti: nuova colonna "Consumo" con la fonte del dato per ogni tipo.
- Barra laterale: numero di stampanti connesse a FleetBuddy (online su totali), con lo stato del Fleet Hub a parte.
- Le schede delle stampanti dirette mostrano l'indirizzo IP al posto dell'identificativo interno.

### Correzioni
- Klipper: strato attuale e totale calcolati anche quando lo slicer non usa `SET_PRINT_STATS_INFO` (es. Elegoo
  OrangeStorm Giga e Neptune): totale dai metadati del file, strato attuale dall'altezza Z, come Mainsail e Fluidd.
- Controllo automatico su GitHub: ogni connettore deve crearsi e avere tutti i metodi dell'interfaccia comune.

## [0.12.2] – 2026-11-07

### Novità
- UltiMaker: la Material Station è mostrata su una sola fila, con l'estrusore di riferimento di ogni bobina (SX per
  `extruder_index` 0, DX per 1) e il print core; gli ugelli sono indicati come SX/DX.
- UltiMaker: riquadro Air Manager (stato, condizione del filtro, percentuale di utilizzo e ore rimanenti) e avvisi da
  UltiMaker Connect: errori noti tradotti (filtro, ventola, Air Manager, temperatura della camera), manutenzione
  richiesta, aggiornamento firmware disponibile, sportello e deumidificatore della Material Station, bobine esaurite.

### Correzioni
- INTAMSYS: accesso con la password in chiaro, come il client web della stampante (il documento indica una formula
  md5 che i firmware in uso non accettano); le varianti cifrate restano come alternativa e quella giusta viene ricordata.
- Pagina Fleet Hub: conta ed elenca solo le Bambu Lab collegate al Hub, spiegando che le altre stampanti sono gestite
  direttamente da FleetBuddy; l'aggiornamento firmware dal Hub propone solo le Bambu. In Informazioni il totale è
  diviso tra Fleet Hub e stampanti dirette.

## [0.12.1] – 2026-11-06

### Correzioni
- UltiMaker S6, S8 e Factor 4: la richiesta di autorizzazione prova tutti i formati accettati dai diversi firmware
  (modulo, multipart, JSON, parametri); se la stampante rifiuta, il messaggio riporta la sua risposta e indica se è
  in modalità di accesso "cloud" (UltiMaker Digital Factory), che non accetta autorizzazioni locali.
- UltiMaker recenti: la prova di connessione non fallisce più se i dati di sistema o UltiMaker Connect richiedono
  l'autorizzazione; lo stato si legge comunque e il resto compare dopo l'autorizzazione. Se è lo stato stesso a
  essere protetto, il messaggio lo dice e riporta i codici di risposta della stampante.
- La prova di connessione mostra la modalità di accesso della stampante ("open" o "cloud").

## [0.12.0] – 2026-11-06

### Novità
- UltiMaker (UM3, S3, S5, S7, S6, S8, Factor 4): un unico collegamento che unisce l'API della stampante (`/api/v1`) e
  UltiMaker Connect quando disponibile. Stato, temperature di ugelli e piano (modificabili), pausa, ripresa, stop,
  invio e avvio di file .gcode e .ufp, camera; con Connect anche materiali caricati per estrusore, Material Station
  (bobine, residuo, umidità), avvisi della stampante e anteprima del lavoro. Autorizzazione richiesta dal modulo e
  approvata sul display della stampante.
- INTAMSYS (INTAMTOUCH Remote Access API, porta 5588): stato, ugelli, piano, camera, camere filamento riscaldate con
  peso del filamento, raffreddamento ad acqua, allarmi; pausa, ripresa, annullamento e avvio di file presenti sulla
  stampante (l'API non permette caricamenti né modifiche di temperatura).
- Archivio: file .ufp (pacchetti di Cura per UltiMaker) e lettura dell'intestazione "Griffin" dei G-code UltiMaker.
- Catalogo: modelli INTAMSYS e UltiMaker aggiornati.

### Correzioni
- Modificando marca o modello di una stampante, la vista delle stampanti si aggiorna subito; prima restava la marca
  precedente (o "Altra marca" per le stampanti aggiunte prima della 0.11.0) finché non cambiavano indirizzo o password.

## [0.11.0] – 2026-11-06

### Novità
- Raise3D (Raise Touch Remote Access API, porta 10800): stato, temperature dei due ugelli e del piano, velocità,
  ventola, pausa, ripresa, stop, camera. L'API non permette di caricare file: dalla scheda si sfoglia la memoria della
  stampante ("File sulla stampante") e si avvia un file già presente.
- Marca e modello delle stampanti dirette, scelti da un catalogo delle principali marche e modelli dei profili di
  OrcaSlicer (o scritti a mano); per le Bambu sono automatici. Mostrati nella scheda e in Amministrazione.
- Vista delle stampanti a schede, elenco o tabella; ricerca per nome, marca, modello o lavoro; ordinamento per nome,
  marca (con raggruppamento), modello, stato o avanzamento. In elenco e tabella un clic apre la scheda completa.
  Le scelte restano memorizzate nel browser.

### Correzioni
- `fleetbuddy-update` aggiorna i tag spostati su GitHub (es. dopo aver corretto una versione pubblicata).
- Il pulsante "Segna il piatto come liberato" compare solo sulle stampanti che hanno quel comando.

## [0.10.0] – 2026-11-06

### Novità
- Elegoo (protocollo SDCP v3, "Elegoo Link"): Centauri Carbon e resine recenti. Ricerca automatica, stato, temperature,
  ventole, avanzamento, pausa, ripresa, stop, invio e avvio dei file con livellamento e timelapse, fotogramma dal video.
- Piani riscaldati multipli (es. Elegoo OrangeStorm Giga con Klipper, Duet con più piani): un riquadro e una
  temperatura regolabile per ciascun piano.
- Senza camera, la scheda mostra l'anteprima della stampa in corso: dalla stampante (Duet, Klipper, PrusaLink)
  oppure dal file in archivio (anche per le Bambu senza camera). Miniature QOI convertite in PNG.
- Opzioni di stampa proposte solo dove esistono: livellamento, calibrazione del flusso e timelapse per le Bambu
  (niente timelapse sulla P1P), livellamento e timelapse per le Elegoo; per le altre valgono le impostazioni del file.
- Duet: dati degli utensili dalle variabili FabbrixOS (diametro e tipo di ugello, per abrasivi, ore di riscaldamento),
  sensori del filamento, utensile attivo, messaggio della scheda, motivo della pausa, potenza del segnale Wi‑Fi.

### Correzioni
- Duet con RepRapFirmware 3.7: temperatura del piano e della camera non mostrate (nuovi campi
  `bedHeaterMapping`/`chamberHeaterMapping`).
- Duet: un sensore di temperatura scollegato o guasto non appare più come 2000 °C ma come errore del sensore, con avviso.

## [0.9.0] – 2026-11-06

### Novità
- Nuovi tipi di stampante collegabili direttamente, senza Fleet Hub: OctoPrint, Klipper (Moonraker), PrusaLink e
  Repetier-Server, oltre a Duet. Per ognuno: stato, temperature, avanzamento, pausa, ripresa, annullamento, invio
  e avvio dei file dall'archivio e coda; temperature e velocità modificabili dove l'API lo consente (non su PrusaLink).
  Webcam letta in automatico da OctoPrint e Moonraker.
- Il modulo "Aggiungi stampante" mostra solo i campi del tipo scelto (chiave API, utente e password, stampante su
  Repetier-Server) con le indicazioni su dove trovarli; tabella delle capacità di ogni tipo.
- Archivio: supporto ai file .bgcode (G-code binario di PrusaSlicer per MK4, XL, MINI, CORE One) con tempo,
  filamenti e miniatura.
- Connettori riorganizzati con un'interfaccia comune: aggiungere un nuovo tipo non tocca il resto del programma.
- `tools/mock_hosts.py`: OctoPrint, Moonraker, PrusaLink e Repetier-Server simulati per lo sviluppo.

### Correzioni
- Stampanti che non comunicano l'esito della stampa: l'esito "completata" o "annullata" tiene conto della velocità
  di avanzamento nell'ultimo intervallo di lettura, per non scambiare una stampa finita per annullata.

## [0.8.0] – 2026-11-05

### Novità
- Stampanti collegate direttamente, senza Fleet Hub: Amministrazione → Altre stampanti. Primo tipo supportato:
  Duet / RepRapFirmware 3 (Duet 2 e Duet 3, sia autonome con API `rr_` sia con SBC con API `/machine`, modalità
  riconosciuta da sola, con password della scheda e prova di connessione). Compaiono nell'elenco insieme alle Bambu,
  con storico, statistiche, coda, gruppi e permessi.
- Sulle stampanti Duet si possono impostare dalla scheda le temperature di ogni utensile, del piano e della camera
  (clic sul riquadro) e il fattore di velocità; pausa, ripresa e annullamento funzionano come per le Bambu.
  Foto da una webcam opzionale (indirizzo di snapshot, es. mjpg-streamer).
- Archivio: accetta anche file .gcode (PrusaSlicer, SuperSlicer, OrcaSlicer, Cura) con lettura di tempo stimato,
  filamenti, colori e miniatura. I .gcode vanno alle stampanti dirette, i .gcode.3mf alle Bambu.
- Il Fleet Hub diventa facoltativo: senza `host` in config.toml (o senza certificati) FleetBuddy funziona con le
  sole stampanti dirette.
- `tools/mock_duet.py`: scheda Duet simulata per lo sviluppo.

### Correzioni
- Un errore imprevisto nell'invio di una voce della coda segna quella voce in errore invece di interrompere la coda.

## [0.7.0] – 2026-11-05

### Novità
- Assegnazione "Automatica" dei filamenti nel dialogo di stampa: si può mettere in coda una stampa per una stampante
  anche se il filamento non è ancora caricato. La stampa resta in coda ("in attesa del filamento") e parte da sola
  appena il filamento viene caricato, assegnato allo slot giusto sull'ugello giusto. Si possono anche combinare slot
  scelti a mano e slot automatici. Se un filamento manca è proposta l'assegnazione automatica e "Stampa ora" è disattivato.
- Coda di stampa: per ogni voce, i filamenti necessari divisi per ugello (SX/DX), con il colore; quelli non caricati
  sulla stampante di destinazione sono evidenziati.
- Archivio: caricando un file diverso con lo stesso nome di uno esistente, il nuovo viene salvato come "1-nome",
  "2-nome" e così via.
- I lati degli ugelli sono indicati come SX/DX in italiano (L/R in inglese e equivalenti nelle altre lingue).

## [0.6.1] – 2026-11-04

### Novità
- Statistiche: un solo selettore del periodo in alto (24 ore, 7, 30, 90 o 365 giorni) che vale per tutta la pagina:
  utilizzo, grafico delle stampe, riepilogo ed esportazioni.
- Grafico delle stampe con barre sottili e asse del tempo: ore per le ultime 24 ore, giorni fino a 90 giorni,
  settimane per l'anno. Ogni barra è divisa in riuscite (verde acqua), non riuscite (rosso) e in corso (blu),
  con il dettaglio passando sopra la barra.

### Correzioni
- Totali, grafico ed esportazioni contano esattamente le stesse stampe: il periodo parte dall'inizio del primo
  giorno (o della prima ora) mostrato nel grafico.

## [0.6.0] – 2026-11-04

### Novità
- Aggiornamento dal browser: in Amministrazione → Informazioni, "Cerca aggiornamenti" controlla subito GitHub e mostra
  le novità; "Installa" avvia l'aggiornamento (con backup e ripristino automatico) e ne segue l'avanzamento fino al
  ricaricamento della pagina. Richiede di eseguire una volta `bash deploy/install.sh` sul Raspberry Pi.

### Correzioni
- Dopo un aggiornamento il browser carica sempre i file nuovi dell'interfaccia (prima poteva usare quelli in cache).
- `fleetbuddy-update` si ferma con un messaggio chiaro se un tag contiene una versione diversa dal suo nome
  (es. tag v0.6.0 creato senza aggiornare `__version__`).

## [0.5.2] – 2026-11-04

### Correzioni
- Coda: non invia più una stampa a una stampante che sta ancora ricevendo quella precedente. Mentre la stampante
  scarica il file resta "inattiva": prima, dopo 45 secondi, la coda le mandava la stampa successiva, che veniva
  ignorata e risultava comunque "inviata".
- Una voce della coda resta visibile come "In partenza" finché la stampante non inizia davvero, poi esce dalla coda
  (la stampa resta nello storico). Se la stampante non parte entro 2 minuti, la voce torna in "Errore" con il motivo
  e si può riprovare; per le copie multiple la copia non partita viene restituita alla voce.
- Anche "Stampa ora" è bloccata mentre la stampante sta ricevendo un'altra stampa.

## [0.5.1] – 2026-11-04

### Correzioni
- "Stampa ora" è disponibile solo se la stampante scelta è pronta (online e inattiva). Se è in stampa, in pausa,
  con il piatto da liberare, offline o sta già ricevendo una stampa, il dialogo lo spiega e propone solo
  "Aggiungi in coda" per quella stampante. Il controllo è fatto anche dal server.

## [0.5.0] – 2026-11-04

### Novità
- Statistiche: utilizzo delle stampanti in tempo reale (in stampa, in pausa, terminate, inattive, totali) e grafico
  del tasso di utilizzo nelle ultime 24 ore, 7 giorni o 30 giorni, con la percentuale per ogni stampante;
  rispetta il filtro per gruppo.
- Dialogo di stampa: anteprima del piatto scelto, con tempo e grammi; scelta dello slot con il colore reale
  della bobina (e il lato L/R sulle doppio estrusore) al posto del codice esadecimale.
- Ventole mostrate come gli altri riquadri, con la percentuale a capo.
- Creazione utente: "Copia credenziali" copia insieme nome utente e password.

### Correzioni
- Comando di stampa: vengono inviate solo le opzioni della famiglia della stampante (P1/A1/X1 oppure H2/P2/X2),
  come indicato dalle API; prima le due serie di opzioni erano inviate insieme.
- Se la stampante non parte entro 2 minuti dall'invio, FleetBuddy lo segnala con il motivo comunicato dal Hub;
  la scheda mostra "comando inviato, in attesa" mentre la stampante scarica il file.
- Il comando inviato e la risposta del Hub sono scritti nel log, per la diagnosi.

## [0.4.0] – 2026-11-04

### Novità
- Nuova scheda della stampante, tutta visibile senza sotto-schede: intestazione con indicatori (connessione, Wi-Fi,
  avvisi, firmware), stato con miniatura della camera (clic per ingrandire), lavoro, avanzamento e prossima stampa
  in coda; riquadri di ugelli, piano, camera e hotend; ventole; comandi; sezione Filamenti con un riquadro per ogni AMS
  (con L/R per l'ugello collegato, umidità e temperatura) e per le bobine esterne, con numero, colore, tipo e residuo.
- Pulsante evidente "Segna il piatto come liberato" a fine stampa.

### Correzioni
- Tipo di hotend: se il singolo ugello non lo comunica si usa il valore generale della stampante (`nozzle_type`),
  la decodifica accetta anche codici con suffisso o in minuscolo, e il codice originale resta visibile accanto alla descrizione.

## [0.3.0] – 2026-11-04

### Novità
- Scheda della stampante con sotto-schede: una per ugello (sinistro/destro sulle doppio estrusore), una per il piano
  e una per la camera quando è misurata. Ogni ugello mostra temperatura, diametro, tipo di hotend (flusso standard,
  alto flusso, TPU alto flusso) e materiale (acciaio inox, acciaio temprato, carburo di tungsteno), decodificati dal
  codice `device.nozzle.info.type` (es. `HH01`), e gli AMS e le bobine esterne collegati a quell'ugello.
- La mappatura automatica dei filamenti sulle doppio estrusore usa gli AMS collegati all'ugello giusto.
- Sotto la camera compare solo l'orario della foto, che cambia soltanto quando arriva una foto nuova.
- Statistiche scaricabili in JSON (tutti i dati) e in CSV (dettaglio stampe, filamenti, per stampante, per giorno);
  Excel resta disponibile. I CSV usano separatore e decimali adatti alla lingua, per aprirsi correttamente in Excel.
- Amministrazione: pulsante per generare una password sicura per i nuovi utenti, da copiare e consegnare.
- Al primo accesso con una password assegnata dall'amministratore, l'utente può sceglierne una propria
  oppure tenere quella ricevuta.

## [0.2.0] – 2026-11-03

### Novità
- Consumo di filamento registrato per ogni stampa, suddiviso per tipo, marca e colore, e visibile nello storico.
  La fonte è il file del 3MF; per le stampe avviate fuori da FleetBuddy si cerca il file in archivio con lo stesso nome
  e, se manca, si stima il consumo dal calo del residuo delle bobine AMS (valori segnati con ≈).
- Statistiche e file Excel con l'elenco dei filamenti consumati per tipo e colore.
- Residuo delle bobine (percentuale e grammi) nelle schede, quando la stampante lo comunica.
- Temperatura della camera per le stampanti che la misurano (H2D e simili), con l'obiettivo impostato;
  non viene più mostrato il valore fittizio delle stampanti senza sensore.
- Barra laterale fissa: resta visibile mentre si scorre l'elenco delle stampanti.
- Dati grezzi di una stampante (menu ⋮, solo amministratori), per verificare quali informazioni invia il Hub.

### Correzioni
- Le stampe avviate da FleetBuddy restano collegate al loro file anche se il Hub segnala l'avvio in ritardo
  o se FleetBuddy viene riavviato nel frattempo: prima in questi casi il consumo di filamento andava perso.
- Lo storico esistente viene ricostruito, una volta sola, per le stampe il cui file è ancora in archivio.

## [0.1.1] – 2026-11-03

### Correzioni
- Il comando `fleetbuddy-update` e il controllo notturno funzionano anche se il repository è stato caricato da Windows
  (permessi di esecuzione persi): vengono avviati tramite bash e i soli cambi di permessi non bloccano più l'aggiornamento.
- Aggiunto `.gitattributes` per mantenere le fine riga Unix negli script; `tools/release.sh` funziona anche da Git Bash su Windows.

## [0.1.0] – 2026-11-03

Prima versione pubblica (anteprima: fino alla 1.0.0 configurazione e funzioni possono ancora cambiare).

### Funzioni
- Cruscotto in tempo reale delle stampanti collegate al Bambu Fleet Hub (stato, avanzamento, temperature, AMS, errori HMS).
- Camera sempre visibile nelle schede, aggiornata a intervallo configurabile senza sfarfallio.
- Comandi pausa / riprendi / interrompi / piatto liberato; modifica degli slot filamento senza RFID.
- Archivio `.gcode.3mf` organizzato in progetti, con miniature, tempi e grammi.
- Avvio stampa con mappatura automatica dei filamenti e riuso della cache MD5 del Hub.
- Coda di stampa verso una stampante, un gruppo o la prima stampante libera.
- Storico di tutte le stampe con foto di fine stampa; statistiche con esportazione in Excel.
- Utenti con ruoli (amministratore, operatore, osservatore), gruppi di stampanti e permessi per stampanti e progetti.
- Notifiche ntfy, Telegram e webhook.
- Multilingua: italiano, inglese, francese, tedesco, spagnolo e portoghese (interfaccia, errori, Excel, notifiche), con lingua per utente.
- Accesso con solo nome utente e password; nome e cognome visibili solo nella gestione utenti.
- Gestione del Hub (in Amministrazione, solo per gli amministratori): ricerca e collegamento stampanti, firmware, NTP, account Web Client.
- Aggiornamento dal repository GitHub con `fleetbuddy-update`: backup, verifica e ripristino automatico; controllo notturno e installazione automatica facoltativa.
- Strumenti: ricerca del Hub via SSDP, attivazione, backup, gestione utenti da riga di comando, Hub simulato per lo sviluppo.

### Sicurezza
- mTLS verso il Hub con TLS ≥ 1.2; sospensione dei tentativi di login dopo un errore per evitare il blocco 1026 del Hub.
- Password utenti con PBKDF2-SHA256, sessioni con cookie HttpOnly, limite ai tentativi di accesso, HTTPS opzionale.
