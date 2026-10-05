"""core/logging_utils.py - thread-sicheres Protokoll.

Schreibt jedes Ereignis als JSONL in data/events.jsonl und haelt einen
In-Memory-Ringpuffer fuer die Anzeige in der GUI. Pythons ``logging`` ist
bewusst nicht im Spiel - wir wollen kontrollierte, frontend-taugliche Zeilen.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path


class AppLog:
    def __init__(self, events_path: Path, max_buffer: int = 2000):
        self._lock = threading.Lock()
        self._buf: list[dict] = []
        self.max_buffer = max_buffer
        self.path = Path(events_path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _emit(self, level: str, msg: str) -> None:
        entry = {"ts": self._now(), "level": level, "msg": str(msg)}
        with self._lock:
            self._buf.append(entry)
            if len(self._buf) > self.max_buffer:
                self._buf.pop(0)
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def debug(self, msg: str) -> None:
        self._emit("debug", msg)

    def info(self, msg: str) -> None:
        self._emit("info", msg)

    def warn(self, msg: str) -> None:
        self._emit("warn", msg)

    def error(self, msg: str) -> None:
        self._emit("error", msg)

    def recent(self, n: int = 300) -> list[dict]:
        with self._lock:
            return list(self._buf[-n:])

    def clear_buffer(self) -> None:
        with self._lock:
            self._buf.clear()

    def export_markdown(self, lines: list[str] | None = None) -> str:
        """Gibt das Protokoll als Markdown zurueck (oder nur die uebergebenen)."""
        with self._lock:
            items = lines if lines is not None else list(self._buf)
        out = ["# Protokoll", ""]
        for e in items:
            out.append(f"- `{e['ts']}` **{e['level']}**: {e['msg']}")
        return "\n".join(out) + "\n"
