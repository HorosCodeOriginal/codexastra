"""core/models.py - Datenformen und Normalisierung.

Fachlogik-Wissen ueber Gespraeche, Nachrichten und Projekte. Kennt niemanden
ausser ``config`` (Konstanten). Alle Lade-Pfade rufen ``normalize_*`` auf, damit
auch aeltere oder von Hand korrigierte Dateien sauber funktionieren.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------
# Nachricht
# --------------------------------------------------------------------------
class ChatMessage:
    __slots__ = ("role", "content", "ts", "kind", "tool_name")

    def __init__(
        self,
        role: str,
        content: str = "",
        ts: str = "",
        kind: str = "message",
        tool_name: str = "",
    ):
        self.role = role            # user | assistant | system | tool | error
        self.content = content
        self.ts = ts or _now()
        self.kind = kind            # message | tool | system | error
        self.tool_name = tool_name

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "kind": self.kind,
            "tool_name": self.tool_name,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ChatMessage":
        if not isinstance(raw, dict):
            return cls(role="system", content=str(raw), kind="error")
        return cls(
            role=str(raw.get("role", "system")),
            content=str(raw.get("content", "")),
            ts=str(raw.get("ts", _now())),
            kind=str(raw.get("kind", "message")),
            tool_name=str(raw.get("tool_name", "")),
        )


# --------------------------------------------------------------------------
# Gespraech
# --------------------------------------------------------------------------
class Conversation:
    def __init__(
        self,
        id: str,
        title: str,
        model: str,
        project_path: str = "",
        session_id: str = "",
        messages: list[ChatMessage] | None = None,
        created: str = "",
        updated: str = "",
    ):
        self.id = id
        self.title = title
        self.model = model
        self.project_path = project_path
        self.session_id = session_id
        self.messages: list[ChatMessage] = messages or []
        self.created = created or _now()
        self.updated = _now() if not updated else updated

    def add(self, msg: ChatMessage) -> None:
        self.messages.append(msg)
        self.updated = _now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "project_path": self.project_path,
            "session_id": self.session_id,
            "created": self.created,
            "updated": self.updated,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Conversation":
        if not isinstance(raw, dict):
            raw = {}
        msgs = [ChatMessage.from_dict(m) for m in (raw.get("messages") or [])]
        return cls(
            id=str(raw.get("id") or new_id()),
            title=str(raw.get("title") or "Neues Gespraech"),
            model=str(raw.get("model") or ""),
            project_path=str(raw.get("project_path") or ""),
            session_id=str(raw.get("session_id") or ""),
            messages=msgs,
            created=str(raw.get("created") or _now()),
            updated=str(raw.get("updated") or _now()),
        )


def new_conversation(model: str, project_path: str = "") -> Conversation:
    return Conversation(
        id=new_id(),
        title="Neues Gespraech",
        model=model,
        project_path=project_path,
    )


def normalize_conversation(raw: Any) -> Conversation:
    """Robust gegen kaputte/ungenaue Eintraege."""
    try:
        return Conversation.from_dict(raw)
    except Exception:
        return new_conversation("", "")


# --------------------------------------------------------------------------
# Projekt (einfache Metadaten; der Baum wird zur Laufzeit gescannt)
# --------------------------------------------------------------------------
class Project:
    def __init__(self, path: str, name: str = "", last_used: str = ""):
        self.path = path
        self.name = name or (path.rstrip("/\\").split("/")[-1] or path)
        self.last_used = last_used or _now()

    def to_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "last_used": self.last_used}

    @classmethod
    def from_dict(cls, raw: Any) -> "Project":
        if not isinstance(raw, dict):
            return cls(str(raw))
        return cls(
            path=str(raw.get("path", "")),
            name=str(raw.get("name", "")),
            last_used=str(raw.get("last_used", _now())),
        )
