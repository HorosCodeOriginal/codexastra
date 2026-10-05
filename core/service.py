"""core/service.py - Fassade fuer die Oberflaeche.

Buendelt alle Seiteneffekte: Bridge-Lebenszyklus, Gateway-Status,
Projekt-Scan und das Ausfuehren von Codex-Turns (streaming + Session-Resume).

Wichtig fuer die Tkinter-Regel ``Widgets nur im Haupt-Thread'':
diese Klasse greift NIE auf Tk-Widgets zu. Stattdessen bekommt ``run_turn``
eine ``push(event)``-Callback, ueber die Ereignisse thread-sicher in eine
Queue des UIs geschoben werden. Die GUI pollt diese Queue und aktualisiert
sich selbst.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
except AttributeError:
    CREATE_NO_WINDOW = 0x08000000

from . import config
from .models import ChatMessage, Conversation
from . import views

# Ereignis-Typen, die ueber push() ans UI gehen:
#   ("status", text)
#   ("text",   delta)            # Assistenten-Text (live)
#   ("tool",   name, detail)     # Werkzeug-Aufruf
#   ("error",  msg)
#   ("done",   session_id, error)  # Turn beendet


class Service:
    def __init__(self, cfg: dict, log, store):
        self.cfg = cfg
        self.log = log
        self.store = store
        self._bridge_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Bridge
    # ------------------------------------------------------------------
    def bridge_health(self) -> dict:
        """Liefert {'bridge_up', 'gateway_ok', 'models', 'error'}."""
        res = {"bridge_up": False, "gateway_ok": False, "models": [], "error": ""}
        try:
            data = self._http_get_json(config.BRIDGE_HEALTH, timeout=4)
            res["bridge_up"] = bool(data and data.get("status") == "ok")
            res["upstream"] = (data or {}).get("upstream", "")
        except Exception as exc:
            res["error"] = f"Bridge nicht erreichbar: {exc}"
            return res
        # Gateway erreichen: ueber die Bridge /v1/models abfragen
        try:
            m = self._http_get_json(f"{config.BRIDGE_URL}/models", timeout=8)
            if isinstance(m, dict):
                models = [x.get("id") for x in (m.get("data") or []) if isinstance(x, dict)]
                res["models"] = models
                res["gateway_ok"] = True
        except Exception as exc:
            res["gateway_ok"] = False
            res["error"] = f"Gateway nicht erreichbar: {exc}"
        return res

    def ensure_bridge(self, timeout: float = 6.0) -> bool:
        """Startet die Bridge bei Bedarf und wartet, bis sie antwortet.

        Ist bereits eine Bridge auf dem Port aktiv, diese aber auf den
        falschen Upstream gerichtet (z. B. ein uebrig gebliebener Mock),
        wird sie beendet und frisch mit dem korrekten Gateway gestartet.
        """
        health = self.bridge_health()
        configured = self.cfg.get("gateway_url", config.GATEWAY_URL)
        if health["bridge_up"]:
            if health.get("upstream") == configured:
                if self.log:
                    self.log.info("Bridge laeuft bereits (korrekter Upstream).")
                return True
            if self.log:
                self.log.warn(
                    f"Bridge zeigt auf falschen Upstream {health.get('upstream')} "
                    f"- wird neu gestartet."
                )
            self._kill_listener_on_port(self.cfg.get("bridge_port", config.BRIDGE_PORT))
        elif not self.cfg.get("auto_start_bridge", True):
            return False
        if not Path(config.BRIDGE_PY).exists():
            if self.log:
                self.log.error(f"bridge.py fehlt: {config.BRIDGE_PY}")
            return False
        if not Path(config.PYTHONW).exists():
            if self.log:
                self.log.error(f"pythonw fehlt: {config.PYTHONW}")
            return False

        args = [
            config.PYTHONW,
            str(config.BRIDGE_PY),
            "--host", self.cfg.get("bridge_host", config.BRIDGE_HOST),
            "--port", str(self.cfg.get("bridge_port", config.BRIDGE_PORT)),
            "--upstream", self.cfg.get("gateway_url", config.GATEWAY_URL),
            "--log", str(config.BRIDGE_LOG),
        ]
        try:
            self._bridge_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
            if self.log:
                self.log.info(f"Bridge gestartet (PID {self._bridge_proc.pid}).")
        except Exception as exc:
            if self.log:
                self.log.error(f"Bridge-Start fehlgeschlagen: {exc}")
            return False

        # Kurz warten, bis die Bridge antwortet.
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = self.bridge_health()
            if h["bridge_up"]:
                return True
            time.sleep(0.3)
        return False

    def _kill_listener_on_port(self, port: int) -> bool:
        """Beendet den Prozess, der auf ``port`` lauscht (Windows, netstat)."""
        try:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=12
            ).stdout
        except Exception:
            return False
        killed = False
        for line in out.splitlines():
            if f":{port}" not in line:
                continue
            if not any(s in line for s in ("WARTEND", "LISTEN", "LISTENING", "ABHOEREN")):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local, pid = parts[1], parts[-1]
            if local.endswith(f":{port}"):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=12,
                    )
                    killed = True
                    if self.log:
                        self.log.info(f"Listener auf Port {port} (PID {pid}) beendet.")
                except Exception:
                    pass
        return killed

    def stop_bridge(self) -> None:
        with self._lock:
            proc = self._bridge_proc
            self._bridge_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            if self.log:
                self.log.info("Bridge gestoppt.")

    # ------------------------------------------------------------------
    # Modelle / Gateway
    # ------------------------------------------------------------------
    def list_models(self) -> list[str]:
        try:
            m = self._http_get_json(f"{config.BRIDGE_URL}/models", timeout=8)
            if isinstance(m, dict):
                return [x.get("id") for x in (m.get("data") or []) if isinstance(x, dict)]
        except Exception as exc:
            if self.log:
                self.log.warn(f"Modellliste fehlgeschlagen: {exc}")
        return []

    # ------------------------------------------------------------------
    # Projekt
    # ------------------------------------------------------------------
    def scan_project(self, path: str) -> dict:
        return views.scan_project(
            path,
            ignore_dirs=self.cfg.get("ignore_dirs"),
            max_files=self.cfg.get("max_project_files", 4000),
        )

    # ------------------------------------------------------------------
    # Codex ausfuehren
    # ------------------------------------------------------------------
    def available(self) -> bool:
        return bool(config.CODEX_BIN) and Path(config.CODEX_BIN).exists()

    def _build_cmd(self, conv: Conversation, model: str, token: str) -> list[str]:
        pid = self.cfg.get("provider_id", config.PROVIDER_ID)
        bridge = f"http://{self.cfg.get('bridge_host', config.BRIDGE_HOST)}:{self.cfg.get('bridge_port', config.BRIDGE_PORT)}/v1"
        args = [config.CODEX_BIN, "exec"]
        if conv.session_id:
            args += ["resume", conv.session_id]
        args += [
            "--json",
            "-m", model,
            "-C", conv.project_path or str(Path.cwd()),
            "-s", self.cfg.get("sandbox", "workspace-write"),
            "--output-last-message", self._last_msg_file(conv),
        ]
        if self.cfg.get("skip_git_repo_check", True):
            args += ["--skip-git-repo-check"]
        # Provider auf die Bridge umleiten + Token durchreichen (kein config.toml noetig)
        args += ["-c", f'model_providers.{pid}.base_url="{bridge}"']
        args += ["-c", f'model_providers.{pid}.wire_api="responses"']
        if token:
            args += ["-c", f'model_providers.{pid}.http_headers.Authorization="Bearer {token}"']
        return args

    def _last_msg_file(self, conv: Conversation) -> str:
        data_dir = config.DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / f"lastmsg-{conv.id}.txt")

    def run_turn(
        self,
        conv: Conversation,
        prompt: str,
        model: str,
        token: str,
        push,
    ) -> subprocess.Popen | None:
        """Startet einen Codex-Turn. Nicht blockierend: arbeitet in Threads
        und meldet Ergebnisse ueber ``push``. Gibt den Prozess zurueck."""
        if not self.available():
            push(("error", "codex ist nicht verfuegbar - Pfad pruefen."))
            return None
        if not conv.project_path or not Path(conv.project_path).exists():
            push(("error", "Kein gueltiges Projekt-Verzeichnis ausgewaehlt."))
            return None

        cmd = self._build_cmd(conv, model, token)
        if self.log:
            self.log.info("codex: " + " ".join(cmd[:6]) + " ...")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=conv.project_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception as exc:
            push(("error", f"codex konnte nicht gestartet werden: {exc}"))
            return None

        push(("status", "Codex arbeitet ..."))

        # Gemeinsamer Zustand fuer die Lesethreads
        state = {
            "session_id": conv.session_id,
            "live_parts": [],
            "tool_msgs": [],
            "lock": threading.Lock(),
        }

        def feed_stdin():
            try:
                if prompt:
                    proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except Exception:
                pass

        threading.Thread(target=feed_stdin, daemon=True).start()
        threading.Thread(
            target=self._pump_stdout, args=(proc, push, state, conv), daemon=True
        ).start()
        threading.Thread(
            target=self._pump_stderr, args=(proc, push, state, conv), daemon=True
        ).start()
        return proc

    def _pump_stdout(self, proc, push, state, conv: Conversation) -> None:
        session_id_holder = state
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self._handle_event(ev, push, state, conv)
        except Exception:
            pass
        finally:
            self._finish_turn(proc, push, state, conv)

    def _pump_stderr(self, proc, push, state, conv: Conversation) -> None:
        try:
            buf = []
            for raw in proc.stderr:
                buf.append(raw.decode("utf-8", "replace"))
            text = "".join(buf).strip()
            if text:
                # Nur bei Fehler/Exit melden; Codex schreibt Diagnostik nach stderr
                if proc.returncode not in (0, None):
                    push(("error", text[-1500:]))
                elif self.log:
                    self.log.debug("codex stderr: " + text[:500])
        except Exception:
            pass

    def _handle_event(self, ev: dict, push, state, conv: Conversation) -> None:
        if not isinstance(ev, dict):
            return
        # Session/Thread-ID merken (Resume fuer Folge-Turns)
        for key in ("thread_id", "session_id", "id"):
            if key in ev and isinstance(ev[key], str) and ev[key]:
                with state["lock"]:
                    if not state["session_id"]:
                        state["session_id"] = ev[key]

        etype = ev.get("type", "")

        # -- Text extrahieren (best effort ueber mehrere bekannte Formen) --
        text = self._extract_text(ev)
        if text:
            with state["lock"]:
                state["live_parts"].append(text)
            push(("text", text))

        # -- Werkzeug-Aufrufe --
        tool = self._extract_tool(ev)
        if tool:
            name, detail = tool
            with state["lock"]:
                state["tool_msgs"].append(
                    ChatMessage(role="tool", content=detail, kind="tool", tool_name=name)
                )
            push(("tool", name, detail))

        # -- Fehler --
        if etype in ("error", "turn.failed", "session.failed"):
            msg = ev.get("message") or ev.get("error") or json.dumps(ev, ensure_ascii=False)
            push(("error", str(msg)[:1500]))

    @staticmethod
    def _extract_text(ev: dict) -> str:
        # Explizite Felder
        for k in ("text", "delta", "output_text"):
            v = ev.get(k)
            if isinstance(v, str) and v:
                return v
        # content-Array (Codex-Nachrichtenform)
        content = ev.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("output_text")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "".join(parts)
        return ""

    @staticmethod
    def _extract_tool(ev: dict) -> tuple[str, str] | None:
        etype = ev.get("type", "")
        if "tool" in etype or etype in ("exec_command", "shell", "function_call"):
            name = ev.get("name") or ev.get("tool_name") or ev.get("tool") or "tool"
            detail = ev.get("arguments") or ev.get("input") or ev.get("command") or ""
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False)
            return (str(name), detail[:2000])
        if "tool_calls" in ev and isinstance(ev["tool_calls"], list):
            parts = []
            for tc in ev["tool_calls"]:
                if isinstance(tc, dict):
                    parts.append(f"{tc.get('name','?')}({tc.get('arguments','')})")
            if parts:
                return ("tool_call", "\n".join(parts)[:2000])
        return None

    def _finish_turn(self, proc, push, state, conv: Conversation) -> None:
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        rc = proc.returncode

        # Finale Antwort aus der Datei (zuverlaessigste Quelle)
        final_text = ""
        try:
            p = Path(self._last_msg_file(conv))
            if p.exists():
                final_text = p.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            pass

        with state["lock"]:
            session_id = state["session_id"]
            live = "".join(state["live_parts"])
            tool_msgs = list(state["tool_msgs"])

        if not final_text and live:
            final_text = live

        # Gespraech aktualisieren + speichern
        if session_id:
            conv.session_id = session_id
        if tool_msgs:
            for tm in tool_msgs:
                conv.add(tm)
        if final_text:
            conv.add(ChatMessage(role="assistant", content=final_text, kind="message"))
        else:
            conv.add(
                ChatMessage(
                    role="assistant",
                    content="(keine Antwort erhalten)",
                    kind="error",
                )
            )
        try:
            self.store.upsert_conversation(conv)
        except Exception as exc:
            if self.log:
                self.log.error(f"Speichern fehlgeschlagen: {exc}")

        err = "" if rc in (0, None) else f"codex exit code {rc}"
        push(("status", "Bereit."))
        push(("done", session_id, err))

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------
    @staticmethod
    def _http_get_json(url: str, timeout: float = 8):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", "replace")
        return json.loads(data)
