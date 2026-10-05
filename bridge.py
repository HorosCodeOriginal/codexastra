"""bridge.py - Responses-API-Übersetzer zwischen Codex CLI und einem Gateway,
das Namespaces nicht auflöst.

Problem (Codex 0.160 gegen ein lokales Relay, verifiziert 2026-10-05):
Codex schickt seine Tools nicht flach, sondern als Input-Item
``type:"additional_tools"`` mit verschachtelten ``type:"namespace"``-Blöcken::

    {"type":"additional_tools","role":"developer","tools":[
      {"type":"namespace","name":"functions","tools":[
        {"type":"custom","name":"exec","description":"Run JavaScript code ..."},
        {"type":"function","name":"wait"}, ...]},
      {"type":"namespace","name":"clock","tools":[{"type":"function","name":"sleep"}]},
      ...]}

Ein Gateway, das Namespaces nicht kennt, reicht das roh ans Modell weiter.
Das Modell benennt den Call dann nach dem Namespace -> der Codex-Router
meldet ``unsupported call: functions``.

Was diese Bridge tut:
  1. ``additional_tools``-Items einsammeln und die Namespaces flachklopfen.
     Der Router löst über den LEAF-Namen auf (``exec``), nicht über
     ``functions.exec`` und nicht über ``functions__exec``.
  2. ``type:"custom"`` (das ist ``exec``, es nimmt rohes JavaScript) wird als
     Funktion mit dem einen String-Feld ``input`` angeboten - das ``custom``-
     Format wird vom Gateway verworfen (getestet: Modell verweigert dann).
  3. Die Antwort wird durchgereicht und dabei ``{"input": "<js>"}`` wieder zu
     rohem JavaScript entpackt, denn Codex erwartet für ``exec`` den reinen
     Quelltext, kein JSON.

Aufruf:  python bridge.py --port 20127 --upstream http://localhost:20128/v1
"""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Konfiguration (wird von main() gesetzt)
# --------------------------------------------------------------------------
UPSTREAM = "http://localhost:20128/v1"
LOG_PATH = None
VERBOSE = False
DUMP_DIR = None
_dump_seq = 0
_stats_lock = threading.Lock()
STATS = {"requests": 0, "flattened": 0, "rewritten": 0, "errors": 0}


def dump(kind: str, data: bytes) -> None:
    """Nur mit --dump: Request/Response zur Diagnose mitschreiben."""
    global _dump_seq
    if not DUMP_DIR:
        return
    try:
        import os
        os.makedirs(DUMP_DIR, exist_ok=True)
        with _stats_lock:
            _dump_seq += 1
            seq = _dump_seq
        path = os.path.join(DUMP_DIR, f"{seq:03d}-{kind}.txt")
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError:
        pass

# Reihenfolge, in der ein JSON-Argument nach dem rohen Skript abgesucht wird.
JS_KEYS = ("input", "code", "js", "javascript", "script", "source", "source_text")


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    if VERBOSE:
        try:
            print(line, flush=True)
        except Exception:
            pass
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------
# Werkzeuge flachklopfen
# --------------------------------------------------------------------------
def _flatten_one(tool: dict, namespace: str | None) -> dict | None:
    """Ein verschachteltes Tool in ein flaches Responses-Tool übersetzen."""
    name = tool.get("name")
    if not name:
        return None
    desc = tool.get("description") or ""
    if namespace:
        desc = f"[{namespace}] {desc}".strip()

    if tool.get("type") == "custom":
        # ``exec``: nimmt rohen JavaScript-Quelltext. Als Funktion mit genau
        # einem String-Feld anbieten - sonst sieht das Modell kein Tool.
        return {
            "type": "function",
            "name": name,
            "description": (
                desc
                + "\n\nArgument: liefere den rohen JavaScript-Quelltext im Feld `input`."
                  " Kein JSON, keine Anführungszeichen, keine Markdown-Zäune."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Roher JavaScript-Quelltext (nicht JSON, nicht Markdown).",
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        }

    params = tool.get("parameters") or {"type": "object", "properties": {}}
    return {"type": "function", "name": name, "description": desc, "parameters": params}


def flatten_tools(input_items: list) -> tuple[list, list]:
    """``additional_tools``-Items entfernen und flache Tools zurückgeben."""
    kept: list = []
    flat: list = []
    seen: set[str] = set()

    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            kept.append(item)
            continue
        for top in item.get("tools") or []:
            if not isinstance(top, dict):
                continue
            if top.get("type") == "namespace":
                ns_name = top.get("name")
                for nested in top.get("tools") or []:
                    conv = _flatten_one(nested, ns_name)
                    if conv and conv["name"] not in seen:
                        seen.add(conv["name"])
                        flat.append(conv)
            else:
                conv = _flatten_one(top, None)
                if conv and conv["name"] not in seen:
                    seen.add(conv["name"])
                    flat.append(conv)
    return kept, flat


# --------------------------------------------------------------------------
# Antwort umschreiben: {"input": "<js>"} -> rohes JavaScript
# --------------------------------------------------------------------------
def unwrap_js(args) -> str | None:
    """Wenn ``args`` JSON mit einem JS-Feld ist, den reinen Quelltext liefern."""
    if not isinstance(args, str):
        return None
    stripped = args.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in JS_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _rewrite_node(node) -> int:
    """Rekursiv alle Tool-Calls im Baum prüfen. Liefert Anzahl Änderungen."""
    changed = 0
    if isinstance(node, dict):
        if node.get("type") in ("function_call", "custom_tool_call") and node.get("name") == "exec":
            raw = unwrap_js(node.get("arguments"))
            if raw is None:
                raw = unwrap_js(node.get("input"))
            if raw is None:
                # Modell hat den Quelltext schon direkt geliefert.
                candidate = node.get("arguments")
                if isinstance(candidate, str) and candidate.strip() and not candidate.lstrip().startswith("{"):
                    raw = candidate
            if raw is not None:
                # Codex registriert `exec` als CUSTOM-Tool: es will rohen
                # JavaScript-Quelltext im Feld `input` eines custom_tool_call.
                # Ein function_call mit JSON-Argumenten endet in
                # "tool exec invoked with incompatible payload".
                node["type"] = "custom_tool_call"
                node["input"] = raw
                node.pop("arguments", None)
                changed += 1
        for value in node.values():
            changed += _rewrite_node(value)
    elif isinstance(node, list):
        for value in node:
            changed += _rewrite_node(value)
    return changed


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _upstream_parts():
    url = urllib.parse.urlsplit(UPSTREAM)
    # "localhost" kann unter Windows auf ::1 (IPv6) aufloesen, waehrend das
    # Gateway nur auf 127.0.0.1 (IPv4) lauscht -> Connection-Fehler/502.
    # Wir erzwingen daher die IPv4-Loopback-Adresse.
    host = url.hostname or "127.0.0.1"
    if host in ("localhost",):
        host = "127.0.0.1"
    port = url.port or (443 if url.scheme == "https" else 80)
    prefix = url.path.rstrip("/")
    return url.scheme, host, port, prefix


def sse_events(resp_obj: dict) -> list[bytes]:
    """Aus einer kompletten Responses-Antwort den SSE-Strom bauen, den Codex
    erwartet. Noetig, weil das Gateway im Streaming-Modus keine Tool-Calls
    liefert (verifiziert 2026-10-05) - non-streaming dagegen schon."""
    seq = 0

    def ev(etype: str, **fields) -> bytes:
        nonlocal seq
        seq += 1
        data = {"type": etype}
        data.update(fields)
        data["sequence_number"] = seq
        return f"event: {etype}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    out: list[bytes] = []
    in_progress = dict(resp_obj, status="in_progress", output=[])
    out.append(ev("response.created", response=in_progress))
    out.append(ev("response.in_progress", response=in_progress))

    for idx, item in enumerate(resp_obj.get("output") or []):
        kind = item.get("type")
        item_id = item.get("id")
        if kind == "function_call":
            out.append(ev("response.output_item.added", output_index=idx,
                          item=dict(item, arguments="", status="in_progress")))
            args = item.get("arguments") or ""
            out.append(ev("response.function_call_arguments.delta", item_id=item_id,
                          output_index=idx, delta=args))
            out.append(ev("response.function_call_arguments.done", item_id=item_id,
                          output_index=idx, arguments=args))
            out.append(ev("response.output_item.done", output_index=idx, item=item))
        elif kind == "custom_tool_call":
            payload = item.get("input") or ""
            out.append(ev("response.output_item.added", output_index=idx,
                          item=dict(item, input="", status="in_progress")))
            out.append(ev("response.custom_tool_call_input.delta", item_id=item_id,
                          output_index=idx, delta=payload))
            out.append(ev("response.custom_tool_call_input.done", item_id=item_id,
                          output_index=idx, input=payload))
            out.append(ev("response.output_item.done", output_index=idx, item=item))
        elif kind == "message":
            out.append(ev("response.output_item.added", output_index=idx,
                          item=dict(item, content=[], status="in_progress")))
            for ci, part in enumerate(item.get("content") or []):
                text = part.get("text", "")
                out.append(ev("response.content_part.added", item_id=item_id, output_index=idx,
                              content_index=ci, part=dict(part, text="")))
                if text:
                    out.append(ev("response.output_text.delta", item_id=item_id, output_index=idx,
                                  content_index=ci, delta=text))
                out.append(ev("response.output_text.done", item_id=item_id, output_index=idx,
                              content_index=ci, text=text))
                out.append(ev("response.content_part.done", item_id=item_id, output_index=idx,
                              content_index=ci, part=part))
            out.append(ev("response.output_item.done", output_index=idx, item=item))
        else:
            out.append(ev("response.output_item.added", output_index=idx, item=item))
            out.append(ev("response.output_item.done", output_index=idx, item=item))

    out.append(ev("response.completed", response=resp_obj))
    return out


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodexAstraBridge/1.0"

    def log_message(self, fmt, *args):  # stderr-Lärm unterdrücken
        if VERBOSE:
            log("http " + (fmt % args))

    # -- Hilfen ------------------------------------------------------------
    def _send_json(self, code: int, obj) -> None:
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _rewrite_location(self, location: str) -> str:
        """Upstream-3xx-Ziel auf die Bridge-Adresse umschreiben."""
        try:
            lp = urllib.parse.urlsplit(location)
            if not lp.scheme and not lp.netloc:
                # relativer Pfad: nur Pfad uebernehmen
                return lp.path + (("?" + lp.query) if lp.query else "")
            addr = self.server.server_address  # (host, port)
            # Die Bridge lauscht immer plain HTTP.
            return f"http://{addr[0]}:{addr[1]}{lp.path}" + \
                   (("?" + lp.query) if lp.query else "")
        except Exception:
            return location

    # -- Routen ------------------------------------------------------------
    def do_GET(self):
        if self.path.startswith("/__bridge/health"):
            self._send_json(200, {
                "status": "ok",
                "upstream": UPSTREAM,
                "stats": dict(STATS),
            })
            return
        self._proxy(b"")

    def do_POST(self):
        self._proxy(self._read_body())

    # -- Weiterleitung -----------------------------------------------------
    def _proxy(self, body: bytes) -> None:
        scheme, host, port, prefix = _upstream_parts()
        # Codex setzt seine base_url auf diese Bridge inkl. Basis-Pfad
        # (z.B. http://127.0.0.1:20127/v1). Damit enthaelt self.path den
        # /v1-Anteil bereits - wir leiten also 1:1 weiter und haengen NICHT
        # noch einmal das upstream-Prefix davor (sonst /v1/v1/...).
        # Absolute-Form (Proxy-Stil "POST http://host/path") muss auf den
        # reinen Pfad zurueckgefuehrt werden, sonst entsteht eine
        # Selbst-Referenz-Schleife.
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme:  # absoluter Request-Target
            target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        else:
            target = self.path

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection", "accept-encoding")}
        headers["Host"] = f"{host}:{port}"
        headers["Accept-Encoding"] = "identity"

        is_responses = self.path.rstrip("/").endswith("/responses")
        want_stream = False

        if is_responses and body:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                with _stats_lock:
                    STATS["requests"] += 1
                items = payload.get("input")
                if isinstance(items, list):
                    kept, flat = flatten_tools(items)
                    if flat:
                        payload["input"] = kept
                        existing = payload.get("tools") or []
                        payload["tools"] = existing + flat
                        with _stats_lock:
                            STATS["flattened"] += 1
                        log("flattened %d tools: %s" % (len(flat), ", ".join(t["name"] for t in flat)))
                # WICHTIG: wir erzwingen KEIN non-streaming mehr. Der
                # non-streaming-Pfad des Gateways bricht auf Folge-Turns
                # ("Failed to convert streaming response to JSON") ab. Der
                # Streaming-Pfad liefert die Tool-Calls korrekt - wir leiten
                # den SSE-Strom durch und uebersetzen function_call->custom_tool_call
                # on-the-fly (siehe _stream_rewrite).
                want_stream = bool(payload.get("stream"))
                body = json.dumps(payload).encode()
                dump("request", body)

        headers["Content-Length"] = str(len(body))

        try:
            conn = (http.client.HTTPSConnection(host, port, timeout=600)
                    if scheme == "https" else http.client.HTTPConnection(host, port, timeout=600))
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:  # noqa: BLE001 - an den Client melden
            with _stats_lock:
                STATS["errors"] += 1
            log("upstream error: %r" % (exc,))
            self._send_json(502, {"error": {"message": "bridge: upstream unreachable: %s" % exc}})
            return

        try:
            if is_responses and want_stream:
                # Streaming: Gateway-SSE durchreichen + exec uebersetzen.
                self._stream_rewrite(resp)
                return

            raw = resp.read()
            dump("response", raw)

            out_headers = [(k, v) for k, v in resp.getheaders()
                           if k.lower() not in ("transfer-encoding", "content-length",
                                                "connection", "content-type")]
            # 3xx-Location gegebenenfalls auf die Bridge zurueckbiegen, damit
            # Codex nicht direkt an den Upstream oder in eine Schleife läuft.
            if 300 <= resp.status < 400:
                out_headers = [(k, self._rewrite_location(v))
                               if k.lower() == "location" else (k, v)
                               for k, v in out_headers]
            self.send_response(resp.status)
            for key, value in out_headers:
                self.send_header(key, value)

            obj = None
            if is_responses:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    obj = None
                if obj is not None:
                    changed = _rewrite_node(obj)
                    if changed:
                        with _stats_lock:
                            STATS["rewritten"] += changed

            data = json.dumps(obj, ensure_ascii=False).encode() if obj is not None else raw
            self.send_header("Content-Type", "application/json" if obj is not None
                             else (resp.getheader("Content-Type") or "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            with _stats_lock:
                STATS["errors"] += 1
            log("relay error: %r" % (exc,))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _stream_rewrite(self, resp) -> None:
        """Gateway-SSE an Codex durchreichen und dabei exec-Function-Calls in
        echte custom_tool_calls umschreiben (Codex erwartet rohes JS im Feld
        ``input``, keine JSON-``arguments``)."""
        self.send_response(resp.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        pending_input: dict = {}   # item_id -> rohes JS (aus .done)
        pending_buf: dict = {}     # item_id -> akkumulierte arguments-Deltas

        def emit(event_type: str, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False)
            self._write_chunk(("event: %s\ndata: %s\n\n" % (event_type, data)).encode())

        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                if line.strip() == b"":
                    continue  # SSE-Trenner - wir rekonstruieren die Framing selbst
                if line.startswith(b"event:"):
                    event_type = line[6:].strip().decode(errors="replace")
                    dline = resp.readline()
                elif line.startswith(b"data:"):
                    event_type = None
                    dline = line
                else:
                    continue

                data_obj = None
                if dline.startswith(b"data:"):
                    data_str = dline[5:].strip()
                    try:
                        data_obj = json.loads(data_str)
                    except ValueError:
                        data_obj = None
                et = event_type or (data_obj.get("type") if isinstance(data_obj, dict) else None)

                # ---- exec als function_call -> custom_tool_call ----
                if et == "response.output_item.added" and isinstance(data_obj, dict):
                    item = data_obj.get("item") or {}
                    if item.get("type") == "function_call" and item.get("name") == "exec":
                        item = dict(item)
                        item["type"] = "custom_tool_call"
                        item.pop("arguments", None)
                        item["input"] = ""
                        data_obj["item"] = item
                        emit(et, data_obj)
                        continue
                if et == "response.function_call_arguments.delta" and isinstance(data_obj, dict):
                    iid = data_obj.get("item_id")
                    pending_buf[iid] = pending_buf.get(iid, "") + (data_obj.get("delta") or "")
                    continue  # wir emittieren das Input erst bei .done
                if et == "response.function_call_arguments.done" and isinstance(data_obj, dict):
                    iid = data_obj.get("item_id")
                    raw = unwrap_js(data_obj.get("arguments")) or data_obj.get("arguments") or ""
                    pending_input[iid] = raw
                    continue
                if et == "response.output_item.done" and isinstance(data_obj, dict):
                    item = data_obj.get("item") or {}
                    if item.get("type") == "function_call" and item.get("name") == "exec":
                        iid = item.get("id") or item.get("call_id")
                        raw = pending_input.get(iid, unwrap_js(item.get("arguments")) or "")
                        item = dict(item)
                        item["type"] = "custom_tool_call"
                        item.pop("arguments", None)
                        item["input"] = raw
                        data_obj["item"] = item
                        oi = data_obj.get("output_index", 0)
                        emit("response.custom_tool_call_input.delta",
                             {"type": "response.custom_tool_call_input.delta",
                              "item_id": iid, "output_index": oi, "delta": raw})
                        emit("response.custom_tool_call_input.done",
                             {"type": "response.custom_tool_call_input.done",
                              "item_id": iid, "output_index": oi, "input": raw})
                        emit(et, data_obj)
                        continue
                # response.created/in_progress/completed: output-Items mitsamt
                # uebersetzen, damit Codex das custom_tool_call sieht.
                if isinstance(data_obj, dict) and "output" in data_obj:
                    _rewrite_node(data_obj)
                if data_obj is not None:
                    emit(et, data_obj)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            with _stats_lock:
                STATS["errors"] += 1
            log("stream rewrite error: %r" % (exc,))
        finally:
            try:
                self._write_chunk(b"")
            except Exception:
                pass

    def _write_chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        else:
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def main(argv=None) -> int:
    global UPSTREAM, LOG_PATH, VERBOSE, DUMP_DIR
    parser = argparse.ArgumentParser(description="Codex <-> Gateway Tool-Bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20127)
    parser.add_argument("--upstream", default="http://localhost:20128/v1")
    parser.add_argument("--log")
    parser.add_argument("--dump", help="Verzeichnis fuer Request/Response-Mitschnitte")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    UPSTREAM = args.upstream
    LOG_PATH = args.log
    VERBOSE = args.verbose
    DUMP_DIR = args.dump

    if not port_is_free(args.host, args.port):
        print(f"Port {args.port} ist belegt - laeuft die Bridge schon?", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    server.daemon_threads = True
    log(f"bridge up on http://{args.host}:{args.port}/v1 -> {UPSTREAM}")
    print(f"bridge up on http://{args.host}:{args.port}/v1 -> {UPSTREAM}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
