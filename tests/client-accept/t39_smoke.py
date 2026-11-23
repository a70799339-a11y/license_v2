#!/usr/bin/env python3
"""T39 手工冒烟：建店→造告警/绑定→指令处置→验证。跑完自清。"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = "/mnt/c/songxiankun/projects/license"


def req(method, path, body=None, token=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=15) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def sql(s):
    subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "license", "-c", s],
                   capture_output=True, text=True, timeout=15)


def iz(text, sender):
    py = "/opt/license/.venv/bin/python3"
    r = subprocess.run([py, f"{ROOT}/tests/t39_cmd.py", text, sender],
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    if r.returncode != 0:
        print("iz err:", (r.stderr or "")[:300])
    return (r.stdout or "").strip()


_, login = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
tok = login["token"]
ts = str(int(time.time() * 1000))[-8:]
code = f"HT-CZ{ts[-6:]}"
name = f"测试店处置{ts}"
_, p = req("POST", "/api/hotels", {"name": name, "code": code, "group": "测试集团", "city": "测试市", "plan": "专业版"}, tok)
hid = p.get("id")
sender = f"t39u{ts}"
sql(f"INSERT INTO wecom_bindings (userid,name,role,group_id) VALUES ('{sender}','T39admin','admin','') ON CONFLICT (userid) DO NOTHING")
sql(f"INSERT INTO alerts (id,level,rule,hotel_name,detail,time,status) VALUES "
    f"('a39{ts[-6:]}','高','T39共享检测','{name}','t39','2026-11-23 08:00:00','待处理'),"
    f"('a39b{ts[-6:]}','中','T39指纹变更','{name}','t39','2026-11-23 08:01:00','待处理')")

print("== 预览 ==")
print(iz(f"处置 {code}", sender))
print("== 确认处置 ==")
print(iz(f"确认处置 {code} 误报测试", sender))
print("== 空态 ==")
print(iz(f"处置 {code}", sender))
print("== 无空格 ==")
print(iz(f"处置{code}", sender))
_, al = req("GET", "/api/alerts", token=tok)
mine = [a for a in al if a.get("hotelName") == name]
print("== 落库 ==", json.dumps(mine, ensure_ascii=False)[:300])
if hid:
    req("PATCH", f"/api/hotels/{hid}", {"status": "已停用"}, tok)
print("smoke done")
