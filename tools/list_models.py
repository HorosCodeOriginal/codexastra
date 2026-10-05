#!/usr/bin/env python3
"""tools/list_models.py - ALLE Modelle des lokalen Gateways auflisten.

Warum es mehrere Quellen gibt (verifiziert 2026-10-06 an FlagshipRouter, Port 20128):

  /v1/models           ->  ~4 Modelle. Kredential-gefiltert: nur Provider mit
                           aktiver Verbindung. Das ist die Liste, die Codex bzw.
                           CodexAstra wirklich benutzen kann.
  /api/models/catalog  -> ~334 Modelle. Der Katalog des Dashboards ("Models"-
                           Screen): alle Modelle der kostenlosen Provider laut
                           brand.json-Policy, inkl. oeffentlicher Renames.
  /api/models          -> ~1103 Modelle. Die komplette eingebaute Tabelle
                           (AI_MODELS aus providerModels.js) + Custom-Modelle,
                           OHNE Credential-Filter. Deshalb zeigt das Dashboard
                           "alle" Modelle - und genau deshalb ist die Liste auch
                           groesser als das, was tatsaechlich nutzbar ist.

Die beiden /api-Endpunkte liegen hinter ``dashboardGuard`` und antworten ohne
Session mit 401. Lokal akzeptieren sie stattdessen den CLI-Token im Header
``x-fr-cli-token``. Der ist berechenbar (kein Browser-Login noetig) aus

    sha256(<DATA_DIR>/machine-id + "fr-cli-auth" + <DATA_DIR>/auth/cli-secret)[:16]

DATA_DIR ist unter Windows ``%APPDATA%\\flagshiprouter`` (Override: Umgebungs-
variablen FLAGSHIPROUTER_DATA_DIR oder DATA_DIR).

Beispiele::

    python tools/list_models.py                       # Uebersicht aller Quellen
    python tools/list_models.py --source v1           # nutzbare Modelle (Codex)
    python tools/list_models.py --source catalog      # Dashboard-Katalog
    python tools/list_models.py --source all --kind llm --grep claude
    python tools/list_models.py --source catalog --json > models.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_PATH = ROOT / "config.json"

# Gemeinsame Katalog-Logik (eine Quelle der Wahrheit) nutzen.
sys.path.insert(0, str(ROOT))
from core import catalog as cat  # noqa: E402

DEFAULT_GATEWAY = "http://127.0.0.1:20128/v1"


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def fetch_v1(base: str, token: str) -> list[str]:
    """Nutzbare Modelle: /v1/models (Bearer-Token des Gateways)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = cat.get_json(f"{base}/models", headers)
    return sorted(
        str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")
    )


def fetch_catalog_entries(root: str, cli: str) -> list[dict]:
    """Dashboard-Katalog der freien Provider, normalisiert."""
    return cat.catalog_entries(cat.fetch_catalog(root, cli))


def fetch_all_entries(root: str, cli: str) -> list[dict]:
    """Komplette eingebaute Tabelle + Custom-Modelle, normalisiert."""
    return cat.all_entries(cat.fetch_all(root, cli))


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------
def _flt(text: str, needle: str | None) -> bool:
    return not needle or needle.lower() in text.lower()


def _kind_of(entry: dict) -> str:
    return str(entry.get("kind") or "llm")


def print_v1(models: list[str], grep: str | None) -> None:
    shown = [m for m in models if _flt(m, grep)]
    print(f"\n=== /v1/models  ({len(shown)}/{len(models)}) - nutzbar fuer Codex ===")
    for mid in shown:
        print(f"  {mid}")


def print_catalog(entries: list[dict], grep: str | None, kind: str | None, ready_only: bool) -> None:
    rows = []
    for e in entries:
        if kind and _kind_of(e) != kind:
            continue
        if ready_only and not e.get("ready"):
            continue
        if not _flt(f"{e['id']} {e.get('name')} {e.get('provider')}", grep):
            continue
        rows.append(e)

    print(f"\n=== /api/models/catalog  ({len(rows)}/{len(entries)}) - Dashboard-Katalog ===")
    by_provider: dict[str, list[dict]] = {}
    for e in rows:
        by_provider.setdefault(e.get("provider") or "?", []).append(e)
    for pid in sorted(by_provider, key=str.lower):
        group = by_provider[pid]
        pname = next((x.get("provider_name") for x in group if x.get("provider_name")), pid)
        ready = "verbunden" if any(x.get("ready") for x in group) else "-"
        print(f"\n  [{pid}] {pname}  ({ready})")
        for e in sorted(group, key=lambda x: x["id"].lower()):
            caps = e.get("caps") or {}
            tags = []
            if _kind_of(e) != "llm":
                tags.append(_kind_of(e))
            if caps.get("vision"):
                tags.append("vision")
            if caps.get("reasoning"):
                tags.append("reasoning")
            if caps.get("tools") is False:
                tags.append("no-tools")
            ctx = caps.get("contextWindow")
            tag = ("  " + " ".join(tags)) if tags else ""
            print(f"    - {e['id']}{tag}{f'  ctx={ctx}' if ctx else ''}")


def print_all(entries: list[dict], grep: str | None) -> None:
    by_provider: dict[str, list[str]] = {}
    shown = 0
    for e in entries:
        if not _flt(e["id"], grep):
            continue
        shown += 1
        by_provider.setdefault(e.get("provider") or "?", []).append(e["id"])

    print(f"\n=== /api/models  ({shown}/{len(entries)}) - komplette statische Tabelle ===")
    for pid in sorted(by_provider, key=str.lower):
        ids = sorted(set(by_provider[pid]), key=str.lower)
        print(f"\n  [{pid}]  ({len(ids)})")
        for mid in ids:
            print(f"    - {mid}")


def print_summary(base: str, root: str, token: str, cli: str | None) -> None:
    print("Gateway :", base)
    print("Dashboard:", root)
    if not cli:
        print("CLI-Token: NICHT ableitbar -", cat.data_dir(),
              "unvollstaendig (machine-id / auth/cli-secret)")
    try:
        v1 = fetch_v1(base, token)
        print(f"\n/v1/models            : {len(v1):>5}  (nutzbar, credential-gefiltert)")
        for mid in v1:
            print("    -", mid)
    except Exception as exc:  # noqa: BLE001
        print("\n/v1/models            : FEHLER -", exc)
    if cli:
        for name, fn in (("/api/models/catalog ", fetch_catalog_entries),
                         ("/api/models         ", fetch_all_entries)):
            try:
                entries = fn(root, cli)
                extra = ""
                if name.startswith("/api/models/catal"):
                    extra = f"  ({len({e.get('provider') for e in entries})} Provider)"
                print(f"{name}: {len(entries):>5}{extra}")
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: FEHLER - {exc}")
    print("\nDetail: --source v1|catalog|all  (--grep TEXT --kind llm --ready --json)")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    cfg = load_config()
    ap = argparse.ArgumentParser(description="Alle Modelle des lokalen Gateways auflisten.")
    ap.add_argument("--source", choices=("summary", "v1", "catalog", "all"), default="summary",
                    help="summary (Default) | v1 = nutzbar | catalog = Dashboard-Katalog | all = komplette Tabelle")
    ap.add_argument("--gateway", default=cfg.get("gateway_url") or DEFAULT_GATEWAY,
                    help="Gateway-Wurzel inkl. /v1 (Default: aus config.json)")
    ap.add_argument("--token", default=cfg.get("token") or "", help="Gateway-Token (Default: aus config.json)")
    ap.add_argument("--data-dir", default=None, help="FlagshipRouter DATA_DIR (Default: autodetect)")
    ap.add_argument("--grep", default=None, help="Nur Eintraege, die TEXT enthalten")
    ap.add_argument("--kind", default=None, help="Nur diese Art (z. B. llm, image, tts, embedding)")
    ap.add_argument("--ready", action="store_true", help="catalog: nur bereite/verbundene Eintraege")
    ap.add_argument("--json", action="store_true", help="Rohantwort ausgeben statt Tabelle")
    args = ap.parse_args(argv)

    base = args.gateway.rstrip("/")
    root = cat.dashboard_root(base)
    ddir = Path(args.data_dir) if args.data_dir else cat.data_dir()
    cli = cat.cli_token(ddir)

    try:
        if args.source == "summary":
            print_summary(base, root, args.token, cli)
            return 0

        if args.source == "v1":
            v1 = fetch_v1(base, args.token)
            if args.json:
                print(json.dumps({"data": [{"id": i} for i in v1]}, indent=2))
            else:
                print_v1(v1, args.grep)
            return 0

        if not cli:
            print(f"Fehler: CLI-Token nicht ableitbar - {ddir} enthaelt kein machine-id / auth/cli-secret.",
                  file=sys.stderr)
            print("Tipp: --data-dir <FlagshipRouter DATA_DIR> setzen.", file=sys.stderr)
            return 2

        if args.source == "catalog":
            if args.json:
                print(json.dumps(cat.fetch_catalog(root, cli), indent=2, ensure_ascii=False))
            else:
                print_catalog(fetch_catalog_entries(root, cli), args.grep, args.kind, args.ready)
            return 0

        if args.json:
            print(json.dumps(cat.fetch_all(root, cli), indent=2, ensure_ascii=False))
        else:
            print_all(fetch_all_entries(root, cli), args.grep)
        return 0

    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} bei {exc.url}" + (" (401: Dashboard-Login/CLI-Token noetig)" if exc.code == 401 else ""),
              file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Gateway nicht erreichbar: {exc.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
