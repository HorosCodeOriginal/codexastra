"""app.py - CodexAstra Desktop (Tkinter, dunkles Theme, kein Terminal).

Bettet die Bridge + codex in eine native Windows-Oberflaeche ein:
  - Gateway-Status-Anzeige (lebendig, per Hintergrund-Thread)
  - Modell-Auswahl (aus dem Gateway geladen)
  - Projekt-Browser mit nativem Ordner-Dialog
  - Chat-Verlauf (mehrere Gespraeche) mit Live-Streaming
  - Zwei Modi: einfach (nur Chat + Projekt) und experte (zusaetzlich Logs,
    Gateway-Details, Bridge-Steuerung)

Wichtig: Widgets werden NUR im Haupt-Thread angefasst. Hintergrund-Threads
(codex, Bridge, Gateway-Monitor) schieben Ereignisse ueber eine Queue, die
hier in ``_poll`` abgearbeitet wird.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import config, exporter, logging_utils, models, service, store, views

# -- Farben (dunkles Theme) -------------------------------------------------
BG = "#1e1e1e"
PANEL = "#252526"
PANEL2 = "#2d2d30"
FG = "#e6e6e6"
MUTED = "#9aa0a6"
BTN = "#3a3d41"
BTN_HOVER = "#45494e"
ACCENT = "#4ec9b0"
OK = "#6a9955"
WARN = "#dcdcaa"
ERROR = "#f48771"
USER = "#569cd6"
ASSIST = "#ce9178"
TOOL = "#c586c0"
SEP = "#3c3c3c"

FONT = ("Segoe UI", 11)
MONO = ("Consolas", 10)


class App:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.log = logging_utils.AppLog(config.EVENTS_PATH)
        self.store = store.Store(config.CONVERSATIONS_PATH, config.PROJECTS_PATH, self.log)
        self.service = service.Service(cfg, self.log, self.store)

        self.q: "queue.Queue" = queue.Queue()
        self.conversations: list[models.Conversation] = []
        self.active: models.Conversation | None = None
        self.project_tree_data: dict = {}
        self.models: list[str] = []
        self._streaming = False
        self._gw_fail = 0
        self._poll_count = 0

        self._apply_theme()
        self._build()
        self._load_state()

        # Hintergrund-Monitor fuer Gateway-Status + Modelle
        threading.Thread(target=self._monitor, daemon=True).start()
        self.root.after(250, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ==================================================================
    # Theme / Layout
    # ==================================================================
    def _apply_theme(self) -> None:
        self.root.configure(bg=BG)
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("TFrame", background=PANEL)
            style.configure("TPanedwindow", background=BG)
            style.configure("TLabel", background=PANEL, foreground=FG)
            style.configure("TButton", background=BTN, foreground=FG,
                            borderwidth=0, relief="flat", padding=4)
            style.map("TButton", background=[("active", BTN_HOVER)])
            style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                            foreground=FG, borderwidth=0)
            style.configure("TNotebook", background=BG, borderwidth=0)
            style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                            padding=[8, 4])
            style.map("TNotebook.Tab", background=[("selected", PANEL2)],
                      foreground=[("selected", FG)])
            style.configure("Treeview", background=PANEL2, foreground=FG,
                            fieldbackground=PANEL2, borderwidth=0)
            style.configure("Treeview.Heading", background=PANEL, foreground=MUTED)
            style.map("Treeview", background=[("selected", "#094771")])
            style.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG)
        except Exception:
            pass

    def _build(self) -> None:
        root = self.root
        root.title(f"{config.APP_NAME} {config.APP_VERSION}")
        geom = self.cfg["ui"].get("window_geometry", "1320x820")
        try:
            root.geometry(geom)
        except tk.TclError:
            root.geometry("1320x820")
        root.minsize(900, 600)

        # -- Toolbar (Zeile 0) --
        tb = tk.Frame(root, bg=PANEL, height=46)
        tb.grid(row=0, column=0, columnspan=3, sticky="ew")
        tb.grid_propagate(False)

        self._tb_label = tk.Label(tb, text=f"{config.APP_NAME}", bg=PANEL, fg=ACCENT,
                                  font=("Segoe UI", 13, "bold"))
        self._tb_label.pack(side="left", padx=(12, 8))

        # Gateway-Status-Pille
        self.gw_dot = tk.Label(tb, text="●", bg=PANEL, fg=MUTED, font=("Segoe UI", 14))
        self.gw_dot.pack(side="left", padx=(0, 2))
        self.gw_label = tk.Label(tb, text="Gateway: …", bg=PANEL, fg=FG, font=FONT)
        self.gw_label.pack(side="left", padx=(0, 10))

        # Modell-Auswahl
        tk.Label(tb, text="Modell:", bg=PANEL, fg=MUTED, font=FONT).pack(side="left", padx=(0, 4))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(tb, textvariable=self.model_var, width=22,
                                        state="readonly", font=FONT)
        self.model_combo.pack(side="left", padx=(0, 10))
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        # Modus-Umschalter
        self.mode_btn = tk.Button(tb, text="Modus: einfach", command=self._toggle_mode,
                                  bg=BTN, fg=FG, relief="flat", font=FONT, padx=8)
        self.mode_btn.pack(side="right", padx=(6, 10))

        # -- Hauptbereich (Zeile 1) --
        # Linke Spalte: Gespraeche + Projekt
        self.left = tk.Frame(root, bg=PANEL)
        self.left.grid(row=1, column=0, sticky="nsew")
        self._build_left(self.left)

        # Mittlere Spalte: Chat
        self.center = tk.Frame(root, bg=BG)
        self.center.grid(row=1, column=1, sticky="nsew")
        self._build_chat(self.center)

        # Rechte Spalte (experte): Logs + Gateway-Details
        self.right = tk.Frame(root, bg=PANEL)
        self.right.grid(row=1, column=2, sticky="nsew")
        self._build_expert(self.right)

        # -- Prompt (Zeile 2) --
        self._build_prompt()

        # -- Statusleiste (Zeile 3) --
        self.status_var = tk.StringVar(value="Bereit.")
        sb = tk.Label(root, textvariable=self.status_var, bg=PANEL2, fg=MUTED,
                      font=("Segoe UI", 10), anchor="w", padx=10)
        sb.grid(row=3, column=0, columnspan=3, sticky="ew")

        root.columnconfigure(0, minsize=300, weight=0)
        root.columnconfigure(1, weight=1)
        root.columnconfigure(2, minsize=320, weight=0)
        root.rowconfigure(1, weight=1)

        self._apply_mode()

    def _build_left(self, parent: tk.Frame) -> None:
        # --- Gespraeche ---
        f1 = tk.LabelFrame(parent, text="Gespraeche", bg=PANEL, fg=MUTED,
                           font=("Segoe UI", 10, "bold"), bd=0)
        f1.pack(side="top", fill="both", expand=False, padx=8, pady=(8, 4))
        f1.configure(height=200)
        self.conv_list = tk.Listbox(f1, bg=PANEL2, fg=FG, font=FONT,
                                    selectbackground="#094771", borderwidth=0,
                                    highlightthickness=0)
        self.conv_list.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        self.conv_list.bind("<<ListboxSelect>>", self._on_conv_select)
        row = tk.Frame(f1, bg=PANEL)
        row.pack(side="top", fill="x", padx=4, pady=(0, 4))
        tk.Button(row, text="Neu", command=self._new_chat, bg=BTN, fg=FG,
                  relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(row, text="Laden", command=self._load_selected, bg=BTN, fg=FG,
                  relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(row, text="Löschen", command=self._delete_conv, bg=BTN, fg=ERROR,
                  relief="flat", font=FONT).pack(side="left", fill="x", expand=True, padx=2)

        # --- Projekt ---
        f2 = tk.LabelFrame(parent, text="Projekt", bg=PANEL, fg=MUTED,
                           font=("Segoe UI", 10, "bold"), bd=0)
        f2.pack(side="top", fill="both", expand=True, padx=8, pady=(4, 8))
        tk.Button(f2, text="📁 Projekt öffnen…", command=self._pick_project,
                  bg=BTN, fg=FG, relief="flat", font=FONT).pack(side="top", fill="x",
                                                                padx=4, pady=4)
        self.proj_label = tk.Label(f2, text="(kein Projekt)", bg=PANEL, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w", wraplength=280)
        self.proj_label.pack(side="top", fill="x", padx=6, pady=(0, 4))
        self.tree = ttk.Treeview(f2, show="tree", height=12)
        self.tree.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<Double-1>", self._on_tree_double)

    def _build_chat(self, parent: tk.Frame) -> None:
        self.chat = tk.Text(parent, bg=BG, fg=FG, font=FONT, wrap="word",
                            state="disabled", relief="flat", padx=12, pady=12,
                            insertbackground=FG)
        self.chat.pack(side="top", fill="both", expand=True)
        self.chat.tag_config("user", foreground=USER)
        self.chat.tag_config("assistant", foreground=ASSIST)
        self.chat.tag_config("tool", foreground=TOOL)
        self.chat.tag_config("error", foreground=ERROR)
        self.chat.tag_config("system", foreground=MUTED)
        self.chat.tag_config("status", foreground=ACCENT)
        self.chat.tag_config("bold", foreground=FG, font=("Segoe UI", 11, "bold"))
        sb = ttk.Scrollbar(parent, command=self.chat.yview)
        sb.pack(side="right", fill="y")
        self.chat.configure(yscrollcommand=sb.set)

    def _build_prompt(self) -> None:
        f = tk.Frame(self.root, bg=PANEL2, height=96)
        f.grid(row=2, column=0, columnspan=3, sticky="ew")
        f.grid_propagate(False)
        self.prompt = tk.Text(f, bg=PANEL2, fg=FG, font=FONT, wrap="word",
                              relief="flat", padx=10, pady=8, height=3,
                              insertbackground=FG)
        self.prompt.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=6)
        self.prompt.bind("<Return>", self._on_prompt_key)
        self.prompt.bind("<Shift-Return>", lambda e: None)
        self.send_btn = tk.Button(f, text="Senden\n⏎", command=self._on_send,
                                  bg=ACCENT, fg="#06231d", relief="flat",
                                  font=("Segoe UI", 11, "bold"), width=10)
        self.send_btn.pack(side="right", fill="y", padx=(4, 8), pady=6)
        self.prompt.insert("1.0", "Frag Codex etwas - er kann Dateien anlegen und Code ausführen…")

    def _build_expert(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Gateway-Details", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="top", anchor="w",
                                                      padx=10, pady=(10, 4))
        self.gw_detail = tk.Text(parent, bg=PANEL2, fg=FG, font=MONO, wrap="word",
                                 height=8, relief="flat", state="disabled", padx=8, pady=6)
        self.gw_detail.pack(side="top", fill="x", padx=8, pady=(0, 8))

        tk.Label(parent, text="Verfügbare Modelle", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="top", anchor="w",
                                                      padx=10, pady=(4, 2))
        self.model_listbox = tk.Listbox(parent, bg=PANEL2, fg=FG, font=MONO,
                                        selectbackground="#094771", borderwidth=0,
                                        highlightthickness=0)
        self.model_listbox.pack(side="top", fill="both", expand=True, padx=8, pady=4)

        tk.Label(parent, text="Protokoll (Live)", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="top", anchor="w",
                                                      padx=10, pady=(4, 2))
        self.logbox = tk.Text(parent, bg=PANEL2, fg=MUTED, font=MONO, wrap="word",
                              height=10, relief="flat", state="disabled", padx=8, pady=6)
        self.logbox.pack(side="top", fill="both", expand=True, padx=8, pady=4)

        row = tk.Frame(parent, bg=PANEL)
        row.pack(side="top", fill="x", padx=8, pady=(4, 8))
        tk.Button(row, text="Bridge neu starten", command=self._restart_bridge,
                  bg=BTN, fg=FG, relief="flat", font=FONT).pack(side="left", fill="x",
                                                               expand=True, padx=2)
        tk.Button(row, text="Export Markdown", command=self._export,
                  bg=BTN, fg=FG, relief="flat", font=FONT).pack(side="left", fill="x",
                                                               expand=True, padx=2)

    # ==================================================================
    # State laden
    # ==================================================================
    def _load_state(self) -> None:
        self.conversations = self.store.load_conversations()
        if not self.conversations:
            self.active = models.new_conversation(self.cfg.get("default_model", config.DEFAULT_MODEL))
            self.conversations.append(self.active)
        else:
            self.active = self.conversations[0]
        self._refresh_conv_list()
        self._refresh_chat()
        self._set_project_label()
        if self.active.project_path:
            self._scan_project(self.active.project_path)
        # Modell vorbelegen
        saved = self.active.model or self.cfg.get("default_model", "")
        if saved:
            self.model_var.set(saved)

    # ==================================================================
    # Modus
    # ==================================================================
    def _apply_mode(self) -> None:
        mode = self.cfg["ui"].get("mode", "simple")
        if mode == "expert":
            self.right.grid()
            self.mode_btn.configure(text="Modus: experte")
        else:
            self.right.grid_remove()
            self.mode_btn.configure(text="Modus: einfach")

    def _toggle_mode(self) -> None:
        cur = self.cfg["ui"].get("mode", "simple")
        self.cfg["ui"]["mode"] = "expert" if cur == "simple" else "simple"
        self._apply_mode()

    # ==================================================================
    # Gespraeche
    # ==================================================================
    def _refresh_conv_list(self) -> None:
        self.conv_list.delete(0, tk.END)
        for c in sorted(self.conversations, key=lambda x: x.updated, reverse=True):
            title = c.title if c.title != "Neues Gespraech" else views.conversation_preview(c)
            self.conv_list.insert(tk.END, title[:60])

    def _new_chat(self) -> None:
        self.active = models.new_conversation(
            self.model_var.get() or self.cfg.get("default_model", config.DEFAULT_MODEL),
            self.active.project_path if self.active else "",
        )
        self.conversations.append(self.active)
        self.store.upsert_conversation(self.active)
        self._refresh_conv_list()
        self._refresh_chat()
        self._set_project_label()
        if self.active.project_path:
            self._scan_project(self.active.project_path)

    def _on_conv_select(self, _e=None) -> None:
        sel = self.conv_list.curselection()
        if not sel:
            return
        idx = sel[0]
        ordered = sorted(self.conversations, key=lambda x: x.updated, reverse=True)
        if 0 <= idx < len(ordered):
            self.active = ordered[idx]
            self._refresh_chat()
            self._set_project_label()
            self.model_var.set(self.active.model or "")
            if self.active.project_path:
                self._scan_project(self.active.project_path)

    def _load_selected(self) -> None:
        self._on_conv_select()

    def _delete_conv(self) -> None:
        if not self.active:
            return
        if not messagebox.askyesno("Löschen", f"Gespraech wirklich löschen?\n\n{self.active.title}"):
            return
        self.store.delete_conversation(self.active.id)
        self.conversations = [c for c in self.conversations if c.id != self.active.id]
        self.active = self.conversations[0] if self.conversations else \
            models.new_conversation(self.cfg.get("default_model", config.DEFAULT_MODEL))
        self._refresh_conv_list()
        self._refresh_chat()

    # ==================================================================
    # Projekt
    # ==================================================================
    def _pick_project(self) -> None:
        initial = self.active.project_path if (self.active and self.active.project_path) else str(Path.home())
        path = filedialog.askdirectory(title="Projekt-Ordner wählen", initialdir=initial,
                                      mustexist=True)
        if not path:
            return
        if self.active is None:
            self._new_chat()
        self.active.project_path = path
        self.active.title = Path(path).name
        self.store.upsert_conversation(self.active)
        self._set_project_label()
        self._scan_project(path)
        self.store.add_project(models.Project(path=path))
        self.log.info(f"Projekt geoeffnet: {path}")

    def _set_project_label(self) -> None:
        if self.active and self.active.project_path:
            self.proj_label.configure(text=self.active.project_path, fg=FG)
        else:
            self.proj_label.configure(text="(kein Projekt gewählt)", fg=MUTED)

    def _scan_project(self, path: str) -> None:
        self.project_tree_data = self.service.scan_project(path)
        self._fill_tree("", self.project_tree_data)
        self.status_var.set(f"Projekt gescannt: {views.project_file_count(self.project_tree_data)} Elemente")

    def _fill_tree(self, parent: str, node: dict) -> None:
        self.tree.delete(*self.tree.get_children())
        if not node:
            return

        def add(parent_iid, n):
            label = n["name"]
            iid = self.tree.insert(parent_iid, "end", text=label, open=False)
            if n.get("is_dir"):
                for ch in n.get("children", []):
                    add(iid, ch)

        add("", node)

    def _on_tree_double(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])
        path = None
        # Pfad ueber den Knotennamen rekonstruieren (Treeview ohne Pfad-Speicher)
        # Wir suchen ueber die Projekt-Wurzel.
        if self.project_tree_data:
            found = self._find_path(self.project_tree_data, item["text"])
            if found:
                path = found
        if path and Path(path).is_file():
            try:
                contents = Path(path).read_text(encoding="utf-8", errors="replace")
                top = tk.Toplevel(self.root)
                top.title(item["text"])
                top.configure(bg=BG)
                txt = tk.Text(top, bg=BG, fg=FG, font=MONO, wrap="none")
                txt.pack(fill="both", expand=True)
                txt.insert("1.0", contents[:20000])
                txt.configure(state="disabled")
                top.geometry("700x500")
            except Exception as exc:
                self.log.warn(f"Datei nicht lesbar: {exc}")

    def _find_path(self, node: dict, name: str, trail: str = "") -> str | None:
        cur = (trail + "/" + node["name"]) if trail else node["name"]
        if node["name"] == name and not node.get("is_dir"):
            return node["path"]
        for ch in node.get("children", []):
            r = self._find_path(ch, name, cur)
            if r:
                return r
        return None

    # ==================================================================
    # Chat / Senden
    # ==================================================================
    def _on_prompt_key(self, event=None) -> None:
        # Enter sendet, Shift+Enter = neue Zeile
        if event and event.state & 0x1:  # Shift
            return None
        self._on_send()
        return "break"

    def _on_send(self) -> None:
        if self._streaming:
            return
        text = self.prompt.get("1.0", "end").strip()
        if not text:
            return
        if self.active is None:
            self._new_chat()
        # Platzhalter entfernen
        if text.startswith("Frag Codex etwas"):
            text = ""
        if not text:
            return
        self.prompt.delete("1.0", "end")
        self.active.add(models.ChatMessage(role="user", content=text, kind="message"))
        self.store.upsert_conversation(self.active)
        self._refresh_chat()
        self._refresh_conv_list()

        self._streaming = True
        self.send_btn.configure(state="disabled", text="…")
        model = self.model_var.get() or self.cfg.get("default_model", config.DEFAULT_MODEL)
        self.active.model = model
        token = self.cfg.get("token", "")
        self.service.run_turn(self.active, text, model, token, self._push)

    def _push(self, event) -> None:
        self.q.put(event)

    def _refresh_chat(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        if not self.active:
            self.chat.configure(state="disabled")
            return
        for m in self.active.messages:
            tag = m.kind if m.kind in ("tool", "error", "system") else m.role
            prefix = {
                "user": "👤 Du\n",
                "assistant": "🤖 Codex\n",
                "tool": f"🔧 {m.tool_name}\n",
                "error": "⚠️ Fehler\n",
                "system": "",
            }.get(tag, "")
            self.chat.insert("end", prefix, "bold")
            self.chat.insert("end", (m.content.strip() + "\n\n"), tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    # Chat live waehrend des Streamings direkt ergaenzen
    def _chat_append(self, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    # ==================================================================
    # Ereignis-Verarbeitung (Haupt-Thread)
    # ==================================================================
    def _poll(self) -> None:
        try:
            while True:
                ev = self.q.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self._poll_count += 1
        if self._poll_count % 4 == 0:
            self._refresh_logbox()
        self.root.after(250, self._poll)

    def _handle_event(self, ev) -> None:
        if not isinstance(ev, tuple):
            return
        kind = ev[0]
        if kind == "status":
            self.status_var.set(ev[1])
        elif kind == "text":
            if not self._streaming:
                self._streaming = True
            self._chat_append(ev[1], "assistant")
        elif kind == "tool":
            self._chat_append(f"\n🔧 {ev[1]}\n{ev[2]}\n", "tool")
        elif kind == "error":
            self._chat_append(f"\n⚠️ {ev[1]}\n", "error")
            self.status_var.set("Fehler - siehe Chat.")
        elif kind == "done":
            self._streaming = False
            self.send_btn.configure(state="normal", text="Senden\n⏎")
            self._refresh_conv_list()
            if ev[2]:
                self.log.warn(f"Turn mit Fehler beendet: {ev[2]}")
        elif kind == "gw":
            self._update_gw(ev[1])
        elif kind == "models":
            self._update_models(ev[1])

    # ==================================================================
    # Gateway-Monitor (Hintergrund)
    # ==================================================================
    def _monitor(self) -> None:
        # Erst Bridge sicherstellen
        try:
            self.service.ensure_bridge(timeout=8)
        except Exception as exc:
            self.log.warn(f"Bridge-Init: {exc}")
        while True:
            try:
                health = self.service.bridge_health()
                self.q.put(("gw", health))
                models_list = self.service.list_models()
                if models_list:
                    self.q.put(("models", models_list))
            except Exception:
                pass
            time.sleep(5)

    def _update_gw(self, health: dict) -> None:
        if health.get("bridge_up") and health.get("gateway_ok"):
            self.gw_dot.configure(fg=OK)
            self.gw_label.configure(text=f"Gateway: OK ({len(health.get('models', []))} Modelle)")
            self._gw_fail = 0
        elif health.get("bridge_up"):
            self.gw_dot.configure(fg=WARN)
            self.gw_label.configure(text="Gateway: Bridge OK, Gateway nicht erreichbar")
        else:
            self.gw_dot.configure(fg=ERROR)
            self.gw_label.configure(text="Gateway: getrennt")
            self._gw_fail += 1
        # Detail-Panel (experte)
        self.gw_detail.configure(state="normal")
        self.gw_detail.delete("1.0", "end")
        self.gw_detail.insert("end",
            f"Bridge:  {config.BRIDGE_URL}\n"
            f"Gateway: {self.cfg.get('gateway_url', config.GATEWAY_URL)}\n"
            f"bridge_up:   {health.get('bridge_up')}\n"
            f"gateway_ok:  {health.get('gateway_ok')}\n"
            f"modelle:     {len(health.get('models', []))}\n"
            f"fehler:      {health.get('error', '-')}\n")
        self.gw_detail.configure(state="disabled")

    def _update_models(self, models_list: list[str]) -> None:
        self.models = models_list
        self.model_listbox.delete(0, tk.END)
        for m in models_list:
            self.model_listbox.insert(tk.END, m)
        # Combobox befuellen (nur wenn noch nicht gesetzt)
        cur = self.model_var.get()
        vals = list(self.model_combo["values"] or [])
        if not vals:
            self.model_combo["values"] = models_list
        if not cur and models_list:
            default = self.cfg.get("default_model", "")
            pick = default if default in models_list else models_list[0]
            self.model_var.set(pick)
            if self.active:
                self.active.model = pick

    def _on_model_change(self, _e=None) -> None:
        if self.active:
            self.active.model = self.model_var.get()
            self.store.upsert_conversation(self.active)

    def _refresh_logbox(self) -> None:
        recent = self.log.recent(120)
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        for e in recent:
            self.logbox.insert("end", f"[{e['ts']}] {e['level']}: {e['msg']}\n")
        self.logbox.configure(state="disabled")
        self.logbox.see("end")

    # ==================================================================
    # Bridge / Export
    # ==================================================================
    def _restart_bridge(self) -> None:
        self.service.stop_bridge()
        ok = self.service.ensure_bridge(timeout=8)
        self.status_var.set("Bridge neu gestartet." if ok else "Bridge-Start fehlgeschlagen.")

    def _export(self) -> None:
        if not self.conversations:
            return
        path = filedialog.asksaveasfilename(
            title="Gespraech exportieren", defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Alle Dateien", "*.*")],
            initialfile=f"codexastra-export.md")
        if not path:
            return
        if len(self.conversations) == 1:
            out = exporter.export_conversation(self.conversations[0], path)
        else:
            out = exporter.export_all_markdown(self.conversations, path)
        self.status_var.set(f"Exportiert: {out}")
        self.log.info(f"Export: {out}")

    # ==================================================================
    # Schliessen
    # ==================================================================
    def _on_close(self) -> None:
        try:
            self.cfg["ui"]["window_geometry"] = self.root.geometry()
            config.save_config(self.cfg)
        except Exception:
            pass
        try:
            self.service.stop_bridge()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    cfg = config.load_config()
    root = tk.Tk()
    app = App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
