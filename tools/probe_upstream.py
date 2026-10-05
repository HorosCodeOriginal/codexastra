"""Isolations-Test: Gateway direkt (ohne Bridge) ansprechen."""
import json
import sys
import urllib.request

UPSTREAM = "http://127.0.0.1:20128/v1"
TOKENS = [
    "DEIN_TOKEN_HIER",                  # hier dein Gateway-Token einsetzen
    "DEIN_ZWEITES_TOKEN_HIER",          # optionales zweites Token
]


def req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa
        return "ERR", str(e).encode()


for tok in TOKENS:
    print("=== TOKEN", tok[-12:], "===")
    st, raw = req("GET", UPSTREAM + "/models", tok)
    print("GET /models ->", st, (raw[:120] if isinstance(raw, bytes) else raw))
    if isinstance(st, int) and st == 200:
        try:
            models = json.loads(raw)
            print("  model count:", len(models.get("data", [])))
            names = [m["id"] for m in models.get("data", [])][:5]
            print("  sample:", names)
        except Exception:
            pass
    # minimal non-streaming responses call
    body = {
        "model": "gpt-6-astra",
        "input": "Say OK.",
        "stream": False,
        "tools": [{"type": "function", "name": "exec",
                   "description": "run js",
                   "parameters": {"type": "object",
                                  "properties": {"input": {"type": "string"}},
                                  "required": ["input"]}}],
    }
    st, raw = req("POST", UPSTREAM + "/responses", tok, body)
    print("POST /responses ->", st)
    print("  body:", (raw[:300] if isinstance(raw, bytes) else raw))
