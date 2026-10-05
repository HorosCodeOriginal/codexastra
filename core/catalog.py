"""core/catalog.py - Modellkatalog des FlagshipRouter-Dashboards lesen.

Es gibt drei Modell-Quellen am lokalen Gateway (verifiziert 2026-10-06):

  /v1/models           -> nur Provider mit aktiver Verbindung ("nutzbar").
                          Das ist, was Codex/CodexAstra wirklich ansprechen kann.
  /api/models/catalog  -> Katalog des Dashboards ("Models"-Screen): alle Modelle
                          der kostenlosen Provider laut brand.json-Policy,
                          inkl. oeffentlicher Renames und Custom-Modellen.
  /api/models          -> die komplette eingebaute Tabelle (AI_MODELS) plus
                          Custom-Modelle, OHNE Credential-Filter.

Die beiden /api-Routen liegen hinter ``src/dashboardGuard.js`` und antworten
ohne Session mit 401. Lokal akzeptiert der Guard stattdessen den Header
``x-fr-cli-token``. Dieser Token ist aus den Laufzeitdaten berechenbar - es ist
KEIN Browser-Login noetig:

    sha256(<DATA_DIR>/machine-id + "fr-cli-auth" + <DATA_DIR>/auth/cli-secret)[:16]

DATA_DIR ist unter Windows ``%APPDATA%\\flagshiprouter`` (Override ueber die
Umgebungsvariablen FLAGSHIPROUTER_DATA_DIR oder DATA_DIR).
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

CLI_TOKEN_HEADER = "x-fr-cli-token"
CLI_TOKEN_SALT = "fr-cli-auth"
TIMEOUT = 20.0


# --------------------------------------------------------------------------
# Pfade / Token
# --------------------------------------------------------------------------
def data_dir() -> Path:
    """Verzeichnis der FlagshipRouter-Laufzeitdaten."""
    for env in ("FLAGSHIPROUTER_DATA_DIR", "DATA_DIR"):
        val = os.environ.get(env)
        if not val:
            continue
        if os.name == "nt" and val.startswith("/"):
            continue  # Unix-Pfad aus einer Docker-.env ignorieren
        return Path(val).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "flagshiprouter"
    return Path.home() / ".flagshiprouter"


def cli_token(ddir: Path | None = None) -> str | None:
    """Lokalen Dashboard-Token berechnen (siehe Modul-Docstring).

    Liefert ``None``, wenn machine-id oder cli-secret fehlen.
    """
    ddir = ddir or data_dir()
    try:
        raw = (ddir / "machine-id").read_text(encoding="utf-8").strip()
        secret = (ddir / "auth" / "cli-secret").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or not secret:
        return None
    return hashlib.sha256((raw + CLI_TOKEN_SALT + secret).encode("utf-8")).hexdigest()[:16]


def dashboard_root(gateway_url: str) -> str:
    """Aus ``http://host:port/v1`` die Dashboard-Wurzel ``http://host:port``."""
    url = (gateway_url or "").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url.rstrip("/")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def get_json(url: str, headers: dict | None = None, timeout: float = TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_catalog(root: str, cli: str) -> dict:
    """/api/models/catalog - Dashboard-Katalog der freien Provider."""
    return get_json(f"{root}/api/models/catalog", {CLI_TOKEN_HEADER: cli})


def fetch_all(root: str, cli: str) -> dict:
    """/api/models - komplette statische Tabelle + Custom-Modelle."""
    return get_json(f"{root}/api/models", {CLI_TOKEN_HEADER: cli})


# --------------------------------------------------------------------------
# Normalisierung -> einheitliche Eintraege
# --------------------------------------------------------------------------
def _entry(mid: str, name: str, provider: str, provider_name: str, kind: str,
           ready: bool, caps: dict | None = None) -> dict:
    return {
        "id": mid,
        "name": name,
        "provider": provider,
        "provider_name": provider_name,
        "kind": kind,
        "ready": ready,
        "caps": caps or {},
    }


def catalog_entries(catalog: dict, kinds: tuple[str, ...] | None = None,
                    ready_only: bool = False) -> list[dict]:
    """Eintraege aus einer /api/models/catalog-Antwort bauen."""
    providers = {p.get("id"): p for p in (catalog.get("providers") or []) if isinstance(p, dict)}
    out: list[dict] = []
    for m in catalog.get("models") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        kind = str(m.get("kind") or "llm")
        if kinds and kind not in kinds:
            continue
        if ready_only and not m.get("ready"):
            continue
        pid = str(m.get("providerId") or m.get("provider") or "")
        prov = providers.get(pid) or {}
        out.append(_entry(
            mid,
            str(m.get("name") or mid),
            pid,
            str(prov.get("name") or pid),
            kind,
            bool(m.get("ready")),
            m.get("caps") if isinstance(m.get("caps"), dict) else {},
        ))
    out.sort(key=lambda e: (e["provider"].lower(), e["id"].lower()))
    return out


def all_entries(payload: dict) -> list[dict]:
    """Eintraege aus einer /api/models-Antwort bauen (komplette Tabelle)."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in payload.get("models") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("routedModel") or m.get("fullModel") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(_entry(
            mid,
            str(m.get("name") or mid),
            str(m.get("provider") or ""),
            "",
            "llm",
            False,
            m.get("caps") if isinstance(m.get("caps"), dict) else {},
        ))
    out.sort(key=lambda e: e["id"].lower())
    return out
