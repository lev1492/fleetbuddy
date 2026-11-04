# Changelog

Tutte le modifiche rilevanti a FleetBuddy. Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e la numerazione segue il [Semantic Versioning](https://semver.org/lang/it/).

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
