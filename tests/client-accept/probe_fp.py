import json, urllib.request, time
BASE = "http://127.0.0.1:8000"
def req(method, path, body=None, token=None, fp=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token: r.add_header("Authorization", f"Bearer {token}")
    if fp: r.add_header("X-Device-Fingerprint", fp)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}

ts = str(int(time.time()*1000))[-6:]
login = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})[1]
tok = login["token"]
st, p = req("POST", "/api/hotels", {"name": f"测试店指纹核{ts}", "code": f"HT-FP{ts}", "plan": "专业版"}, token=tok)
hid = p.get("id")
st, lc = req("POST", "/api/licenses", {"hotelId": hid, "plan": "专业版", "type": "新签", "months": 6}, token=tok)
print("发授权:", st, "code=", lc.get("code"))

fp = "fp" + ts + "x"*20
# Issue2 实测：用「客户编号」而非授权码激活
st, act = req("POST", "/api/client/activate", {"licenseCode": f"HT-FP{ts}", "deviceName": "probe"}, fp=fp)
ctok = act.get("token", "")
print(f"Issue2 用客户编号 HT-FP{ts} 激活: HTTP {st} → {'⚠️ 成功（编号可激活）' if st==200 else '✅ 被拒'}")

if ctok:
    st1, _ = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.3"}, token=ctok)  # 无 fp 头
    st2, _ = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.3"}, token=ctok, fp="WRONGFP"+ts)
    st3, _ = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.3"}, token=ctok, fp=fp)
    print(f"Issue4 无指纹头:   HTTP {st1} → {'⚠️ 通过（校验跳过）' if st1==200 else '✅ 被拒'}")
    print(f"Issue4 错误指纹:   HTTP {st2} → {'✅ 被拒' if st2!=200 else '❌ 通过'}")
    print(f"Issue4 正确指纹:   HTTP {st3} → {'✅ 正常' if st3==200 else '❌ 被拒'}")

if hid: req("PATCH", f"/api/hotels/{hid}", {"status": "已停用"}, token=tok)
print("清扫完成")
