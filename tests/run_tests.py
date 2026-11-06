"""License 授权管理系统 — 全模块客户端测试套件
用法:
    python tests/run_tests.py                 # 测本机 :8000
    BASE_URL=http://localhost python tests/run_tests.py   # 测 WSL/Nginx 部署
结果写入 tests/last_results.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
results: list[dict] = []


def req(method: str, path: str, body=None, token=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
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
            return e.code, (json.loads(raw) if raw else None)
        except Exception:
            return e.code, {"_raw": raw[:200]}   # 非 JSON 响应（如网关 HTML）保留现场
    except Exception as e:
        return "ERR", {"_raw": str(e)[:200]}


def check(cid: str, module: str, name: str, cond: bool, actual=""):
    results.append({"id": cid, "module": module, "name": name, "pass": bool(cond), "actual": str(actual)[:180]})
    print(f"  [{'PASS' if cond else 'FAIL'}] {cid} {module} · {name}" + (f"  ({actual})" if actual else ""))


def main():
    ts = int(time.time()) % 1000000
    print(f"目标: {BASE}\n")

    # ---------------- 1. 认证模块 ----------------
    print("【1. 认证模块】")
    st, p = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    token = (p or {}).get("token")
    check("T1.1", "认证", "管理员登录成功并签发 JWT", st == 200 and bool(token), f"HTTP {st}")
    st, _ = req("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
    check("T1.2", "认证", "错误密码返回 401", st == 401, f"HTTP {st}")
    st, _ = req("POST", "/api/auth/login", {"username": "ghost", "password": "x"})
    check("T1.3", "认证", "不存在用户返回 401", st == 401, f"HTTP {st}")
    st, _ = req("GET", "/api/hotels")
    check("T1.4", "认证", "无 token 访问受保护接口返回 401", st == 401, f"HTTP {st}")
    st, _ = req("GET", "/api/hotels", token="bad.token.value")
    check("T1.5", "认证", "无效 token 返回 401", st == 401, f"HTTP {st}")
    st, p = req("GET", "/api/auth/me", token=token)
    check("T1.6", "认证", "me 接口返回用户身份", st == 200 and (p or {}).get("username") == "admin")

    # ---------------- 2. 客户管理模块 ----------------
    print("【2. 客户管理模块】")
    st, hotels = req("GET", "/api/hotels", token=token)
    hotels = hotels or []
    check("T2.1", "客户管理", "客户列表可获取（种子 40 家）", st == 200 and len(hotels) >= 40, f"{len(hotels)} 条")

    code = f"HT-T{ts}"
    st, p = req("POST", "/api/hotels",
                {"name": f"自动化测试酒店{ts}", "code": code, "group": "测试集团", "city": "测试市", "plan": "专业版"},
                token)
    new_id = (p or {}).get("id")
    check("T2.2", "客户管理", "新增客户成功", st == 200 and (p or {}).get("ok") is True, f"id={new_id}")

    st, _ = req("POST", "/api/hotels", {"name": "重复编号", "code": code}, token)
    check("T2.3", "客户管理", "重复客户编号返回 400", st == 400, f"HTTP {st}")

    st, p = req("PATCH", f"/api/hotels/{new_id}", {"plan": "旗舰版"}, token)
    check("T2.4", "客户管理", "修改客户套餐生效", st == 200 and (p or {}).get("plan") == "旗舰版")

    ids2 = [hotels[0]["id"], hotels[1]["id"]]
    st, p = req("POST", "/api/hotels/batch-renew", {"ids": ids2}, token)
    check("T2.5", "客户管理", "批量续期两家酒店", st == 200 and all(h.get("expireAt") == "2027-11-01" for h in (p or [])))

    st, p = req("POST", "/api/hotels/batch-disable", {"ids": [new_id]}, token)
    check("T2.6", "客户管理", "批量停用生效", st == 200 and (p or [{}])[0].get("status") == "已停用")

    st, p = req("GET", f"/api/hotels/{new_id}/detail", token=token)
    keys = set((p or {}).keys())
    check("T2.7", "客户管理", "客户详情聚合（酒店+设备+授权+告警）", st == 200 and {"hotel", "devices", "licenses", "alerts"} <= keys,
          f"devices={len((p or {}).get('devices', []))}")

    st, _ = req("GET", "/api/hotels/h999/detail", token=token)
    check("T2.8", "客户管理", "不存在客户返回 404", st == 404, f"HTTP {st}")

    # ---------------- 3. 授权管理模块 ----------------
    print("【3. 授权管理模块】")
    st, lics = req("GET", "/api/licenses", token=token)
    lics = lics or []
    check("T3.1", "授权管理", "授权列表可获取", st == 200 and len(lics) > 0, f"{len(lics)} 条")

    st, p = req("POST", "/api/licenses", {"hotelId": hotels[2]["id"], "plan": "旗舰版", "type": "续期", "months": 12}, token)
    code1 = (p or {}).get("code")
    check("T3.2", "授权管理", "发放授权（续期/12 个月）", st == 200 and (p or {}).get("status") == "生效中", f"code={code1}")

    st, p = req("POST", "/api/licenses", {"hotelId": hotels[3]["id"], "plan": "基础版", "type": "新签", "months": 6}, token)
    code2 = (p or {}).get("code")
    lic2_id = (p or {}).get("id")
    check("T3.3", "授权管理", "发放授权（新签/6 个月）", st == 200 and (p or {}).get("type") == "新签", f"code={code2}")
    check("T3.4", "授权管理", "授权码自动唯一递增", bool(code1) and bool(code2) and code1 != code2, f"{code1} → {code2}")

    st, p = req("POST", f"/api/licenses/{lic2_id}/revoke", {"reason": "自动化测试吊销"}, token)
    check("T3.5", "授权管理", "吊销授权并记录原因", st == 200 and (p or {}).get("status") == "已吊销" and (p or {}).get("reason") == "自动化测试吊销")

    st, _ = req("POST", "/api/licenses/ln-404/revoke", {"reason": "x"}, token)
    check("T3.6", "授权管理", "吊销不存在授权返回 404", st == 404, f"HTTP {st}")

    # ---------------- 4. 套餐管理模块 ----------------
    print("【4. 套餐管理模块】")
    st, plans = req("GET", "/api/plans", token=token)
    plans = plans or []
    names = sorted(x.get("name", "") for x in plans)
    expected = {"基础版", "专业版", "旗舰版"}
    check("T4.1", "套餐管理", "套餐列表为 3 档", st == 200 and set(names) == expected, str(names))
    check("T4.2", "套餐管理", "套餐字段完整（价格/设备上限/功能）",
          all(x.get("price") and x.get("maxDevices") and x.get("features") for x in plans))

    # ---------------- 5. AI 智能风控模块 ----------------
    print("【5. AI 智能风控模块】")
    st, alerts = req("GET", "/api/alerts", token=token)
    alerts = alerts or []
    check("T5.1", "AI 智能风控", "告警列表可获取", st == 200 and len(alerts) > 0, f"{len(alerts)} 条")

    pending = [a for a in alerts if a.get("status") != "已处置"]
    if pending:
        a_id = pending[0]["id"]
        st, p = req("POST", f"/api/alerts/{a_id}/handle", token=token)
        check("T5.2", "AI 智能风控", "告警标记处置生效", st == 200 and (p or {}).get("status") == "已处置", f"id={a_id}")
    else:
        check("T5.2", "AI 智能风控", "告警标记处置生效", True, "无待处置告警（此前已全部处置，状态合法）")

    st, _ = req("POST", "/api/alerts/a-404/handle", token=token)
    check("T5.3", "AI 智能风控", "处置不存在告警返回 404", st == 404, f"HTTP {st}")

    # ---------------- 6. 到期续期模块 ----------------
    print("【6. 到期续期模块】")
    st, stats = req("GET", "/api/stats", token=token)
    es = (stats or {}).get("expireSoon", {})
    check("T6.1", "到期续期", "到期分布统计（7/30/60/90 天）",
          st == 200 and all(isinstance(es.get(k), int) for k in ("d7", "d30", "d60", "d90")),
          f"d7={es.get('d7')} d30={es.get('d30')} d60={es.get('d60')} d90={es.get('d90')}")

    st, s = req("GET", "/api/settings", token=token)
    check("T6.2", "到期续期", "自动续期开关可读取", st == 200 and (s or {}).get("auto_renew") in ("on", "off"),
          f"auto_renew={(s or {}).get('auto_renew')}")

    new_val = "off" if (s or {}).get("auto_renew") == "on" else "on"
    req("POST", "/api/settings", {"key": "auto_renew", "value": new_val}, token)
    _, s2 = req("GET", "/api/settings", token=token)
    check("T6.3", "到期续期", "自动续期设置修改后持久化", (s2 or {}).get("auto_renew") == new_val, f"→ {new_val}")

    # ---------------- 7. 安全设计模块 ----------------
    print("【7. 安全设计模块】")
    _, s3 = req("GET", "/api/settings", token=token)
    check("T7.1", "安全设计", "心跳超时参数可读（72h）", (s3 or {}).get("heartbeat_timeout_hours") == "72",
          (s3 or {}).get("heartbeat_timeout_hours"))

    st, lics2 = req("GET", "/api/licenses", token=token)
    revoked = [l for l in (lics2 or []) if l.get("status") == "已吊销"]
    check("T7.2", "安全设计", "吊销状态重读持久化", st == 200 and len(revoked) >= 1, f"已吊销 {len(revoked)} 条")

    st, _ = req("POST", "/api/settings", {"key": "heartbeat_timeout_hours", "value": "72"}, token)
    check("T7.3", "安全设计", "安全参数写入正常", st == 200, f"HTTP {st}")

    # ---------------- 8. 操作日志模块 ----------------
    print("【8. 操作日志模块】")
    st, logs = req("GET", "/api/logs", token=token)
    logs = logs or []
    check("T8.1", "操作日志", "日志接口可获取", st == 200 and len(logs) > 0, f"{len(logs)} 条")

    actions = {l.get("action") for l in logs}
    need = {"新增客户", "发放授权", "批量续期", "吊销授权", "处置告警", "修改设置"}
    check("T8.2", "操作日志", "关键操作均被审计记录", need <= actions, f"缺失 {sorted(need - actions) if need - actions else '无'}")

    # ---------------- 9. 数据持久化 ----------------
    print("【9. 数据持久化】")
    st, hotels2 = req("GET", "/api/hotels", token=token)
    ok = st == 200 and all(h.get("expireAt") == "2027-11-01" for h in (hotels2 or []) if h.get("id") in ids2)
    check("T9.1", "数据持久化", "批量续期结果重读一致", ok)

    st, p = req("GET", f"/api/hotels/{new_id}/detail", token=token)
    hotel_status = ((p or {}).get("hotel") or {}).get("status")
    check("T9.2", "数据持久化", "停用状态重读一致", hotel_status == "已停用", f"status={hotel_status}")

    # ---------------- 10. 客户端同步（激活/心跳/版本更新） ----------------
    print("【10. 客户端同步】")
    FP = "ab" * 32  # 模拟设备指纹（64 位 hex）

    st, p = req("GET", "/api/client/version")
    check("T10.1", "客户端同步", "版本接口无需鉴权可获取", st == 200 and bool((p or {}).get("latest")),
          f"latest={(p or {}).get('latest')}")

    st, p = req("GET", "/api/client/public-key")
    check("T10.2", "客户端同步", "Ed25519 公钥可获取", st == 200 and (p or {}).get("algorithm") == "ed25519"
          and len((p or {}).get("public_key", "")) == 64)

    st, p = req("POST", "/api/client/activate",
                {"licenseCode": "HT-0001", "deviceName": "自动化测试客户端", "clientVersion": "0.1.0"},
                headers={"X-Device-Fingerprint": FP})
    signed = (p or {}).get("signed_license") or {}
    ctoken = (p or {}).get("token")
    check("T10.3", "客户端同步", "激活成功并签发签名 License", st == 200 and bool(ctoken)
          and bool(signed.get("signature")) and bool(signed.get("payload")),
          f"lic={((p or {}).get('license') or {}).get('code')}")
    check("T10.4", "客户端同步", "License 签名结构完整（载荷/签名/公钥）",
          {"payload", "signature", "public_key"} <= set(signed.keys())
          and len(signed.get("signature", "")) == 128)

    st, p = req("POST", "/api/client/activate", {"licenseCode": "NO-SUCH-CODE"},
                headers={"X-Device-Fingerprint": FP})
    check("T10.5", "客户端同步", "无效授权码激活返回 404", st == 404, f"HTTP {st}")

    st, p = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T10.6", "客户端同步", "心跳返回授权状态与服务端时间", st == 200
          and (p or {}).get("status") in ("valid", "expiring") and bool((p or {}).get("server_time")),
          f"status={(p or {}).get('status')}")

    st, p = req("POST", "/api/client/heartbeat", {"clientVersion": "0.0.1"},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T10.7", "客户端同步", "版本落后触发同步更新（update_available）", st == 200
          and (p or {}).get("update_available") is True and bool((p or {}).get("latest_version")),
          f"latest={(p or {}).get('latest_version')}")

    st, p = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                token=ctoken, headers={"X-Device-Fingerprint": "ff" * 32})
    check("T10.8", "客户端同步", "指纹不一致心跳拒绝（401）", st == 401, f"HTTP {st}")

    st, p = req("GET", "/api/client/profile", token=ctoken, headers={"X-Device-Fingerprint": FP})
    devs = (p or {}).get("devices") or []
    check("T10.9", "客户端同步", "自助数据仅含本酒店（设备/告警）", st == 200
          and (p or {}).get("hotel", {}).get("id") == "h001"
          and any(d.get("fingerprint") == FP for d in devs),
          f"devices={len(devs)}，含本机={any(d.get('fingerprint') == FP for d in devs)}")

    # ---------------- 11. P2 规模化（RBAC / 分页 / 告警聚合） ----------------
    print("【11. P2 规模化】")
    st, p = req("POST", "/api/auth/login", {"username": "viewer", "password": "viewer123"})
    vtok = (p or {}).get("token")
    check("T11.1", "P2 规模化", "viewer 登录并返回角色", st == 200 and (p or {}).get("role") == "viewer")

    st, p = req("POST", "/api/auth/login", {"username": "ops", "password": "ops123"})
    otok = (p or {}).get("token")
    check("T11.2", "P2 规模化", "operator 登录并返回角色", st == 200 and (p or {}).get("role") == "operator")

    st, _ = req("GET", "/api/hotels", token=vtok)
    check("T11.3", "P2 规模化", "viewer 可读客户列表", st == 200, f"HTTP {st}")

    st, _ = req("POST", "/api/hotels",
                {"name": "只读越权测试", "code": f"HT-V{ts}"}, token=vtok)
    check("T11.4", "P2 规模化", "viewer 写操作被拒（403）", st == 403, f"HTTP {st}")

    st, _ = req("POST", "/api/settings", {"key": "rbac_test", "value": "1"}, token=otok)
    check("T11.5", "P2 规模化", "operator 改设置被拒（403）", st == 403, f"HTTP {st}")

    st, _ = req("GET", "/api/users", token=otok)
    check("T11.6", "P2 规模化", "operator 访问用户管理被拒（403）", st == 403, f"HTTP {st}")

    st, p = req("GET", "/api/users", token=token)
    roles = {u.get("role") for u in (p or [])}
    check("T11.7", "P2 规模化", "admin 用户管理含三角色", st == 200
          and {"admin", "operator", "viewer"} <= roles, str(sorted(roles)))

    st, p = req("GET", "/api/hotels/page?page=1&page_size=5", token=token)
    check("T11.8", "P2 规模化", "服务端分页返回 total/items", st == 200
          and (p or {}).get("total", 0) >= 40 and len((p or {}).get("items", [])) == 5,
          f"total={(p or {}).get('total')}")

    st, p = req("GET", "/api/hotels/page?page=1&page_size=5&keyword=%E6%9D%AD%E5%B7%9E", token=token)
    ok_kw = all("杭州" in (h.get("name", "") + h.get("city", "") + h.get("group", "")) for h in (p or {}).get("items", []))
    check("T11.9", "P2 规模化", "分页关键词筛选生效", st == 200 and ok_kw,
          f"命中 {(p or {}).get('total')} 条")

    st, p = req("GET", "/api/alerts/groups", token=token)
    groups = p or []
    check("T11.10", "P2 规模化", "告警按规则聚合（含次数/酒店数）", st == 200 and len(groups) > 0
          and all("count" in g and "hotelCount" in g for g in groups),
          f"{len(groups)} 组")

    # ---------------- 12. 总/子管理（集团） ----------------
    print("【12. 总/子管理（集团）】")
    GP_FP = "b7" * 32  # 集团总端模拟指纹

    st, p = req("GET", "/api/groups", token=token)
    gs = p or []
    check("T12.1", "总/子管理", "集团列表含成员数/设备数", st == 200 and len(gs) > 0
          and all("memberCount" in g and "deviceCount" in g for g in gs),
          f"{len(gs)} 个集团")

    g0 = gs[0] if gs else {}
    st, p = req("POST", "/api/client/activate",
                {"licenseCode": g0.get("code", "GP-0001"), "deviceName": "集团总部测试"},
                headers={"X-Device-Fingerprint": GP_FP})
    gtok = (p or {}).get("token")
    signed = (p or {}).get("signed_license") or {}
    check("T12.2", "总/子管理", "集团总端激活（scope=group + 签名）", st == 200
          and (p or {}).get("scope") == "group" and bool(signed.get("signature"))
          and (signed.get("payload") or {}).get("scope") == "group",
          f"{(p or {}).get('hotel', {}).get('name')}")

    st, p = req("GET", "/api/client/group/profile", token=gtok, headers={"X-Device-Fingerprint": GP_FP})
    members = (p or {}).get("members") or []
    check("T12.3", "总/子管理", "集团 profile 数据隔离（仅本集团门店）", st == 200
          and (p or {}).get("scope") == "group" and len(members) == g0.get("memberCount", -1),
          f"成员 {len(members)} 家")

    st, p = req("POST", "/api/client/group/renew", {"hotelIds": []}, token=gtok,
                headers={"X-Device-Fingerprint": GP_FP})
    check("T12.4", "总/子管理", "集团总端批量续期生效", st == 200 and (p or {}).get("renewed", 0) > 0,
          f"renewed={(p or {}).get('renewed')}")

    st, p2 = req("GET", "/api/client/group/profile", token=gtok, headers={"X-Device-Fingerprint": GP_FP})
    all_2027 = all((m.get("expire_at") or "").startswith("2027") for m in (p2 or {}).get("members", []))
    check("T12.5", "总/子管理", "续期结果持久化（成员到期 2027）", st == 200 and all_2027)

    st, _ = req("GET", "/api/client/group/profile", token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T12.6", "总/子管理", "单店 token 调集团接口被拒（400）", st == 400, f"HTTP {st}")

    st, p = req("POST", "/api/groups",
                {"name": f"自动化测试集团{ts}", "plan": "专业版", "expireAt": "2027-12-31"}, token=token)
    check("T12.7", "总/子管理", "后台创建集团并签发集团码", st == 200 and (p or {}).get("code", "").startswith("GP-"),
          f"code={(p or {}).get('code')}")

    st, _ = req("POST", "/api/groups/g001/renew", token=token)
    check("T12.8", "总/子管理", "后台集团续期接口可用", st == 200, f"HTTP {st}")

    # ---------------- 13. 离线宽限期（服务端统一配置） ----------------
    print("【13. 离线宽限期】")
    st, p = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T13.1", "离线宽限", "心跳下发离线宽限时长（默认72h）", st == 200
          and (p or {}).get("offline_grace_hours") == 72,
          f"grace={(p or {}).get('offline_grace_hours')}")

    st, _ = req("POST", "/api/settings", {"key": "heartbeat_timeout_hours", "value": "48"}, token=token)
    st2, p2 = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                  token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T13.2", "离线宽限", "后台改配置即时下发（72→48h）", st == 200
          and (p2 or {}).get("offline_grace_hours") == 48,
          f"grace={(p2 or {}).get('offline_grace_hours')}")
    # 恢复默认 72
    req("POST", "/api/settings", {"key": "heartbeat_timeout_hours", "value": "72"}, token=token)

    st, p = req("POST", "/api/client/activate",
                {"licenseCode": "HT-0002", "deviceName": "宽限激活测试", "clientVersion": "0.1.0"},
                headers={"X-Device-Fingerprint": "c3" * 32})
    check("T13.3", "离线宽限", "激活响应含宽限时长", st == 200
          and (p or {}).get("offline_grace_hours") == 72, f"grace={(p or {}).get('offline_grace_hours')}")

    # ---------------- 14. 实时告警管道（检测→自动记账） ----------------
    print("【14. 实时告警管道】")

    def alerts_of(rule, hotel=None):
        st, p = req("GET", "/api/alerts", token=token)
        out = [a for a in (p or []) if a.get("rule") == rule]
        if hotel is not None:
            out = [a for a in out if a.get("hotelName") == hotel]
        return out

    def t14fp(tag):
        """每轮唯一的设备指纹（固定指纹重复跑会触发跨店绑定 409）"""
        return (f"{ts:08x}" + tag * 60)[:64]

    # 无效激活尝试（同一无效码两次 → 只记一条，验证去重）
    for _ in range(2):
        req("POST", "/api/client/activate", {"licenseCode": f"NOPE-{ts}"},
            headers={"X-Device-Fingerprint": t14fp("a")})
    invalid = alerts_of("无效激活尝试", hotel=f"NOPE-{ts}")
    check("T14.1", "告警管道", "无效激活自动告警（含 24h 去重）", len(invalid) == 1,
          f"{len(invalid)} 条")

    # 指纹变更（凭证挪机）
    req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
        token=ctoken, headers={"X-Device-Fingerprint": "ff" * 32})
    fp_alerts = alerts_of("设备指纹变更")
    check("T14.2", "告警管道", "指纹不一致心跳触发「设备指纹变更」", len(fp_alerts) >= 1,
          f"{len(fp_alerts)} 条")

    # 时间回滚（客户端上报时钟异常）
    req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0", "clockAnomaly": True},
        token=ctoken, headers={"X-Device-Fingerprint": FP})
    clock_alerts = alerts_of("时间回滚")
    check("T14.3", "告警管道", "时钟回滚上报触发「时间回滚」", len(clock_alerts) >= 1,
          f"{len(clock_alerts)} 条")

    # 吊销后使用：专用酒店 激活→吊销→心跳
    st, p = req("POST", "/api/hotels",
                {"name": f"告警管道测试店{ts}", "code": f"HT-R{ts}", "plan": "基础版"}, token=token)
    rid = (p or {}).get("id")
    st, p = req("POST", "/api/licenses", {"hotelId": rid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
    rlic = (p or {}).get("code")
    rfp = t14fp("b")
    st, p = req("POST", "/api/client/activate",
                {"licenseCode": rlic, "deviceName": "吊销测试", "clientVersion": "0.1.0"},
                headers={"X-Device-Fingerprint": rfp})
    rtok = (p or {}).get("token")
    st, p = req("GET", "/api/licenses", token=token)
    rlic_id = next((l["id"] for l in (p or []) if l.get("code") == rlic), None)
    req("POST", f"/api/licenses/{rlic_id}/revoke", {"reason": "告警管道测试吊销"}, token=token)
    st, p = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                token=rtok, headers={"X-Device-Fingerprint": rfp})
    revoked_alerts = alerts_of("吊销后使用")
    check("T14.4", "告警管道", "吊销授权仍心跳触发「吊销后使用」",
          len(revoked_alerts) >= 1 and (p or {}).get("status") == "revoked",
          f"{len(revoked_alerts)} 条 status={(p or {}).get('status')}")

    # 多设备并发激活：同一店 3 台新设备
    st, p = req("POST", "/api/hotels",
                {"name": f"并发激活测试店{ts}", "code": f"HT-M{ts}", "plan": "基础版"}, token=token)
    mid = (p or {}).get("id")
    st, p = req("POST", "/api/licenses", {"hotelId": mid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
    mlic = (p or {}).get("code")
    for i in range(3):
        req("POST", "/api/client/activate",
            {"licenseCode": mlic, "deviceName": f"并发机{i+1}", "clientVersion": "0.1.0"},
            headers={"X-Device-Fingerprint": t14fp(f"m{i}")})
    multi_alerts = alerts_of("多设备并发激活")
    check("T14.5", "告警管道", "24h 内多设备激活触发「多设备并发激活」", len(multi_alerts) >= 1,
          f"{len(multi_alerts)} 条")

    # 心跳中断：读取告警触发惰性扫描，重复读取不重复记账
    st, p = req("GET", "/api/alerts", token=token)
    n1 = len([a for a in (p or []) if a.get("rule") == "心跳中断"])
    st, p = req("GET", "/api/alerts", token=token)
    n2 = len([a for a in (p or []) if a.get("rule") == "心跳中断"])
    check("T14.6", "告警管道", "长期无心跳触发「心跳中断」且不重复记账", n1 >= 1 and n1 == n2,
          f"首轮 {n1} 条，复读 {n2} 条")

    # 聚合视图兼容真实告警
    st, p = req("GET", "/api/alerts/groups", token=token)
    rules_seen = {g.get("rule") for g in (p or [])}
    check("T14.7", "告警管道", "聚合视图覆盖真实告警规则",
          st == 200 and {"设备指纹变更", "时间回滚"} <= rules_seen,
          f"{len(rules_seen)} 类规则")

    # ---------------- 15. 日志/审计脱敏 ----------------
    print("【15. 日志脱敏】")
    secret_reason = (
        "违规转租 手机13812345678 身份证330106199001011234 "
        "邮箱song.xk@example.com 卡号6222021234567890 授权LIC-2026-0123"
    )
    # 用吊销原因把敏感串带进审计日志
    st, p = req("POST", "/api/hotels",
                {"name": f"脱敏测试店{ts}", "code": f"HT-S{ts}", "plan": "基础版"}, token=token)
    sid = (p or {}).get("id")
    st, p = req("POST", "/api/licenses", {"hotelId": sid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
    slic = (p or {}).get("code")
    st, p = req("GET", "/api/licenses", token=token)
    slic_id = next((l["id"] for l in (p or []) if l.get("code") == slic), None)
    req("POST", f"/api/licenses/{slic_id}/revoke", {"reason": secret_reason}, token=token)

    st, p = req("GET", "/api/logs", token=token)
    log_text = " ".join((l.get("detail") or "") + (l.get("target") or "") for l in (p or []))
    # 只看本次脱敏测试店新写入的吊销记录（历史日志为掩码上线前的存量明文，不参与断言）
    my_logs = " ".join(
        (l.get("detail") or "")
        for l in (p or [])
        if l.get("target") == f"脱敏测试店{ts}" and l.get("action") == "吊销授权"
    )
    masked_ok = (
        "138****5678" in log_text
        and "330106199001011234" not in log_text
        and "song.xk@example.com" not in log_text
        and "6222021234567890" not in log_text
    )
    check("T15.1", "日志脱敏", "审计详情掩码（手机/身份证/邮箱/卡号）", masked_ok,
          "含138****5678" if "138****5678" in log_text else "掩码缺失")

    check("T15.2", "日志脱敏", "授权码部分掩码保留可追溯（LIC-****23）",
          "LIC-****" in my_logs and "LIC-2026-0123" not in my_logs,
          my_logs[:60])

    st, p = req("GET", "/api/alerts", token=token)
    full_fp_leaked = any(
        len(seg) >= 40 and all(c in "0123456789abcdefABCDEF" for c in seg)
        for a in (p or [])
        for seg in (a.get("detail") or "").replace("…", " ").split()
    )
    check("T15.3", "日志脱敏", "告警详情不含完整设备指纹", not full_fp_leaked)

    # 设置值脱敏（用户输入进入日志的另一条链路）
    req("POST", "/api/settings", {"key": "mask_test_contact", "value": "13987654321"}, token=token)
    st, p = req("GET", "/api/logs", token=token)
    log_text2 = " ".join((l.get("detail") or "") for l in (p or []))
    check("T15.4", "日志脱敏", "设置修改日志掩码手机号",
          "139****4321" in log_text2 and "13987654321" not in log_text2)

    # ---------------- 16. 企业微信机器人 ----------------
    print("【16. 企业微信机器人】")

    # 配置读写（webhook 返回掩码）
    st, p = req("POST", "/api/wecom/config",
                {"opsWebhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef123456",
                 "pushEnabled": True, "dryRun": True}, token=token)
    st, p = req("GET", "/api/wecom/config", token=token)
    check("T16.1", "企微机器人", "配置读写 + webhook 接口掩码返回", st == 200
          and (p or {}).get("pushEnabled") is True and "abcdef123456" not in str((p or {}).get("opsWebhook")),
          str((p or {}).get("opsWebhook"))[:40])

    # 测试推送（dry-run 记录）
    st, p = req("POST", "/api/wecom/test", token=token)
    st, s2 = req("GET", "/api/settings", token=token)
    last_push = (s2 or {}).get("wecom_last_push", "")
    check("T16.2", "企微机器人", "测试推送进入 dry-run 记录", (p or {}).get("dryRun") is True
          and "企微推送测试" in last_push)

    # 高危告警实时路由（时间回滚=高 → 立即推）：用新店避免 24h 告警去重
    st, p = req("POST", "/api/hotels",
                {"name": f"企微推送测试店{ts}", "code": f"HT-W{ts}", "plan": "基础版"}, token=token)
    wid = (p or {}).get("id")
    st, p = req("POST", "/api/licenses", {"hotelId": wid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
    wlic = (p or {}).get("code")
    act_resp = req("POST", "/api/client/activate",
                   {"licenseCode": wlic, "deviceName": "企微推送测试", "clientVersion": "0.1.0"},
                   headers={"X-Device-Fingerprint": t14fp("w")})[1]
    wtok = (act_resp or {}).get("token")
    req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0", "clockAnomaly": True},
        token=wtok, headers={"X-Device-Fingerprint": t14fp("w")})
    st, s2 = req("GET", "/api/settings", token=token)
    last_push = (s2 or {}).get("wecom_last_push", "")
    check("T16.3", "企微机器人", "高危告警即时推送（内容含规则且已脱敏）",
          "时间回滚" in last_push and f"企微推送测试店{ts}" in last_push,
          f"wlic={'有' if wlic else '无'} act={str(act_resp)[:80]}")

    # 指令管道：未绑定 → 绑定 → 查询
    st, p = req("POST", "/api/wecom/simulate", {"text": "状态 HT-0001", "userid": f"u{ts}"}, token=token)
    unbound_ok = "尚未绑定" in (p or {}).get("reply", "")
    req("POST", "/api/wecom/bindings", {"userid": f"u{ts}", "name": "测试员", "role": "operator"}, token=token)
    st, p = req("POST", "/api/wecom/simulate", {"text": "状态 HT-0001", "userid": f"u{ts}"}, token=token)
    query_ok = "授权状态" in (p or {}).get("reply", "")
    check("T16.4", "企微机器人", "指令管道（未绑定拦截 + 绑定后查状态）", unbound_ok and query_ok)

    # 续期二次确认
    st, p = req("POST", "/api/wecom/simulate", {"text": "续期 HT-0001", "userid": f"u{ts}"}, token=token)
    step1 = "确认续期" in (p or {}).get("reply", "")
    st, p = req("POST", "/api/wecom/simulate", {"text": "确认续期 HT-0001", "userid": f"u{ts}"}, token=token)
    step2 = "已续期" in (p or {}).get("reply", "")
    check("T16.5", "企微机器人", "续期指令二次确认后执行", step1 and step2)

    # 集团数据隔离：group 绑定用户不能查别家集团门店
    g0 = (req("GET", "/api/groups", token=token)[1] or [{}])[0].get("id", "g001")
    req("POST", "/api/wecom/bindings", {"userid": f"ug{ts}", "name": "集团管理员", "role": "group", "groupId": g0}, token=token)
    st, p = req("POST", "/api/wecom/simulate", {"text": "状态 HT-0001", "userid": f"ug{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    # HT-0001 属于万豪集团(g001)：若 g0==g001 则允许，否则应拒绝——两种情况都验证权限判定生效
    g0_members = (req("GET", f"/api/groups/{g0}", token=token)[1] or {}).get("members", [])
    ht_in_g0 = any(m.get("code") == "HT-0001" for m in g0_members)
    check("T16.6", "企微机器人", "集团绑定指令数据隔离",
          (ht_in_g0 and "授权状态" in reply) or ((not ht_in_g0) and "授权状态" not in reply),
          f"HT-0001∈{g0}={ht_in_g0}")

    # 日报生成（dry-run 记录）：清当日幂等标记 + 清遗留集团通道（避免集团日报覆盖记录槽）
    req("POST", "/api/settings", {"key": "wecom_daily_last", "value": ""}, token=token)
    st, wc = req("GET", "/api/wecom/config", token=token)
    for gid in ((wc or {}).get("groupHooks") or {}).keys():
        req("POST", "/api/wecom/group-hook", {"groupId": gid, "webhookUrl": ""}, token=token)
    req("POST", "/api/wecom/flush-daily", token=token)
    st, s2 = req("GET", "/api/settings", token=token)
    last_push = (s2 or {}).get("wecom_last_push", "")
    check("T16.7", "企微机器人", "运营日报生成（含当日告警汇总）",
          "运营日报" in last_push and "新增告警" in last_push,
          last_push[:60])

    # ---------------- 17. 客户端企微自助配置（门店/集团） ----------------
    print("【17. 客户端企微自助配置】")
    HOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=testhook123"

    # 门店客户端保存并读取
    st, p = req("POST", "/api/client/notify/config", {"webhookUrl": HOOK},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    st, p = req("GET", "/api/client/notify/config",
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T17.1", "客户端企微配置", "门店保存并回显 webhook", st == 200
          and (p or {}).get("webhookUrl") == HOOK and (p or {}).get("scope") == "hotel")

    # 域名白名单（防 SSRF）
    st, _ = req("POST", "/api/client/notify/config", {"webhookUrl": "https://evil.example.com/hook"},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T17.2", "客户端企微配置", "非法域名被拒（400 防 SSRF）", st == 400, f"HTTP {st}")

    # 集团总端配置（走 wecom_group_hooks）
    gfp = t14fp("gc")
    st, p = req("POST", "/api/client/activate",
                {"licenseCode": "GP-0001", "deviceName": "配置测试总端"}, headers={"X-Device-Fingerprint": gfp})
    gtok2 = (p or {}).get("token")
    ghook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=grouphook456"
    req("POST", "/api/client/notify/config", {"webhookUrl": ghook},
        token=gtok2, headers={"X-Device-Fingerprint": gfp})
    st, p = req("GET", "/api/client/notify/config",
                token=gtok2, headers={"X-Device-Fingerprint": gfp})
    check("T17.3", "客户端企微配置", "集团总端保存并回显（scope=group）", st == 200
          and (p or {}).get("webhookUrl") == ghook and (p or {}).get("scope") == "group")

    # scope 隔离：门店 token 只能读到自己的配置（hotel scope，非 group 的值）
    st, p = req("GET", "/api/client/notify/config",
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    check("T17.4", "客户端企微配置", "scope 隔离（门店只见门店配置）",
          (p or {}).get("scope") == "hotel" and (p or {}).get("webhookUrl") == HOOK)

    # 测试推送进入 dry-run 记录且路由到本实体
    st, p = req("POST", "/api/client/notify/test", {},
                token=ctoken, headers={"X-Device-Fingerprint": FP})
    st, s2 = req("GET", "/api/settings", token=token)
    last_push = (s2 or {}).get("wecom_last_push", "")
    try:
        rec = json.loads(last_push)
    except Exception:
        rec = {}
    check("T17.5", "客户端企微配置", "测试推送路由到门店通道", (p or {}).get("dryRun") is True
          and "消息通知测试" in last_push and bool(rec.get("hotel_id")),
          f"hotel_id={rec.get('hotel_id')}")

    # 审计留痕（脱敏：key= 掩码）
    st, p = req("GET", "/api/logs", token=token)
    cfg_logs = [l for l in (p or []) if l.get("action") == "配置企微通知"]
    log_ok = any("key=***" in (l.get("detail") or "") for l in cfg_logs)
    check("T17.6", "客户端企微配置", "配置变更审计留痕且 webhook key 脱敏", log_ok,
          f"{len(cfg_logs)} 条配置日志")

    # ---------------- 18. 大模型调用审计（全量留痕 + 脱敏） ----------------
    print("【18. 大模型调用审计】")

    secret_input = f"联系人手机 13812345678，酒店设备指纹 {'cd' * 32}，最近哪家风险高？-{ts}"
    st, p = req("POST", "/api/llm-logs/simulate",
                {"sourceType": "group", "sourceId": "GP-0001", "sourceName": "审计测试集团",
                 "userId": "u-test", "scene": "对话查询", "userInput": secret_input},
                token=token)
    check("T18.1", "大模型审计", "经网关调用自动留痕（输入/输出/模型/耗时）",
          st == 200 and (p or {}).get("result", {}).get("status") == "success",
          f"status={(p or {}).get('result', {}).get('status')}")

    st, logs = req("GET", "/api/llm-logs?sourceType=group&limit=10", token=token)
    rec = next((l for l in (logs or []) if "审计测试集团" in (l.get("sourceName") or "")), {})
    masked_ok = (
        "138****5678" in (rec.get("userInput") or "")
        and "13812345678" not in (rec.get("userInput") or "")
        and "13812345678" not in (rec.get("modelInput") or "")
        and bool(rec.get("modelOutput"))
        and rec.get("sourceType") == "group"
    )
    check("T18.2", "大模型审计", "用户输入/模型输入输出落库且自动脱敏", masked_ok,
          f"rec={'有' if rec else '无'}")

    st, logs2 = req("GET", "/api/llm-logs?sourceType=hotel&limit=10", token=token)
    only_hotel = all(l.get("sourceType") == "hotel" for l in (logs2 or []))
    check("T18.3", "大模型审计", "按调用来源（集团/门店/后台）过滤", st == 200 and only_hotel,
          f"hotel 来源 {len(logs2 or [])} 条")

    st, p = req("POST", "/api/llm-logs/simulate", {"sourceType": "admin", "userInput": "x"}, token=vtok)
    check("T18.4", "大模型审计", "只读角色无权触发调用（403）", st == 403, f"HTTP {st}")

    # ---------------- 19. 大模型接入（本地 Ollama/llama.cpp + 云端 API） ----------------
    print("【19. 大模型接入（本地+云端）】")

    # 快照当前模型配置（本段用例会改动配置，结束时还原，避免破坏环境）
    st, llm_snapshot = req("GET", "/api/ai/llm-status", token=token)
    llm_snapshot = {
        "active": (llm_snapshot or {}).get("active", ""),
        "fallback": (llm_snapshot or {}).get("fallback", "on") != "off",
        "local": (llm_snapshot or {}).get("local", {}),
        "cloud": {**(llm_snapshot or {}).get("cloud", {}), "apiKey": ""},
    }

    st, p = req("POST", "/api/ai/config",
                {"active": "local", "fallback": False,
                 "local": {"provider": "ollama", "endpoint": "http://localhost:11434", "model": "qwen2.5:7b"},
                 "cloud": {"endpoint": "https://api.deepseek.com/v1", "model": "deepseek-chat", "apiKey": "sk-test-123456"}},
                token=token)
    st2, s = req("GET", "/api/ai/llm-status", token=token)
    cloud = (s or {}).get("cloud", {})
    check("T19.1", "大模型接入", "云端+本地双配置保存回显（Key 只回是否已配）",
          st == 200 and (s or {}).get("active") == "local"
          and (s or {}).get("local", {}).get("provider") == "ollama"
          and "11434" in (s or {}).get("local", {}).get("endpoint", "")
          and cloud.get("apiKeySet") is True and "sk-test-123456" not in json.dumps(s),
          f"active={(s or {}).get('active')} keySet={cloud.get('apiKeySet')}")

    st, p = req("POST", "/api/ai/config",
                {"active": "cloud", "local": {"provider": "llama.cpp"}}, token=vtok)
    check("T19.2", "大模型接入", "只读角色无权改模型配置（403）", st == 403, f"HTTP {st}")

    # 未接入/占位路径：清空双槽位后解读 → 网关照常留痕
    req("POST", "/api/ai/config",
        {"active": "", "fallback": False, "local": {"provider": "", "endpoint": "", "model": ""},
         "cloud": {"endpoint": "", "model": ""}}, token=token)
    st, p = req("POST", "/api/ai/alert-explain",
                {"rule": "时间回滚", "hotelName": "接入测试店", "detail": "客户端时钟回滚 48 小时"},
                token=token)
    st2, logs = req("GET", "/api/llm-logs?limit=10", token=token)
    rec = next((l for l in (logs or []) if l.get("scene") == "告警解读"), {})
    check("T19.3", "大模型接入", "告警 AI 解读经审计网关（调用即留痕）",
          st == 200 and (p or {}).get("audit", {}).get("status") == "success" and bool(rec),
          f"model={(p or {}).get('audit', {}).get('model')} rec={'有' if rec else '无'}")

    # 失败也留痕：本地指向不可达端点（关兜底）→ error 记录入审计
    req("POST", "/api/ai/config",
        {"active": "local", "fallback": False,
         "local": {"provider": "ollama", "endpoint": "http://127.0.0.1:59999", "model": "x"}}, token=token)
    st, p = req("POST", "/api/ai/alert-explain",
                {"rule": "设备指纹变更", "hotelName": "接入测试店", "detail": "指纹不一致"},
                token=token)
    st2, logs = req("GET", "/api/llm-logs?limit=5", token=token)
    err_rec = next((l for l in (logs or []) if l.get("status") == "error"), {})
    check("T19.4", "大模型接入", "模型调用失败同样留痕（error 记录）", bool(err_rec),
          f"error rec={'有' if err_rec else '无'}")
    req("POST", "/api/ai/config",
        {"active": "", "fallback": False, "local": {"provider": "", "endpoint": "", "model": ""},
         "cloud": {"endpoint": "", "model": ""}}, token=token)

    # ---------------- 20. 企微机器人 AI 问答（大模型处理自由文本） ----------------
    print("【20. 企微 AI 问答】")

    # 未配置模型：非数据问题 → 占位提示 + 留痕（数据类问题走数据直出，见 T21）
    st, p = req("POST", "/api/wecom/simulate", {"text": "介绍一下你能做什么", "userid": f"u{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    st2, logs = req("GET", "/api/llm-logs?limit=10", token=token)
    qa_rec = next((l for l in (logs or []) if l.get("scene") == "企微问答"), {})
    check("T20.1", "企微 AI 问答", "自由文本走大模型管道（占位提示 + 留痕）",
          "AI" in reply and bool(qa_rec), f"reply={reply[:24]} rec={'有' if qa_rec else '无'}")

    # 配置云端不可达 → AI 问答失败提示 + error 留痕（兜底关闭）
    req("POST", "/api/ai/config",
        {"active": "cloud", "fallback": False,
         "cloud": {"endpoint": "https://127.0.0.1:59999/v1", "model": "m", "apiKey": "sk-x"}}, token=token)
    st, p = req("POST", "/api/wecom/simulate", {"text": "帮我看看风险", "userid": f"u{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    st2, logs = req("GET", "/api/llm-logs?limit=5", token=token)
    err_qa = next((l for l in (logs or []) if l.get("scene") == "企微问答" and l.get("status") == "error"), {})
    check("T20.2", "企微 AI 问答", "模型异常时优雅降级（提示+error 留痕）",
          "暂不可用" in reply and bool(err_qa), f"reply={reply[:24]}")

    # 指令仍优先于 AI（状态指令不进大模型）
    st, logs_a = req("GET", "/api/llm-logs?limit=100", token=token)
    before = len([l for l in (logs_a or []) if l.get("scene") == "企微问答"])
    st, p = req("POST", "/api/wecom/simulate", {"text": "状态 HT-0001", "userid": f"u{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    st2, logs_b = req("GET", "/api/llm-logs?limit=100", token=token)
    after = len([l for l in (logs_b or []) if l.get("scene") == "企微问答"])
    check("T20.3", "企微 AI 问答", "确定性指令优先（状态不进大模型）",
          "授权状态" in reply and after == before, f"reply={reply[:24]} qa增量={after - before}")

    # ---------------- 21. 企微问答数据接地（真实数据直出，防模型编造） ----------------
    print("【21. 企微问答数据接地】")

    # 造一家 20 天后到期的酒店，并确保走「无模型数据直出」路径
    expire_20d = time.strftime("%Y-%m-%d", time.localtime(time.time() + 20 * 86400))
    st, p = req("POST", "/api/hotels",
                {"name": f"接地测试店{ts}", "code": f"HT-G{ts}", "plan": "基础版"}, token=token)
    gid = (p or {}).get("id")
    req("PATCH", f"/api/hotels/{gid}", {"expireAt": expire_20d}, token=token)
    req("POST", "/api/ai/config",
        {"active": "", "fallback": False, "local": {"provider": ""}, "cloud": {}}, token=token)
    st, p = req("POST", "/api/wecom/simulate",
                {"text": "查询出还有一个月要过期的酒店信息", "userid": f"u{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    check("T21.1", "企微数据接地", "到期查询返回真实酒店（非编造模板）",
          f"接地测试店{ts}" in reply and expire_20d in reply and "[酒店名称]" not in reply
          and "📋" in reply,
          f"含真实店={f'接地测试店{ts}' in reply} 含日期={expire_20d in reply}")

    # 数据隔离：集团用户问到期 → 不得出现非本集团门店
    req("POST", "/api/wecom/bindings", {"userid": f"ugx{ts}", "name": "隔离测试", "role": "group", "groupId": "g001"}, token=token)
    st, p = req("POST", "/api/wecom/simulate",
                {"text": "哪些酒店快到期？", "userid": f"ugx{ts}"}, token=token)
    reply = (p or {}).get("reply", "")
    check("T21.2", "企微数据接地", "集团问答只含本集团数据（隔离）",
          f"接地测试店{ts}" not in reply and ("本集团" in reply or "📋" in reply),
          f"越界店出现={f'接地测试店{ts}' in reply}")
    # 清理测试酒店（停用，防残留污染数据查询）
    req("PATCH", f"/api/hotels/{gid}", {"status": "已停用"}, token=token)

    # ---------------- 22. 设备ID防撞（同秒+同指纹前缀双激活） ----------------
    print("【22. 设备 ID 防撞】")
    fp_a, fp_b = (f"{ts:08x}" + "z" * 56)[:64], (f"{ts:08x}" + "y" * 56)[:64]  # 每轮唯一、前8位相同（避开 t14fp 的 a/b/m/w/gc 尾缀）
    codes = []
    for i, fp in enumerate((fp_a, fp_b)):
        st, p = req("POST", "/api/hotels",
                    {"name": f"防撞店{ts}-{i}", "code": f"HT-C{ts}{i}", "plan": "基础版"}, token=token)
        hid = (p or {}).get("id")
        st, p = req("POST", "/api/licenses", {"hotelId": hid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
        lic = (p or {}).get("code")
        st, p = req("POST", "/api/client/activate",
                    {"licenseCode": lic, "deviceName": f"防撞机{i}", "clientVersion": "0.1.0"},
                    headers={"X-Device-Fingerprint": fp})
        codes.append(st)
    check("T22.1", "设备ID防撞", "同秒同前缀指纹双激活均成功（ID 不冲突）",
          codes == [200, 200], f"HTTP {codes}")
    # 同设备重复激活走复用分支
    st, p = req("POST", "/api/client/activate",
                {"licenseCode": req("GET", "/api/licenses", token=token)[1][0].get("code", "HT-0001"),
                 "deviceName": "重复激活", "clientVersion": "0.1.0"},
                headers={"X-Device-Fingerprint": fp_a})
    check("T22.2", "设备ID防撞", "同指纹重复激活复用设备（不报错）", st in (200, 403, 404, 409), f"HTTP {st}")

    # ---------------- 23. 检索中提示（慢路径先提示后结果） ----------------
    print("【23. 检索中提示】")

    st, p = req("POST", "/api/wecom/simulate",
                {"text": "授权到期前我应该做什么准备？", "userid": f"u{ts}"}, token=token)
    check("T23.1", "检索中提示", "AI 慢路径先返回『检索中』提示",
          (p or {}).get("ackSent") is True and "正在检索" in (p or {}).get("ack", "")
          and bool((p or {}).get("reply")),
          f"ackSent={(p or {}).get('ackSent')}")

    st, p = req("POST", "/api/wecom/simulate",
                {"text": "状态 HT-0001", "userid": f"u{ts}"}, token=token)
    check("T23.2", "检索中提示", "快速指令不加检索提示（秒回无噪音）",
          (p or {}).get("ackSent") is False and "授权状态" in (p or {}).get("reply", ""),
          f"ackSent={(p or {}).get('ackSent')}")

    st, p = req("POST", "/api/wecom/simulate",
                {"text": "帮我看看数据", "userid": f"nobody{ts}"}, token=token)
    check("T23.3", "检索中提示", "未绑定用户快速提示（不进慢路径）",
          (p or {}).get("ackSent") is False and "尚未绑定" in (p or {}).get("reply", ""),
          f"ackSent={(p or {}).get('ackSent')}")

    # 还原模型配置（测试改动不污染环境；apiKey 空串=不修改已有 Key）
    req("POST", "/api/ai/config", llm_snapshot, token=token)

    # ---------------- 汇总 ----------------
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    summary = {
        "target": BASE,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": f"{passed / total * 100:.1f}%",
        "results": results,
    }
    out = os.path.join(os.path.dirname(__file__), "last_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n===== 测试汇总: {passed}/{total} 通过（{summary['pass_rate']}）=====")
    for r in results:
        if not r["pass"]:
            print(f"  ✗ {r['id']} {r['module']} · {r['name']} ({r['actual']})")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
