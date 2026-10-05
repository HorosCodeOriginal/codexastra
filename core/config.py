"""core/config.py - Einstellungen, Pfade und Konstanten.

Alle Pfade sind projektlokal (relativ zu dieser Datei). Nichts wird ausserhalb
von C:/CodexAstra angefasst. Der Token wird hier gehalten, damit die App
selbststaendig startet, OHNE die Warnungen des ursprünglichen .ps1-Skripts im
Chat anzuzeigen - er wird per ``-c`` an codex durchgereicht.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

APP_NAME = "CodexAstra"
APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BASE_DIR / "core"
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"
EVENTS_PATH = DATA_DIR / "events.jsonl"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
PROJECTS_PATH = DATA_DIR / "projects.json"
BRIDGE_PY = BASE_DIR / "bridge.py"

# -- Bridge / Gateway -------------------------------------------------------
BRIDGE_HOST = "127.0.0.1"
# Port 20127 ist der Default von FlagshipRouter (package.json) - dort kollidiert
# die Bridge mit einem per `npm start` gestarteten Router. Deshalb 20131.
BRIDGE_PORT = 20131
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/v1"
BRIDGE_HEALTH = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/__bridge/health"
# Upstream des Gateways: der lokale Dashboard-Service des Nutzers.
GATEWAY_URL = "http://127.0.0.1:20128/v1"

PROVIDER_ID = "flagshiprouter"
DEFAULT_MODEL = "gpt-6-astra"
BRIDGE_LOG = DATA_DIR / "bridge.log"

# -- codex Binary aufloesen -------------------------------------------------
def _resolve_codex() -> str | None:
    """Liefert den Pfad zur echten codex.exe (nicht zum npm-Shim)."""
    # 1) Expliziter Installationspfad der OpenAI-Codex-App
    candidate = Path(
        r"C:/Users/Administrator/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe"
    )
    if candidate.exists():
        return str(candidate)
    # 2) PATH - bevorzuge die .exe, nicht den npm-.cmd-Shim
    found = shutil.which("codex")
    if found:
        p = Path(found)
        if p.suffix.lower() == ".cmd":
            exe = p.with_suffix(".exe")
            if exe.exists():
                return str(exe)
        return str(p)
    return None


CODEX_BIN = _resolve_codex()

# -- Python-Interpreter fuer die Bridge ------------------------------------
def _resolve_pythonw() -> str:
    venv_pw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pw.exists():
        return str(venv_pw)
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    which = shutil.which("pythonw") or shutil.which("python")
    return which or "python"


PYTHONW = _resolve_pythonw()

# -- Standard-Konfiguration -------------------------------------------------
DEFAULTS = {
    "ui": {
        "mode": "simple",            # "simple" oder "expert"
        "window_geometry": "1320x820",
        "theme": "dark",
        "font_size": 11,
    },
    # Token des Gateways. Wird per -c an codex durchgereicht. ACHTUNG: hier nur
    # ein Platzhalter - das echte Token gehoert in die (gitignored) config.json
    # oder in die Umgebungsvariable LOCALCODEX_API_KEY. Nie ein echtes Token
    # oeffentlich committen.
    "token": "DEIN_TOKEN_HIER",
    "gateway_url": GATEWAY_URL,
    "provider_id": PROVIDER_ID,
    "default_model": DEFAULT_MODEL,
    "sandbox": "workspace-write",    # read-only | workspace-write | danger-full-access
    "skip_git_repo_check": True,
    "auto_start_bridge": True,
    "bridge_host": BRIDGE_HOST,
    "bridge_port": BRIDGE_PORT,
    # Welche Modelle die Auswahl zeigt:
    #   "gateway" -> nur /v1/models (nutzbar, credential-gefiltert)
    #   "catalog" -> /api/models/catalog (Dashboard-Katalog der freien Provider)
    #   "all"     -> /api/models (komplette eingebaute Tabelle + Custom-Modelle)
    "model_source": "gateway",
    # Im Katalog nur Modelle verbundener Provider zeigen ("ready").
    "model_ready_only": True,
    "max_project_files": 4000,
    "ignore_dirs": [
        ".git", "node_modules", "__pycache__", ".venv", "dist",
        "build", ".workbuddy-ai", "target", "out", "bin", "obj",
    ],
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Laedt config.json, mischt fehlende Schluessel aus DEFAULTS (robust)."""
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        if isinstance(user, dict):
            _deep_update(cfg, user)
    except (FileNotFoundError, ValueError, OSError):
        pass
    # Verschachtelte UI-Defaults auffuellen
    cfg.setdefault("ui", {})
    for k, v in DEFAULTS["ui"].items():
        cfg["ui"].setdefault(k, v)
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    """Atomares Schreiben der Konfiguration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _deep_update(base: dict, over: dict) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
