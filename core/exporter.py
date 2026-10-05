"""core/exporter.py - Export von Gespraechen und Protokoll als Markdown.

Nichts wird ohne Rueckgabewert geschrieben: die Aufrufer entscheiden, wohin
die Datei landet (native filedialog im UI).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import Conversation


def conversation_to_markdown(conv: Conversation) -> str:
    lines = [
        f"# {conv.title}",
        "",
        f"- **Modell:** `{conv.model}`",
        f"- **Projekt:** `{conv.project_path or '-'}`",
        f"- **Erstellt:** {conv.created}",
        f"- **Aktualisiert:** {conv.updated}",
        "",
        "---",
        "",
    ]
    for m in conv.messages:
        if m.role == "user":
            lines.append(f"## 👤 Du")
            lines.append("")
            lines.append(m.content.strip() or "_(leer)_")
        elif m.role == "assistant":
            lines.append(f"## 🤖 Codex")
            lines.append("")
            lines.append(m.content.strip() or "_(leer)_")
        elif m.kind == "tool":
            lines.append(f"### 🔧 Werkzeug: {m.tool_name}")
            lines.append("")
            lines.append("```")
            lines.append(m.content.strip())
            lines.append("```")
        elif m.kind == "error":
            lines.append("### ⚠️ Fehler")
            lines.append("")
            lines.append("```")
            lines.append(m.content.strip())
            lines.append("```")
        elif m.kind == "system":
            lines.append(f"_{m.content.strip()}_")
        lines.append("")
    return "\n".join(lines) + "\n"


def export_conversation(conv: Conversation, dest: str) -> str:
    """Schreibt eine einzelne Gespraechs-Datei; liefert den Pfad zurueck."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(conversation_to_markdown(conv), encoding="utf-8")
    return str(path)


def export_all_markdown(convs: list[Conversation], dest: str) -> str:
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    header = f"# CodexAstra - Export ({stamp})\n\n{len(convs)} Gespraech(e)\n\n---\n\n"
    body = "\n\n---\n\n".join(conversation_to_markdown(c) for c in convs)
    path.write_text(header + body + "\n", encoding="utf-8")
    return str(path)
