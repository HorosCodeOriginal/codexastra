"""Mock-Gateway fuer die Bridge-End-to-End-Verifikation.

Liefert auf POST /v1/responses einen SSE-Strom, der im 1. Turn einen
exec-Tool-Call (function_call) emittiert und im 2. Turn eine finale
Nachricht. Damit laesst sich die Kette

    Codex -> Bridge (function_call->custom_tool_call) -> Codex exec (V8) -> Datei

beweisen, ohne dass das echte LLM-Gateway erreichbar sein muss.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 20129
WORKDIR = r"C:\CodexAstra\data\mock-test"
JS = (
    "const r = await tools.exec_command({"
    "cmd: \"Set-Content -Path bridge-ok.txt -Value 'bridge-ok' -NoNewline\", "
    "workdir: " + json.dumps(WORKDIR) + "});"
    "text(JSON.stringify(r));"
)
ARGUMENTS = json.dumps({"input": JS})
_LOCK = threading.Lock()
_COUNT = {"n": 0}


def _sse(events):
    return "".join(f"event: {e}\ndata: {d}\n\n" for e, d in events).encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({
                "object": "list",
                "data": [{"id": "gpt-6-astra", "object": "model", "owned_by": "mock"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)  # Request ignorieren
        with _LOCK:
            _COUNT["n"] += 1
            n = _COUNT["n"]
        if n == 1:
            events = [
                ("response.created", json.dumps({"type": "response.created",
                    "response": {"status": "in_progress", "output": []}})),
                ("response.in_progress", json.dumps({"type": "response.in_progress",
                    "response": {"status": "in_progress", "output": []}})),
                ("response.output_item.added", json.dumps({
                    "type": "response.output_item.added", "output_index": 0,
                    "item": {"type": "function_call", "arguments": "", "call_id": "call_mock1",
                             "name": "exec", "id": "fc_mock1"}})),
                ("response.function_call_arguments.delta", json.dumps({
                    "type": "response.function_call_arguments.delta", "item_id": "fc_mock1",
                    "output_index": 0, "delta": ARGUMENTS})),
                ("response.function_call_arguments.done", json.dumps({
                    "type": "response.function_call_arguments.done", "item_id": "fc_mock1",
                    "output_index": 0, "arguments": ARGUMENTS})),
                ("response.output_item.done", json.dumps({
                    "type": "response.output_item.done", "output_index": 0,
                    "item": {"type": "function_call", "arguments": ARGUMENTS, "call_id": "call_mock1",
                             "name": "exec", "id": "fc_mock1"}})),
                ("response.completed", json.dumps({"type": "response.completed",
                    "response": {"status": "completed", "output": [
                        {"type": "function_call", "arguments": ARGUMENTS, "call_id": "call_mock1",
                         "name": "exec", "id": "fc_mock1"}]}})),
            ]
        else:
            events = [
                ("response.created", json.dumps({"type": "response.created",
                    "response": {"status": "in_progress", "output": []}})),
                ("response.in_progress", json.dumps({"type": "response.in_progress",
                    "response": {"status": "in_progress", "output": []}})),
                ("response.output_item.added", json.dumps({
                    "type": "response.output_item.added", "output_index": 0,
                    "item": {"type": "message", "role": "assistant", "status": "in_progress",
                             "content": []}})),
                ("response.content_part.added", json.dumps({
                    "type": "response.content_part.added", "item_id": "msg_mock", "output_index": 0,
                    "content_index": 0, "part": {"type": "output_text", "text": ""}})),
                ("response.content_part.done", json.dumps({
                    "type": "response.content_part.done", "item_id": "msg_mock", "output_index": 0,
                    "content_index": 0, "part": {"type": "output_text", "text": "Datei erstellt."}})),
                ("response.output_item.done", json.dumps({
                    "type": "response.output_item.done", "output_index": 0,
                    "item": {"type": "message", "role": "assistant", "status": "completed",
                             "content": [{"type": "output_text", "text": "Datei erstellt."}]}})),
                ("response.completed", json.dumps({"type": "response.completed",
                    "response": {"status": "completed", "output": [
                        {"type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": "Datei erstellt."}]}]}})),
            ]
        body = _sse(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"mock gateway on http://127.0.0.1:{PORT}/v1", flush=True)
    srv.serve_forever()
