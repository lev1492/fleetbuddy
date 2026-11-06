# Changelog

Tutte le modifiche rilevanti a FleetBuddy. Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e la numerazione segue il [Semantic Versioning](https://semver.org/lang/it/).

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
