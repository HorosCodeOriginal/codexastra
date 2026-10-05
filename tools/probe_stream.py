"""Direkter Vergleich: Gateway STREAMING vs NON-STREAMING bei einem Tool-Call.
Frage: liefert der Streaming-Pfad function_call/custom_tool_call mit?"""
import json
import urllib.request
import http.client

UP = "http://127.0.0.1:20128/v1"
TOK = "DEIN_TOKEN_HIER"  # hier dein Gateway-Token einsetzen


def build(stream):
    return {
        "model": "gpt-6-astra",
        "input": "Use the exec tool to run: write a file named probe.txt with content hi.",
        "stream": stream,
        "tools": [{
            "type": "function", "name": "exec",
            "description": "Run JavaScript. Use tools.exec_command to run shell commands.",
            "parameters": {"type": "object",
                           "properties": {"input": {"type": "string"}},
                           "required": ["input"]},
        }],
    }


def non_streaming():
    body = json.dumps(build(False)).encode()
    req = urllib.request.Request(UP + "/responses", data=body, method="POST")
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            obj = json.loads(r.read())
            types = [o.get("type") for o in obj.get("output", [])]
            print("NON-STREAM status", r.status, "output types:", types)
            for o in obj.get("output", []):
                if o.get("type") in ("function_call", "custom_tool_call"):
                    print("   TOOL CALL FOUND:", o.get("name"), str(o)[:160])
    except Exception as e:
        print("NON-STREAM ERROR:", e)


def streaming():
    body = json.dumps(build(True)).encode()
    conn = http.client.HTTPConnection("127.0.0.1", 20128, timeout=60)
    conn.request("POST", "/v1/responses", body=body, headers={
        "Authorization": "Bearer " + TOK,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    })
    r = conn.getresponse()
    print("STREAM status", r.status)
    buf = b""
    events = 0
    found_call = False
    while True:
        line = r.readline()
        if not line:
            break
        if line.startswith(b"event:"):
            events += 1
        if b"function_call" in line or b"custom_tool_call" in line:
            found_call = True
            print("   STREAM TOOL-CALL LINE:", line.decode(errors="replace")[:200])
    print("STREAM event lines:", events, "tool call seen:", found_call)
    conn.close()


print(">>> NON-STREAMING")
non_streaming()
print(">>> STREAMING")
streaming()
