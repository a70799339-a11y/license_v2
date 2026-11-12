"""探 LIC-2026-0085 服务端返回的 license/hotel 字段"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
TS = str(int(time.time()))
FP = ("probeexpire" + TS).ljust(64, "0")[:64]

body = json.dumps({"licenseCode": "LIC-2026-0085", "deviceName": "探针-过期字段", "os_name": "probe"}).encode()
req = urllib.request.Request(
    BASE + "/api/client/activate", data=body,
    headers={"Content-Type": "application/json", "X-Device-Fingerprint": FP}, method="POST")
with urllib.request.urlopen(req, timeout=10) as r:
    resp = json.loads(r.read())
print("hotel:", json.dumps(resp.get("hotel"), ensure_ascii=False))
print("license:", json.dumps(resp.get("license"), ensure_ascii=False))
print("server_time:", resp.get("server_time"))
