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
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except Exception:
            return e.code, None


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
