"""A2 后台确认/驳回续期订单探针
用法: python order_admin.py confirm|reject|list [reason]
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def req(method, path, token=None, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


st, login = req("POST", "/api/auth/login", data={"username": "admin", "password": "admin123"})
token = login.get("token")
assert token, login

action = sys.argv[1] if len(sys.argv) > 1 else "list"
st, orders = req("GET", "/api/renew-orders", token=token)
if isinstance(orders, dict):
    orders = orders.get("items") or orders.get("data") or []

target = next((o for o in orders if o.get("orderNo") == "RO-20261111-2aa445"), None)
print("找到订单:", json.dumps(target, ensure_ascii=False) if target else "无")

if action == "list":
    print(json.dumps(orders[:5], ensure_ascii=False, indent=1))
elif action == "confirm" and target:
    st, r = req("POST", f"/api/renew-orders/{target['id']}/confirm", token=token, data={})
    print("confirm:", st, json.dumps(r, ensure_ascii=False))
elif action == "reject" and target:
    reason = sys.argv[2] if len(sys.argv) > 2 else "验收测试驳回"
    st, r = req("POST", f"/api/renew-orders/{target['id']}/reject", token=token, data={"reason": reason})
    print("reject:", st, json.dumps(r, ensure_ascii=False))
