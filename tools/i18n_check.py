#!/usr/bin/env python3
"""Controllo delle traduzioni.

Raccoglie tutte le frasi da tradurre (in italiano) dal codice e verifica che ogni file
static/i18n/<lingua>.json le contenga, con gli stessi segnaposto {nome}.

  python -m tools.i18n_check              # rapporto
  python -m tools.i18n_check --keys       # elenca le frasi trovate
  python -m tools.i18n_check --strict     # esce con errore se manca qualcosa (utile in CI)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.i18n import CATALOG_DIR, LANGS  # noqa: E402

PY_FUNCS = {"_": 0, "tr": 1, "T": 0, "nt": 0, "toast": 0, "qmsg": 0, "sh": 0}
PY_DICTS = {"ERROR_HINTS", "STATUS_IT", "JOB_TOASTS", "JOB_TITLES"}
JS_MAPS = ("STATES", "JOBS", "ROLES", "SPEEDS", "FLOW", "MATERIAL")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _js_unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def js_keys(text: str) -> set[str]:
    keys = {_js_unescape(m) for m in re.findall(r'\bt\(\s*"((?:[^"\\]|\\.)*)"', text)}
    for name in JS_MAPS:
        m = re.search(r"const %s = \{(.*?)\};" % name, text, re.S)
        if m:
            keys |= set(re.findall(r':\s*"((?:[^"\\]|\\.)*)"', m.group(1)))
    return keys


def html_keys(text: str) -> set[str]:
    return set(re.findall(r'data-i18n="([^"]+)"', text))


def py_keys(source: str) -> set[str]:
    keys: set[str] = set()
    tree = ast.parse(source)

    def const(n):
        return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if fname in PY_FUNCS and len(node.args) > PY_FUNCS[fname]:
                v = const(node.args[PY_FUNCS[fname]])
                if v:
                    keys.add(v)
            for kw in node.keywords:
                if kw.arg == "key" and const(kw.value):
                    keys.add(const(kw.value))
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in PY_DICTS for n in names) and isinstance(node.value, ast.Dict):
                keys |= {const(v) for v in node.value.values if const(v)}
            for t in node.targets:   # reason = "..."  oppure  secs, reason = 30, "..."
                if isinstance(t, ast.Name) and t.id == "reason" and const(node.value):
                    keys.add(const(node.value))
                if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for tt, vv in zip(t.elts, node.value.elts):
                        if isinstance(tt, ast.Name) and tt.id == "reason" and const(vv):
                            keys.add(const(vv))
        elif isinstance(node, ast.Dict):   # parametri "*_key" e voci {"msg": "..."}
            for k, v in zip(node.keys, node.values):
                if const(k) and (const(k).endswith("_key") or const(k) == "msg") and const(v):
                    keys.add(const(v))
    return keys


def all_keys() -> set[str]:
    keys: set[str] = set()
    keys |= js_keys((ROOT / "static" / "app.js").read_text(encoding="utf-8"))
    keys |= html_keys((ROOT / "static" / "index.html").read_text(encoding="utf-8"))
    for p in sorted((ROOT / "app").rglob("*.py")):
        if p.name != "i18n.py":
            keys |= py_keys(p.read_text(encoding="utf-8"))
    return {k for k in keys if k.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    keys = all_keys()
    if a.keys:
        print(json.dumps(sorted(keys), ensure_ascii=False, indent=1))
        return
    problems = 0
    print(f"Frasi da tradurre: {len(keys)}")
    for lang in LANGS:
        if lang == "it":
            continue
        path = CATALOG_DIR / f"{lang}.json"
        cat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        missing = sorted(k for k in keys if not cat.get(k))
        unused = sorted(k for k in cat if k not in keys)
        bad = sorted(k for k in keys if cat.get(k) and
                     set(PLACEHOLDER.findall(k)) != set(PLACEHOLDER.findall(cat[k])))
        problems += len(missing) + len(bad)
        print(f"  {lang}: {len(keys) - len(missing)}/{len(keys)} tradotte"
              f"{f', {len(missing)} mancanti' if missing else ''}"
              f"{f', {len(bad)} con segnaposto diversi' if bad else ''}"
              f"{f', {len(unused)} non più usate' if unused else ''}")
        for k in missing[:10]:
            print(f"      manca: {k}")
        for k in bad[:10]:
            print(f"      segnaposto: {k}  →  {cat[k]}")
    if a.strict and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
