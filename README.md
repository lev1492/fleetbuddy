# FleetBuddy

**Versione 0.1.0** (anteprima) · [Changelog](CHANGELOG.md)

> Progetto indipendente, non affiliato né approvato da Bambu Lab. "Bambu Lab" e "Fleet Hub" sono marchi dei rispettivi titolari.

Gestione di una flotta di stampanti Bambu Lab dal Raspberry Pi, in stile [Bambuddy](https://github.com/maziggy/bambuddy),
ma passando **esclusivamente dal Bambu Fleet Hub** (HTTP API v1.0.0) invece che dall'MQTT/FTP diretto delle stampanti.

## Cosa fa

| Funzione | Come è realizzata con il Fleet Hub |
|---|---|
| Multilingua | italiano, inglese, francese, tedesco, spagnolo e portoghese: interfaccia, messaggi di errore, file Excel e notifiche; ogni utente sceglie la propria lingua |
| Utenti e permessi | accesso con utente e password; ruoli Amministratore / Operatore / Osservatore; l'admin decide quali stampanti (singole o per gruppo) e quali progetti vede ogni utente |
| Gruppi di stampanti | filtro nel cruscotto e nelle statistiche, coda "sulla prima libera del gruppo", permessi per gruppo |
| Progetti | l'archivio è organizzato in progetti; i file senza progetto sono visibili solo agli admin |
| Cruscotto in tempo reale | polling di `GET /v1/hub/devices` ogni 5 s, inviato al browser via Server-Sent Events |
| Comandi pausa / riprendi / interrompi / piatto liberato | `POST /v1/hub/devices/{sn}/opt`, con verifica dello stato e di `mqtt_status == 2` |
| Camera | sempre visibile nella scheda, aggiornata ogni minuto (configurabile) senza sfarfallio; il Hub fornisce istantanee, non uno stream |
| AMS e bobina esterna | lettura slot, colori, RFID; modifica degli slot senza tag RFID |
| Archivio 3MF | caricamento `.gcode.3mf`, lettura di `slice_info.config`, miniature, tempi, grammi |
| Avvio stampa | auto‑mappatura filamento → slot (tipo, ID Bambu, colore), modificabile a mano; usa la cache MD5 del Hub per non ricaricare il file |
| Coda di stampa | parte da sola sulla prima stampante inattiva con i filamenti giusti; copie multiple; pausa coda |
| Storico | registra **tutte** le stampe viste dal Hub (anche quelle lanciate da Bambu Studio), con foto finale |
| Consumo filamento | per ogni stampa, grammi e metri per tipo, marca e colore: dal file del 3MF quando è in archivio (anche se la stampa è partita da Bambu Studio), altrimenti stimati dal calo del residuo delle bobine AMS (segnati con ≈) |
| Residuo bobine | percentuale e grammi rimasti, per le bobine di cui la stampante li comunica (tipicamente Bambu con RFID) |
| Scheda stampante | tutto in vista: stato con miniatura della camera, prossima stampa in coda, temperature di ugelli, piano e camera, tipo di hotend (diametro, flusso, materiale), ventole, comandi e filamenti per AMS con lato L/R e residuo |
| Statistiche | stampe, tasso di successo, ore, filamento per tipo e colore, per giorno e per stampante; esportazione in JSON, CSV ed Excel |
| Errori HMS | decodifica `attr/code` e descrizione tramite `/v1/hub/hms` e `/v1/hub/device_error`, con cache locale |
| Notifiche | ntfy, Telegram (con foto), webhook generico |
| Gestione Hub | info e spazio, NTP/ora, ricerca SSDP e scansione IP, collega/scollega stampanti, firmware, cache file |
| Attivazione | `tools/activate.py` esegue i 3 passi del Layer 2 |

### Cosa non si può fare tramite il Hub (rispetto a Bambuddy)
Le API v1.0.0 non espongono: **modifica di temperature (ugelli, piano, camera) e velocità**, stream camera continuo, download dei timelapse,
luce, invio di G-code, ventole, scheda SD. FleetBuddy mostra temperature e velocità in sola lettura; per cambiarle
durante una stampa usa il display della stampante o Bambu Studio (che resta compatibile con il Hub). Se in futuro il Hub le aggiungerà, basta estendere `app/hub_client.py`.

## Requisiti
- Raspberry Pi 3 o superiore con **Raspberry Pi OS Bookworm** (Python 3.11). Consumo tipico: ~60–80 MB di RAM.
- Fleet Hub raggiungibile in rete dal Pi sulla porta **TCP 8888** (API mTLS). Per trovarlo con
  `tools/find_hub.py` servono anche le porte UDP 1990/1991 e 2021/2022 (SSDP) nella stessa sottorete.
- Dal Developer Center Bambu: **Activation Key** e **certificato client** (vedi sotto).
- La CA del Hub: copia `cert/root-ca.crt` della demo ufficiale Bambu in `certs/root-ca.crt`
  (non è inclusa nel repository).
- Stampanti con firmware almeno pari al minimo supportato (A1/A1 mini 01.08.00.00; P1P/P1S/X1C 01.09.00.00;
  X1E 01.01.03.12; H2D/H2S/P2S 01.02.00.00; H2D Pro/X2D 01.01.00.00). FleetBuddy segnala quelle da aggiornare.

> **Attenzione:** una stampante collegata al Fleet Hub funziona solo in LAN e non è più raggiungibile da
> Bambu Handy né dal cloud. Resta invece utilizzabile da Bambu Studio sul PC (calibrazioni, filamenti),
> e le stampe avviate da Studio compaiono comunque nello storico di FleetBuddy.

## Certificato client (una volta sola)
Direttamente sul Pi, così la chiave privata non lascia mai il dispositivo:
```bash
cd ~/fleetbuddy/certs
openssl genrsa -out hub_client.key 4096
openssl rsa -in hub_client.key -pubout          # copia l'output (BEGIN PUBLIC KEY …)
chmod 600 hub_client.key
```
Incolla la chiave pubblica nel Developer Center → *Client Certificate Issuance* → **Issue**, scarica il
certificato e salvalo come `certs/hub_client.chain.crt`. Se ne possono emettere fino a 5 (RSA ≥ 2048).

## Sicurezza: cosa sapere prima di attivare
Dal *Security White Paper v1.0* derivano alcune regole pratiche:

- **Il Hub resta legato al certificato usato per l'attivazione.** Anche un altro certificato emesso
  dal Developer Center per lo stesso account verrà rifiutato. Attiva il Hub con il certificato che userà
  FleetBuddy (meglio se creato direttamente sul Pi) e **fai subito un backup** con `bash tools/backup.sh /media/chiavetta`.
  Se perdi chiave o certificato, l'unico rimedio è il ripristino di fabbrica del Hub e una nuova attivazione.
- **Password dell'account API persa = ripristino di fabbrica.** È salvata in `config.toml`, che l'installer
  rende leggibile solo al tuo utente (`chmod 600`), ed è inclusa nel backup.
- **Il Hub non manda nulla all'esterno dopo l'attivazione; FleetBuddy sì, ma solo se lo chiedi.** Le notifiche
  ntfy/Telegram escono su internet (Telegram riceve anche la foto a fine stampa). Per restare tutto in LAN usa
  un server ntfy locale o il webhook verso un servizio interno.
- **I modelli sono in chiaro**, sia nella cache del Hub (la cifratura è annunciata per una versione futura)
  sia nell'archivio di FleetBuddy (`data/archive`). Tieni Hub e Pi in un luogo ad accesso controllato.
- **L'interfaccia di FleetBuddy** va protetta come il resto: imposta `ui_password` e attiva l'HTTPS con
  `bash deploy/make_ui_cert.sh`, altrimenti la password viaggia in chiaro nella LAN.
- **Istantanee della camera:** il Hub conserva solo l'ultima e la aggiorna durante la stampa. FleetBuddy
  salva la foto di fine stampa nello storico, così non viene sovrascritta.
- **Account del Web Client del Hub:** facoltativo; se non lo crei la porta 443 del Hub resta chiusa.
  Si crea da Amministrazione → Fleet Hub, con password diversa da quella API (consigliati ≥ 12 caratteri).
- Le connessioni verso il Hub usano almeno TLS 1.2, come richiesto dal Hub.

## Installazione

Il repository può essere privato: il Pi lo scarica con una **chiave di sola lettura** (deploy key),
senza la tua password GitHub. Sostituisci `UTENTE` con il tuo nome utente GitHub.

1. Sul Pi crea la chiave di accesso:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/fleetbuddy_deploy -N "" -C "fleetbuddy@$(hostname)"
   cat ~/.ssh/fleetbuddy_deploy.pub
   cat >> ~/.ssh/config <<'CFG'
   Host github-fleetbuddy
       HostName github.com
       User git
       IdentityFile ~/.ssh/fleetbuddy_deploy
       IdentitiesOnly yes
   CFG
   ```
2. Su GitHub: repository → **Settings → Deploy keys → Add deploy key**, incolla la chiave pubblica
   e lascia **disattivato** "Allow write access".
3. Scarica e installa l'ultima versione pubblicata:
   ```bash
   git clone git@github-fleetbuddy:UTENTE/fleetbuddy.git ~/fleetbuddy && cd ~/fleetbuddy
   git -c advice.detachedHead=false checkout "$(git tag -l 'v*' --sort=-v:refname | head -n1)"
   bash deploy/install.sh
   ```
4. Copia `cert/root-ca.crt` della demo Bambu in `certs/root-ca.crt`, crea chiave e certificato client
   (sezione sopra), poi:
   ```bash
   .venv/bin/python -m tools.find_hub --write    # trova il Hub e scrive IP/porta in config.toml
   nano config.toml                              # account API, Activation Key, notifiche
   .venv/bin/python -m tools.activate --info     # verifica mTLS e stato del Hub
   .venv/bin/python -m tools.activate            # solo se il Hub non è ancora attivato
   sudo systemctl start fleetbuddy
   ```

Apri `http://<ip-del-pi>:8000`. Log: `journalctl -u fleetbuddy -f`.

Consigli per il Pi 3: lascia `poll_interval = 5` e tieni `data/` su una chiavetta USB se archivi molti 3MF
(puoi puntare `data_dir` a `/mnt/usb/fleetbuddy`).

## Aggiornamenti

```bash
fleetbuddy-update            # controlla, mostra le novità e chiede conferma
fleetbuddy-update --check    # controlla soltanto
```
L'aggiornamento installa l'ultima release (tag `vX.Y.Z`), non il codice in sviluppo. In ordine:
backup di configurazione, certificati e database in `~/fleetbuddy-backups` (tiene gli ultimi 10);
installazione di codice e librerie; riavvio; verifica che FleetBuddy risponda con la nuova versione.
Se non risponde, **torna da solo alla versione precedente**. Configurazione, certificati, database e archivio
non sono nel repository, quindi non vengono mai toccati.

Ogni notte un timer controlla se c'è una nuova versione e la segnala agli amministratori
(pallino su *Amministrazione* e riquadro *Informazioni*). Per installarla anche in automatico:
```toml
[update]
auto_install = true
```
Una versione che non è riuscita a partire non viene ritentata ogni notte: solo con `fleetbuddy-update`.
Stato del timer: `systemctl list-timers fleetbuddy-update.timer`.

### Passare all'installazione da GitHub (installazioni fatte con lo zip)
Dopo aver preparato la deploy key (punti 1 e 2):
```bash
cd ~/fleetbuddy
git init -q -b main
git remote add origin git@github-fleetbuddy:UTENTE/fleetbuddy.git
git fetch --tags origin
git reset -q --hard origin/main      # i file del programma diventano quelli del repository;
                                     # config.toml, certs/, data/ e .venv/ restano dove sono
git -c advice.detachedHead=false checkout "$(git tag -l 'v*' --sort=-v:refname | head -n1)"
bash deploy/install.sh               # aggiunge il comando fleetbuddy-update e il timer notturno
sudo systemctl restart fleetbuddy
```

## Utenti
- Al primo avvio viene creato l'amministratore indicato in `ui_user`/`ui_password`; se la password è vuota,
  il browser chiede di crearlo.
- Gli altri utenti si creano da **Amministrazione**; con **Permessi** si scelgono gruppi, stampanti e progetti visibili.
- Per accedere servono solo nome utente e password. Nome e cognome sono facoltativi e compaiono solo nella
  gestione utenti dell'amministratore; nel resto dell'interfaccia e nello storico si vede il nome utente.
- Il pulsante **Genera password** crea una password sicura da consegnare al nuovo utente; al primo accesso
  FleetBuddy gli propone di sceglierne una propria (facoltativo).
- Password admin dimenticata: `.venv/bin/python -m tools.users reset-password admin`.
- Le password sono salvate con PBKDF2-SHA256; le sessioni durano 30 giorni; dopo 5 tentativi errati
  l'accesso da quell'indirizzo è sospeso per 5 minuti.

## Lingue
- La lingua si sceglie nella pagina di accesso o in basso a sinistra; viene salvata nel profilo dell'utente e
  lo segue su ogni dispositivo. Le notifiche (ntfy, Telegram, webhook) usano la lingua impostata in Amministrazione.
- Il testo di riferimento è l'italiano, scritto direttamente nel codice; le traduzioni stanno in
  `static/i18n/<lingua>.json` (`"frase italiana": "traduzione"`) e servono sia al browser sia al server.
- Dopo aver modificato o aggiunto frasi: `python -m tools.i18n_check` elenca quelle mancanti e quelle
  con segnaposto (`{nome}`) diversi dall'originale. Con `--strict` fallisce, utile in una GitHub Action.
- Per aggiungere una lingua: crea `static/i18n/xx.json`, aggiungila a `LANGS` in `app/i18n.py` e in `static/app.js`.
- Una frase uguale in italiano ma diversa in altre lingue usa un contesto dopo `|` (es. `Piatto|letto` per
  la temperatura del piano, distinto da `Piatto` della piastra di stampa); in italiano il contesto non viene mostrato.

## Note tecniche emerse confrontando PDF e script demo
1. **Hash della password al login.** Il PDF indica `sha256(hex(sha256(hex(sha256(pwd)) + salt)) + challenge)`,
   mentre `base_lib/login.py` usa `sha256(hex(sha256(pwd + salt)) + challenge)`. FleetBuddy usa la versione della demo
   e, se il Hub risponde 1023, riprova con l'altra e lo segnala nel log (`password_hash_mode`).
2. **Porta e SNI.** Il Technical White Paper conferma la porta `8888` per le API (la `443` è solo il Web Client
   del Hub, attivo dopo aver creato un account web). La demo forza il nome TLS `fleet_v1.bambulab.com`.
   Il token JWT ha vita breve: FleetBuddy rifà il login da solo quando riceve 401.
3. **Stampa.** Il codice d'errore 1004 ("is not one plate gcode 3mf") suggerisce che il Hub accetti solo `.gcode.3mf`
   a piatto singolo: esporta con "Esporta G-code del piatto" dallo slicer. Il piatto multiplo è comunque gestito.
4. **Dopo ogni stampa** il Hub richiede `bed_clean` (altrimenti 1051 "device busy"): è il pulsante
   "Piatto liberato", ed è anche ciò che sblocca la coda per quella stampante.
5. **Doppio estrusore (H2D).** L'auto‑mappatura rispetta il lato della bobina esterna (254 = sx, 255 = dx), ma il
   collegamento AMS↔estrusore non è esposto dall'API: controlla la mappatura proposta prima di stampare.

## Struttura
```
app/hub_client.py   client mTLS + login ticket/token + tutte le API del PDF
app/service.py      polling, storico, coda, notifiche, eventi SSE
app/printers.py     normalizzazione stato, decodifica HMS/AMS, auto-mappatura
app/threemf.py      lettura .gcode.3mf
app/main.py         API REST, permessi e interfaccia web (FastAPI)
app/auth.py         utenti, sessioni, ruoli
app/i18n.py         traduzioni lato server
app/export.py       esportazione Excel
static/i18n/        file delle traduzioni
static/             interfaccia (HTML/CSS/JS senza build)
tools/find_hub.py   ricerca del Hub via SSDP (multicast e broadcast)
tools/activate.py   attivazione Hub
tools/i18n_check.py controllo delle traduzioni mancanti
tools/users.py      gestione utenti da riga di comando (es. reset password admin)
tools/update.sh     aggiornamento dal repository (comando fleetbuddy-update)
tools/release.sh    pubblicazione di una nuova versione
tools/backup.sh     backup di certificati, configurazione e database
deploy/make_ui_cert.sh  certificato HTTPS per l'interfaccia
tools/mock_hub.py   Hub simulato con mTLS per sviluppare senza hardware
```

## Se il Hub non si trova
- Il Pi deve stare nella stessa sottorete; nel router il Hub appare come `Bambu-Fleet Hub-XXXXX`
  (ultime 5 cifre del seriale) con MAC `5C:F8:38:31:xx:xx`.
- Oppure inserisci una chiavetta USB nel Hub: dopo ~20 s compare `status.txt` con l'IP.
- Per un IP statico: file `set_net_ip` sulla chiavetta (vedi par. 2.1.2 del Technical White Paper).
- LED NET rosso fisso = DHCP fallito; LED SYS verde lampeggiante lento = Hub da attivare.

## Sviluppo senza Hub
```bash
bash tools/make_mock_certs.sh
python -m tools.mock_hub &        # finto Hub su :8888 con 2 stampanti (P1S, H2D)
# in config.toml: host="127.0.0.1", certificati in certs/mock/, password="Mock1234!"
python -m app            # oppure: uvicorn app.main:app --reload
```

## Versioni e pubblicazione
- La versione è definita in un solo punto: `app/__init__.py` (`__version__`), ed è mostrata nell'interfaccia,
  nel log all'avvio e nei file Excel esportati. Imposta lì anche `REPO_URL` per mostrare il link al repository.
- Numerazione [SemVer](https://semver.org/lang/it/). Nella fase 0.x: correzioni → 0.1.1, nuove funzioni o modifiche
  incompatibili → 0.2.0. Quando funzioni e configurazione saranno stabili si passerà alla 1.0.0.
- Per pubblicare una versione (dal PC di sviluppo): aggiungi in `CHANGELOG.md` la sezione `## [0.1.1] – data`,
  fai il commit e lancia `tools/release.sh 0.1.1`. Lo script controlla che tutto sia salvato e le traduzioni complete,
  aggiorna la versione, crea il tag `v0.1.1` e lo invia a GitHub: i Pi lo vedranno al controllo successivo.
- Il ramo `main` può contenere lavoro in corso: i Pi installano solo i tag di release.
- Ad ogni invio su GitHub, `.github/workflows/checks.yml` verifica codice, traduzioni e che nel repository
  non siano finiti `config.toml`, chiavi, certificati o database.

## Possibili sviluppi
Inventario bobine (stile Spoolman) con scalo automatico dei grammi, gestione permessi multiutente,
stampa da URL esterno (`external_url_print`, già nel client), backup automatico del database, esportazione CSV dello storico.
