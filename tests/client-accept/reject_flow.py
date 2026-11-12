"""A2#10 服务端驳回路径：下单→凭证→驳回→查状态（用已激活设备 token）"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
dev = json.load(open(r"C:\songxiankun\projects\license\tests\client-accept\_online_dev.json"))
token, fp = dev["token"], dev["fp"]


def req(method, path, tok=None, data=None, headers=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    if headers:
        for k, v in headers.items():
            r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


hdrs = {"X-Device-Fingerprint": fp}
st, o = req("POST", "/api/client/renew/order", token, {"months": 12, "plan": "", "channel": "transfer"}, hdrs)
print("下单:", st, o.get("order_no"), o.get("amount"))
no = o["order_no"]
st, r = req("POST", f"/api/client/renew/order/{no}/proof", token,
            {"proof_no": "ACCEPT-REJECT-001", "note": "驳回路径验收"}, hdrs)
print("凭证:", st, r)

st, login = req("POST", "/api/auth/login", data={"username": "admin", "password": "admin123"})
at = login["token"]
st, orders = req("GET", "/api/renew-orders", at)
target = next((x for x in orders if x.get("orderNo") == no), None)
st, r = req("POST", f"/api/renew-orders/{target['id']}/reject", at, {"reason": "验收测试-驳回路径核验"})
print("驳回:", st, r)
st, after = req("GET", "/api/renew-orders", at)
t2 = next((x for x in after if x.get("orderNo") == no), None)
print("终态:", t2.get("status"), "原因:", t2.get("rejectReason"))
