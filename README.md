# CodexAstra – Desktop für lokales Codex

Eine native **Windows-Desktop-App** (Tkinter, dunkles Theme) für den lokalen
OpenAI-Codex-CLI. Sie startet Codex **ohne Terminal**, leitet dessen
Werkzeug-Aufrufe über eine kleine Bridge an dein Gateway weiter und bietet
Chat-Verlauf, Modell-Auswahl, Projekt-Browser und Live-Ausgabe.

## Was es kann

- **Kein Terminal mehr.** Die App läuft über `pythonw.exe` – es erscheint
  kein Konsolenfenster.
- **Codex mit Datei- & Code-Zugriff.** Über die eingebaute Bridge
  (`bridge.py`) werden Codex’ `exec`-Werkzeug-Aufrufe so übersetzt, dass das
  Gateway sie ausführt – Codex kann Dateien anlegen und Code ausführen.
- **Gateway-Status** live (Bridge ↔ Gateway), inkl. Modellliste.
- **Modell-Auswahl** aus dem Gateway (z. B. `gpt-6-astra`).
- **Projekt-Browser** mit nativem Ordner-Dialog und Dateibaum.
- **Mehrere Gespäche** mit Verlauf, die als JSON gespeichert werden.
- **Export** eines oder aller Gespäche als Markdown.
- **Zwei Modi:** *einfach* (nur Chat + Projekt) und *experte* (zusätzlich
  Logs, Gateway-Details, Bridge-Steuerung).

## Schnellstart

1. **Doppelklick auf `run.ps1`.**
   Beim ersten Start wird bei Bedarf eine tkinter-fähige `.venv` angelegt und
   nebenbei die Verknüpfung `CodexAstra.lnk` erstellt – diese startet die App
   ganz ohne Terminal.
2. **Projekt öffnen:** Links auf *📁 Projekt öffnen…* und einen Ordner wählen.
   Dort arbeitet Codex (Dateien anlegen, Code ausführen).
3. **Modell wählen** (oben), dann unten eine Frage eingeben und **Senden**
   (oder `Enter`). `Shift+Enter` = neue Zeile.
4. **Gateway-Status** oben prüfen (grün = OK).

> Hinweis: Die Bridge startet automatisch, lauscht auf `127.0.0.1:20131`
> (`bridge_port` in `config.json`) und zeigt auf dein lokales Gateway unter
> `http://127.0.0.1:20128/v1`. Port 20131 statt 20127, weil 20127 der Default
> von FlagshipRouter ist und sonst kollidiert. Das Token aus `config.json` wird
> per `-c` an Codex durchgereicht – es muss nicht in `config.toml` stehen und es
> werden **keine Token-Warnungen** angezeigt.

## Struktur

```
app.py                 Oberfläche (Einstiegspunkt)
run.ps1                Doppelklick-Starter (pythonw, kein Terminal)
config.json            Einstellungen (Token, Gateway, Modell, …)
bridge.py              Responses-API-Übersetzer (Codex ↔ Gateway)
core/                  Fachlogik (config, models, store, service, …)
docs/ARCHITEKTUR.md    Schichtenmodell & Datenfluss
tools/                 create_demo_data.py, smoke_app.py (+ Diagnose-Skripte)
data/                  conversations.json, events.jsonl, bridge.log
```

## Tasten & Bedienung

- `Enter` im Eingabefeld → senden
- `Shift+Enter` → Zeilenumbruch
- Modus-Umschalter oben rechts: *einfach* ↔ *experte*
- **Modell-Quelle** (Knopf neben der Modell-Auswahl) schaltet die Liste um:
  *nutzbar* (`/v1/models`, nur Provider mit Credentials) → *alle (frei)*
  (Dashboard-Katalog, `/api/models/catalog`) → *alle (komplett)*
  (`/api/models`, komplette eingebaute Tabelle). Die Wahl wird in
  `config.json` (`model_source`) gespeichert. Im *experten*-Modus zeigt
  „Verfügbare Modelle" zusätzlich Art, Verbindungsstatus und Kontextfenster.
- **nur verbundene** (zweiter Knopf) blendet im Katalog Modelle nicht
  verbundener Provider aus (`model_ready_only`). Beim Senden mit einem Modell,
  das nicht in `/v1/models` steht, fragt die App vorher nach.

## Fehlerbehebung

| Symptom | Ursache / Lösung |
| --- | --- |
| Gateway-Anzeige rot | Gateway (Port 20128) läuft nicht. Im *experten*-Modus *Bridge neu starten* oder das Dashboard starten. |
| „codex ist nicht verfügbar“ | `codex.exe` nicht im PATH. Pfad ggf. in `core/config.py` (`_resolve_codex`) anpassen. |
| Kein Terminal beim Start | `run.ps1` nutzt `pythonw`; für den saubersten Start die `CodexAstra.lnk` verwenden. |
| App zeigt nichts / hängt | `data/events.jsonl` prüfen; `tools/smoke_app.py` ausführen, um die UI klickfrei zu testen. |

## Diagnose-Skripte (optional)

- `tools/probe_upstream.py` – Gateway & Token prüfen
- `tools/probe_stream.py` – zeigt, dass der Streaming-Pfad Tool-Calls liefert
- `tools/mock_gateway.py` – lokales Mock-Gateway zum Testen ohne echtes LLM
- `tools/list_models.py` – **alle** Modelle auflisten. Drei Quellen:
  `/v1/models` (nutzbar, credential-gefiltert), `/api/models/catalog`
  (Dashboard-Katalog der freien Provider) und `/api/models` (komplette
  eingebaute Tabelle). Die `/api`-Endpunkte liegen hinter `dashboardGuard`
  und werden lokal über den berechenbaren CLI-Token (`x-fr-cli-token` aus
  `<DATA_DIR>/machine-id` + `auth/cli-secret`) ohne Browser-Login erreicht.

  ```powershell
  python tools/list_models.py                        # Übersicht + nutzbare Modelle
  python tools/list_models.py --source catalog       # Dashboard-Katalog
  python tools/list_models.py --source all --grep claude
  python tools/list_models.py --source catalog --json > models.json
  ```
