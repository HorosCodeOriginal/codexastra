# Architektur – CodexAstra

## 1. Schichtenmodell

```
┌─────────────────────────────────────────────────────────────┐
│  app.py  (Tkinter-Oberfläche, NUR Haupt-Thread)              │
│  Widgets, Events, Layout, Theme, Queue-Poll                  │
└───────────────┬─────────────────────────────────────────────┘
                │ kennt nur service (Fassade)
┌───────────────▼─────────────────────────────────────────────┐
│  core/service.py  (Fassade, Seiteneffekte)                   │
│  • Bridge-Lebenszyklus (start/stop/health)                   │
│  • Gateway-Status, Modellliste                               │
│  • Projekt-Scan                                             │
│  • codex exec ausführen (streaming + Session-Resume)         │
└───┬───────────┬───────────────┬──────────────┬──────────────┘
    │           │               │              │
┌───▼───┐ ┌────▼────┐ ┌────────▼────────┐ ┌────▼─────┐
│config │ │ models  │ │ store (JSON)     │ │ views    │
│       │ │         │ │ atomar + backup  │ │ exporter │
└───────┘ └─────────┘ └─────────────────┘ └──────────┘
                │
        core/logging_utils.py (thread-sicheres Protokoll)
```

**Regel:** `models`/`config` kennen niemanden. `store`/`views`/`exporter`
kennen nur jene. `service` kennt alle. Die Oberfläche kennt nur `service`.

## 2. Datenfluss – ein Turn

```
Nutzer tippt Prompt
  → app._on_send()  (Haupt-Thread)
      • fügt User-Nachricht zu Conversation hinzu, speichert
      • service.run_turn(conv, prompt, model, token, push)
          → baut codex-Kommando (exec, --json, resume <id>, -C <projekt>,
             -s workspace-write, -c base_url→Bridge, -c Authorization)
          → Popen(codex, stdin=PIPE, stdout/stderr=PIPE, CREATE_NO_WINDOW)
          → Hintergrund-Threads lesen stdout (JSONL) + stderr
              • parsen Events → push(("text"|"tool"|"error"|"status"|"done", …))
              • session_id/thread_id merken (für Resume)
          → nach Ende: letzte Antwort aus --output-last-message-Datei,
            Conversation aktualisieren + speichern
  → Queue-Events werden in app._poll() (Haupt-Thread) abgearbeitet
      • Text/Tool/Fehler live in die Chat-Anzeige schreiben
      • Statusleiste + Gespächsliste aktualisieren
```

`bridge.py` liegt zwischen Codex und dem Gateway:
- Codex sendet seine Tools als `additional_tools` mit verschachtelten
  `namespace`-Blöcken (z. B. `functions.exec`). Das Gateway löst Namespaces
  nicht auf → Router-Fehler `unsupported call: functions`.
- Die Bridge klopft die Namespaces flach, bietet `exec` als Funktion mit
  einem `input`-String-Feld an und übersetzt die Antwort on-the-fly
  (`function_call` → `custom_tool_call` mit rohem JavaScript im Feld `input`).
- **Streaming** wird durchgereicht (der non-streaming-Pfad des Gateways
  bricht auf Folge-Turns ab).

## 3. Datenmodell

- **Conversation** (`core/models.py`): `id`, `title`, `model`,
  `project_path`, `session_id` (Codex-Thread für Resume), `messages[]`,
  `created`, `updated`.
- **ChatMessage**: `role` (user/assistant/tool/system/error), `content`,
  `kind`, `tool_name`, `ts`.
- **Project**: `path`, `name`, `last_used` (Metadaten; der Baum wird zur
  Laufzeit gescannt).
- **Persistenz:** `data/conversations.json` (Liste), `data/projects.json`,
  `data/events.jsonl` (Protokoll). Schreiben erfolgt **atomar**
  (temp + `os.replace`).

## 4. Sicherheitsregeln

| Regel | Umsetzung |
| --- | --- |
| Kein Terminal | `pythonw.exe` + `CREATE_NO_WINDOW` für Bridge und codex |
| Kein direktes Löschen | Gespäche via `store.delete_conversation` (Backup vorher) |
| Atomares Schreiben | temp-Datei + `os.replace` |
| Robustes Laden | kaputtes JSON → Standardwerte statt Absturz |
| Token-Schutz | Token nur in `config.json`; wird per `-c` durchgereicht, nie im Chat gezeigt |
| Pfadgrenzen | Projekt-Scan ignoriert `.git`, `node_modules`, `.venv`, … |

## 5. Erweiterungspunkte

- **Weitere Provider:** `provider_id`/`gateway_url` in `config.json`.
- **Sandbox-Stufe:** `sandbox` (`read-only` | `workspace-write` |
  `danger-full-access`).
- **Mehr Modelle:** werden automatisch aus `/v1/models` geladen.
- **Eigene Werkzeuge im UI:** `core/views.py` (Suche/Filter), `app.py`
  (neue Panels), `core/service.py` (neue Events in `push`).
- **Klickfreier Test:** `tools/smoke_app.py` baut die UI ohne Maus.
