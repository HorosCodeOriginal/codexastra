"""core/views.py - Filter, Suche, Sortierung, Projekt-Baum-Scan.

Reine Hilfsfunktionen ohne Seiteneffekte. Kennt nur die Modelle aus models.
"""
from __future__ import annotations

import os
from pathlib import Path

from .models import Conversation, Project


# --------------------------------------------------------------------------
# Gespraeche
# --------------------------------------------------------------------------
def search_conversations(convs: list[Conversation], query: str) -> list[Conversation]:
    if not query:
        return convs
    q = query.lower()
    out = []
    for c in convs:
        hay = [c.title, c.model, c.project_path]
        hay += [m.content for m in c.messages]
        if any(q in (h or "").lower() for h in hay):
            out.append(c)
    return out


def sort_conversations(convs: list[Conversation], key: str = "updated") -> list[Conversation]:
    if key == "title":
        return sorted(convs, key=lambda c: c.title.lower())
    if key == "created":
        return sorted(convs, key=lambda c: c.created, reverse=True)
    return sorted(convs, key=lambda c: c.updated, reverse=True)


def conversation_preview(conv: Conversation, limit: int = 120) -> str:
    for m in reversed(conv.messages):
        if m.role == "user" and m.content.strip():
            t = m.content.strip().replace("\n", " ")
            return (t[:limit] + "...") if len(t) > limit else t
    return "(leeres Gespraech)"


# --------------------------------------------------------------------------
# Projekt-Baum
# --------------------------------------------------------------------------
def scan_project(
    root: str,
    ignore_dirs: list[str] | None = None,
    max_files: int = 4000,
    max_depth: int = 8,
) -> dict:
    """Liefert eine verschachtelte Baum-Struktur des Projektordners.

    {"name", "path", "is_dir": True, "children": [...]}
    Dateien werden als {"name","path","is_dir": False} abgelegt.
    """
    ignore = set(ignore_dirs or [])
    root_path = Path(root)
    if not root_path.exists():
        return {"name": root_path.name or root, "path": str(root_path), "is_dir": True, "children": []}

    def walk(p: Path, depth: int) -> dict:
        node = {
            "name": p.name or str(p),
            "path": str(p),
            "is_dir": True,
            "children": [],
        }
        if depth >= max_depth:
            return node
        try:
            entries = sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return node
        count = 0
        for entry in entries:
            if entry.name in ignore or entry.name.startswith("."):
                if entry.name in ignore:
                    continue
            if entry.is_dir(follow_symlinks=False):
                if count >= max_files:
                    break
                node["children"].append(walk(Path(entry.path), depth + 1))
                count += 1
            else:
                if count >= max_files:
                    break
                node["children"].append(
                    {"name": entry.name, "path": str(entry.path), "is_dir": False}
                )
                count += 1
        return node

    return walk(root_path, 0)


def project_file_count(tree: dict) -> int:
    if not tree.get("is_dir"):
        return 1
    total = 0
    for ch in tree.get("children", []):
        total += project_file_count(ch)
    return total


def search_files(tree: dict, query: str, out: list[dict] | None = None) -> list[dict]:
    if out is None:
        out = []
    q = query.lower()
    if q and q in tree.get("name", "").lower():
        out.append(tree)
    for ch in tree.get("children", []):
        search_files(ch, query, out)
    return out
