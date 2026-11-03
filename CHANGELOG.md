# Changelog

Tutte le modifiche rilevanti a FleetBuddy. Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e la numerazione segue il [Semantic Versioning](https://semver.org/lang/it/).

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
