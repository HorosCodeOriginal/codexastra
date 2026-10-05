"""tools/smoke_app.py - klickfreier Selbsttest der Oberflaeche.

Baut die App ohne Maus auf, schaltet alle Modi/Reiter/Dialoge durch und
prueft Zustand statt nur Widget-Vorhandensein. Nutzt eine temporaere
Datenumgebung, damit die echte config.json / conversations.json des Nutzers
nicht veraendert werden.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config as cc  # noqa: E402
import core.service as cservice  # noqa: E402
from app import App  # noqa: E402


def pump(root: tk.Tk, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            break
        time.sleep(0.03)


def main() -> int:
    # --- temporaere Datenumgebung (kein Ueberschreiben der Nutzerdaten) ---
    tmp = Path(tempfile.mkdtemp(prefix="codexastra_smoke_"))
    cc.CONVERSATIONS_PATH = tmp / "conversations.json"
    cc.PROJECTS_PATH = tmp / "projects.json"
    cc.EVENTS_PATH = tmp / "events.jsonl"
    cc.DATA_DIR = tmp

    # --- Netzwerk/Codex im Test stubben (kein echter Start noetig) ---
    cservice.Service.ensure_bridge = lambda self, timeout=6: True
    cservice.Service.bridge_health = lambda self: {
        "bridge_up": True, "gateway_ok": True,
        "models": [cc.DEFAULT_MODEL, "gpt-4o", "o3"], "error": "",
    }
    cservice.Service.list_models = lambda self: [cc.DEFAULT_MODEL, "gpt-4o", "o3"]
    cservice.Service.available = lambda self: True

    # --- Dialoge stubben ---
    import tkinter.filedialog as fd
    import tkinter.messagebox as mb

    sample_proj = tmp / "sample"
    sample_proj.mkdir()
    (sample_proj / "a.txt").write_text("hallo", encoding="utf-8")
    (sample_proj / "b.py").write_text("print(1)", encoding="utf-8")

    fd.askdirectory = lambda **k: str(sample_proj)
    fd.asksaveasfilename = lambda **k: str(tmp / "export.md")
    mb.askyesno = lambda *a, **k: True
    mb.showerror = lambda *a, **k: None
    mb.showwarning = lambda *a, **k: None

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            print("  ✗ " + msg)
        else:
            print("  ✓ " + msg)

    cfg = cc.load_config()
    cfg["ui"]["mode"] = "simple"
    root = tk.Tk()
    try:
        app = App(root, cfg)
        pump(root, 0.6)

        print("[Modi]")
        check(app.cfg["ui"]["mode"] == "simple", "Start im einfachen Modus")
        check(app.right.winfo_ismapped() == 0, "Experten-Panel im einfachen Modus versteckt")
        app._toggle_mode()
        pump(root, 0.3)
        check(app.cfg["ui"]["mode"] == "expert", "Umschaltung auf experte")
        check(app.right.winfo_ismapped() == 1, "Experten-Panel im experten-Modus sichtbar")
        app._toggle_mode()
        pump(root, 0.2)

        print("[Gespraeche]")
        before = len(app.conversations)
        app._new_chat()
        pump(root, 0.2)
        check(len(app.conversations) == before + 1, "Neues Gespraech erzeugt")
        check(app.active is not None, "Aktives Gespraech gesetzt")

        print("[Projekt]")
        app._pick_project()
        pump(root, 0.3)
        check(app.active.project_path == str(sample_proj), "Projekt-Pfad uebernommen")
        tree_children = app.tree.get_children()
        check(len(tree_children) > 0, "Projekt-Baum gefuellt")

        print("[Modell]")
        check(app.model_combo["values"], "Modell-Combobox befuellt")

        print("[Export]")
        app._export()
        pump(root, 0.3)
        check((tmp / "export.md").exists(), "Markdown-Export geschrieben")

        print("[Status]")
        app._update_gw({"bridge_up": True, "gateway_ok": True,
                        "models": [cc.DEFAULT_MODEL], "error": ""})
        check("OK" in app.gw_label.cget("text"), "Gateway-Status-Anzeige aktualisiert")

        print("[Shutdown]")
        # Kein echtes Speichern der Nutzer-config: _on_close nicht aufrufen.
        root.destroy()

        # Aufraeumen - bewusst KEIN save_config(): die echte config.json des
        # Nutzers soll vom Test nicht veraendert werden.
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        failures.append(f"Ausnahme: {exc}")

    print()
    if failures:
        print(f"ERGEBNIS: {len(failures)} Fehler")
        for f in failures:
            print(" - " + f)
        return 1
    print("ERGEBNIS: OK - alle Pruefungen bestanden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
