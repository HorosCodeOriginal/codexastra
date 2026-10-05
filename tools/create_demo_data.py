"""tools/create_demo_data.py - Beispieldaten zum Ausprobieren.

Legt ein Beispiel-Projekt (data/sample-project) und zwei Beispiel-Gespraeche
(data/conversations.json) an, damit man die App sofort mit Inhalt testen
kann. Vorhandene Gespraeche werden nicht ueberschrieben - nur ergaenzt.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Pfad zum Projektroot (eine Ebene ueber tools/)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, models, store  # noqa: E402


def make_sample_project() -> Path:
    proj = ROOT / "data" / "sample-project"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "docs").mkdir(parents=True, exist_ok=True)

    (proj / "README.md").write_text(
        "# Beispielprojekt\n\nDieses Projekt wurde von create_demo_data.py angelegt.\n",
        encoding="utf-8",
    )
    (proj / "src" / "hello.py").write_text(
        "def greet(name: str) -> str:\n    return f'Hallo, {name}!'\n\n"
        "if __name__ == '__main__':\n    print(greet('CodexAstra'))\n",
        encoding="utf-8",
    )
    (proj / "docs" / "notes.md").write_text(
        "## Notizen\n- Codex kann hier Dateien anlegen.\n- Und Code ausfuehren.\n",
        encoding="utf-8",
    )
    return proj


def make_demo_conversations(svc_store: store.Store, project_path: str) -> int:
    existing = svc_store.load_conversations()
    if existing:
        print(f"Es gibt bereits {len(existing)} Gespraech(e) - keine Demo angelegt.")
        return 0

    c1 = models.new_conversation(config.DEFAULT_MODEL, project_path)
    c1.title = "Beispiel: Hallo Welt"
    c1.add(models.ChatMessage(role="user", content="Erstelle eine hello.py, die 'Hallo, CodexAstra' ausgibt."))
    c1.add(models.ChatMessage(role="assistant",
                              content="Ich habe src/hello.py angelegt. Mit `python src/hello.py` erhaeltst du die Ausgabe."))
    c1.add(models.ChatMessage(role="tool", kind="tool", tool_name="exec",
                              content="Set-Content -Path src/hello.py -Value \"...\"\nr = await tools.exec_command({cmd: 'python src/hello.py'})\ntext(JSON.stringify(r))"))

    c2 = models.new_conversation(config.DEFAULT_MODEL, project_path)
    c2.title = "Beispiel: Status"
    c2.add(models.ChatMessage(role="user", content="Was steht in der README?"))
    c2.add(models.ChatMessage(role="assistant",
                              content="Die README beschreibt ein Beispielprojekt, das zum Ausprobieren der App dient."))

    svc_store.upsert_conversation(c1)
    svc_store.upsert_conversation(c2)
    return 2


def main() -> None:
    cfg = config.load_config()
    svc_store = store.Store(config.CONVERSATIONS_PATH, config.PROJECTS_PATH, None)
    proj = make_sample_project()
    n = make_demo_conversations(svc_store, str(proj))
    print(f"Beispielprojekt: {proj}")
    print(f"Beispiel-Gespraeche angelegt: {n}")
    print("Starte die App (run.ps1) und waehle das Projekt data/sample-project.")


if __name__ == "__main__":
    main()
