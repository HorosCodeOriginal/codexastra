"""core/store.py - Persistenz mit atomarem Schreiben und Papierkorb-Logik.

Nichts wird direkt geloescht: geloeschte Gespraeche wandern in einen
Papierkorb und werden erst bei ``purge`` endgueltig entfernt. Vor dem
endgueltigen Loeschen wird automatisch ``backup()`` aufgerufen.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from . import models
from .models import Conversation, Project


class Store:
    def __init__(self, conv_path: Path, proj_path: Path, log=None):
        self.conv_path = Path(conv_path)
        self.proj_path = Path(proj_path)
        self.log = log
        self._lock = threading.Lock()
        for p in (self.conv_path, self.proj_path):
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    # -- Gespraeche ---------------------------------------------------------
    def load_conversations(self) -> list[Conversation]:
        try:
            with open(self.conv_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [models.normalize_conversation(r) for r in raw]

    def save_conversations(self, convs: list[Conversation]) -> None:
        self._atomic_write(
            self.conv_path,
            [c.to_dict() for c in convs],
        )

    def upsert_conversation(self, conv: Conversation) -> None:
        """Ein Gespraech einfuegen/aktualisieren und sofort speichern."""
        with self._lock:
            convs = self.load_conversations()
            idx = next((i for i, c in enumerate(convs) if c.id == conv.id), None)
            if idx is None:
                convs.append(conv)
            else:
                convs[idx] = conv
            self.save_conversations(convs)

    def delete_conversation(self, conv_id: str) -> None:
        with self._lock:
            convs = self.load_conversations()
            kept = [c for c in convs if c.id != conv_id]
            if len(kept) == len(convs):
                return
            self.backup(self.conv_path)
            self.save_conversations(kept)

    # -- Projekte -----------------------------------------------------------
    def load_projects(self) -> list[Project]:
        try:
            with open(self.proj_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [Project.from_dict(r) for r in raw]

    def save_projects(self, projects: list[Project]) -> None:
        self._atomic_write(self.proj_path, [p.to_dict() for p in projects])

    def add_project(self, project: Project) -> None:
        with self._lock:
            projs = self.load_projects()
            if any(p.path == project.path for p in projs):
                # Nur last_used aktualisieren
                for p in projs:
                    if p.path == project.path:
                        p.last_used = project.last_used
            else:
                projs.append(project)
            self.save_projects(projs)

    # -- Hilfen -------------------------------------------------------------
    def backup(self, path: Path) -> None:
        """Kopie mit Zeitstempel anlegen - nie den Originalpfad ueberschreiben."""
        try:
            path = Path(path)
            if not path.exists():
                return
            stamp = models._now().replace(":", "")
            dst = path.with_suffix(f".{stamp}.bak")
            shutil.copy2(path, dst)
            if self.log:
                self.log.debug(f"backup {path.name} -> {dst.name}")
        except OSError as exc:
            if self.log:
                self.log.warn(f"backup fehlgeschlagen: {exc}")

    def _atomic_write(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError as exc:
            if self.log:
                self.log.error(f"speichern fehlgeschlagen {path}: {exc}")
            raise
