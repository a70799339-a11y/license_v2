import json, urllib.request
BASE = "http://127.0.0.1:8000"
def req(method, path, body=None, token=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token: r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read() or b"{}")
login = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
tok = login["token"]
r = (req("POST", "/api/wecom/simulate", {"text": "提取前十条日志信息", "userid": "probe28"}, token=tok) or {}).get("reply", "")
print("HAS_FENCE:", "```" in r)
import re
m = re.search(r"LIC-(?!\*)[0-9A-Za-z]", r)
print("UNMASKED_LIC:", bool(m), m.group(0) if m else "")
print("REPLY_HEAD:", repr(r[:220]))
