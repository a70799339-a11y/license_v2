"""A1.3 辅助：激活一台全新在线设备（刚活跃），供 UI 解绑拒绝路径验证"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
TS = str(int(time.time()))
FP = ("acceptonline" + TS).ljust(64, "0")[:64]

body = json.dumps({
    "licenseCode": "LIC-2026-0085",
    "deviceName": "验收在线机",
    "os_name": "acceptance-test",
}).encode()
req = urllib.request.Request(
    BASE + "/api/client/activate",
    data=body,
    headers={"Content-Type": "application/json", "X-Device-Fingerprint": FP},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as r:
    resp = json.loads(r.read())
print("token:", (resp.get("token") or "")[:20], "...")
print("fp:", FP)
with open(r"C:\songxiankun\projects\license\tests\client-accept\_online_dev.json", "w") as f:
    json.dump({"fp": FP, "token": resp.get("token"), "deviceName": "验收在线机"}, f)
