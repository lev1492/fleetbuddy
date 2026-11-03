#!/usr/bin/env python3
"""Trova i Fleet Hub nella rete locale via SSDP (Technical White Paper v1.0, par. 2.1.4).

  python -m tools.find_hub            # elenca gli Hub trovati
  python -m tools.find_hub --write    # scrive IP e porta del primo Hub in config.toml

Invia la richiesta sia in multicast (239.255.255.250, UDP 1990/1991) sia in broadcast
(255.255.255.255, UDP 2021/2022) per le reti che bloccano il multicast.
Se non trova nulla: il Pi deve essere nella stessa sottorete del Hub; in alternativa
inserisci una chiavetta USB nel Hub e leggi l'IP dal file status.txt creato dopo ~20 s.
"""
import argparse
import re
import socket
import sys
import time
from pathlib import Path

ST = "urn:bambulab-com:v1-hub:server:1"   # identificativo fisso del firmware (dalla demo)
MSG = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
       f"MX: 3\r\nST: {ST}\r\n\r\n").encode()
TARGETS = [("239.255.255.250", 1990), ("239.255.255.250", 1991),
           ("255.255.255.255", 2021), ("255.255.255.255", 2022)]


def parse(text: str, ip: str) -> dict:
    info = {"ip": ip}
    for line in text.split("\r\n"):
        k, sep, v = line.partition(":")
        if sep:
            info[k.strip().lower()] = v.strip()
    return info


def discover(timeout: float = 5) -> list[dict]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    for addr in TARGETS:
        try:
            s.sendto(MSG, addr)
        except OSError as e:
            print(f"  (invio a {addr[0]}:{addr[1]} non riuscito: {e})", file=sys.stderr)
    found: dict[str, dict] = {}
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, (ip, _port) = s.recvfrom(2048)
        except socket.timeout:
            continue
        text = data.decode("utf-8", "ignore")
        if "bambulab" in text.lower() and ip not in found:
            found[ip] = parse(text, ip)
    s.close()
    return list(found.values())


def write_config(ip: str, port: str):
    cfg = Path(__file__).resolve().parent.parent / "config.toml"
    if not cfg.exists():
        sys.exit("config.toml non esiste: copia prima config.example.toml")
    text = cfg.read_text(encoding="utf-8")
    hub = re.search(r"(?ms)^\[hub\]\s*$(.*?)(?=^\[|\Z)", text)
    if not hub:
        sys.exit("Sezione [hub] non trovata in config.toml")
    body = re.sub(r'(?m)^host\s*=.*$', f'host = "{ip}"', hub.group(1), count=1)
    if port.isdigit():
        body = re.sub(r'(?m)^port\s*=.*$', f'port = {port}', body, count=1)
    cfg.write_text(text[:hub.start(1)] + body + text[hub.end(1):], encoding="utf-8")
    print(f"config.toml aggiornato: host = {ip}, port = {port or 'invariata'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="scrive il primo Hub trovato in config.toml")
    ap.add_argument("--timeout", type=float, default=5)
    a = ap.parse_args()
    print("Cerco Fleet Hub nella rete…")
    hubs = discover(a.timeout)
    if not hubs:
        sys.exit("Nessun Hub trovato. Verifica la sottorete oppure leggi l'IP da status.txt su chiavetta USB.")
    for h in hubs:
        state = {"Activate": "attivato", "NoActivate": "da attivare"}.get(h.get("state", ""), h.get("state", "?"))
        print(f"- {h['ip']}:{h.get('port', '8888')}  {h.get('name', 'Fleet Hub')}  SN {h.get('serverid', '?')}  ({state})")
    if a.write:
        write_config(hubs[0]["ip"], hubs[0].get("port", ""))


if __name__ == "__main__":
    main()
