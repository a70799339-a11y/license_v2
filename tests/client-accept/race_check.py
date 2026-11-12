"""T38.1 独立竞态复现：双线程抢最后 1 个配额位（快速迭代用，正式回归仍在 run_tests.py T38.1）"""
import json
import subprocess
import threading
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"


def req(method, path, body=None, token=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else None
        except Exception:
            return e.code, {"_raw": raw[:100]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:100]}


st, login = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
token = (login or {}).get("token")

fails = 0
for round_no in range(5):
    ts = uuid.uuid4().hex[:8]
    st, p = req("POST", "/api/hotels",
                {"name": f"竞态店{ts}", "code": f"HT-R{ts}", "group": "测试集团", "city": "测试市", "plan": "基础版"},
                token)
    cid = p["id"]
    st, lic = req("POST", "/api/licenses", {"hotelId": cid, "plan": "基础版", "months": 1}, token)
    code = lic["code"]
    subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "license", "-c",
                    f"UPDATE hotels SET max_devices = 1 WHERE id = '{cid}'"], capture_output=True)
    barrier = threading.Barrier(2)
    race = {}

    def act(key):
        barrier.wait()
        fpx = (key * 2) * 28 + ts
        sx, _ = req("POST", "/api/client/activate",
                    {"licenseCode": code, "deviceName": f"抢位-{key}", "os_name": "testos"},
                    headers={"X-Device-Fingerprint": fpx})
        race[key] = sx

    ths = [threading.Thread(target=act, args=("a",)), threading.Thread(target=act, args=("b",))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    st, hd = req("GET", f"/api/hotels/{cid}/detail", token=token)
    dev = (hd.get("hotel") or {}).get("devices", -1)
    sts = sorted([race.get("a"), race.get("b")])
    ok = sts == [200, 403] and dev == 1
    if not ok:
        fails += 1
    print(f"round {round_no + 1}: sts={sts} devices={dev} {'OK' if ok else 'FAIL'}")

print(f"\n===== 竞态复现: {5 - fails}/5 =====")
