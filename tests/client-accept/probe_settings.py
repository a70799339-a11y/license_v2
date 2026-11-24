import json, urllib.request
def req(method, path, body=None, token=None):
    r = urllib.request.Request("http://127.0.0.1:8000" + path, data=json.dumps(body).encode() if body else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token: r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read() or b"{}")
login = req("POST", "/api/auth/login", {"username": "viewer", "password": "viewer123"})
d = req("GET", "/api/settings", token=login["token"])
print("viewer 角色:", login.get("role"), "| settings 返回键数:", len(d))
for k in ("llm_cloud_api_key", "wecom_bot_secret", "wecom_ops_webhook", "wecom_ws_push_chatid", "wecom_hotel_hook_h001"):
    v = d.get(k, "<无此键>")
    print(f"  {k} = {repr(v)[:22]}")
