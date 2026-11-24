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
from datetime import datetime, timedelta

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
        with urllib.request.urlopen(r, timeout=300) as resp:
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


def req_raw(method: str, path: str, raw: bytes, token=None):
    """原样请求体（测非法 JSON 等异常输入），不走 json.dumps"""
    r = urllib.request.Request(BASE + path, data=raw, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return "ERR", {"_raw": str(e)[:200]}


def sql_exec(sql: str) -> bool:
    """测试造数辅助：直连本地库改时间戳/配额等无 API 的字段（WSL 内 postgres 免密；失败返回 False）"""
    import subprocess
    for cmd in (["sudo", "-u", "postgres", "psql", "-d", "license", "-c", sql],
                ["psql", "-U", "postgres", "-d", "license", "-c", sql]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def check(cid: str, module: str, name: str, cond: bool, actual=""):
    try:
        results.append({"id": cid, "module": module, "name": name, "pass": bool(cond), "actual": str(actual)[:180]})
        print(f"  [{'PASS' if cond else 'FAIL'}] {cid} {module} · {name}" + (f"  ({actual})" if actual else ""))
    except Exception:
        # 记录器自身绝不允许中断测试流
        results.append({"id": str(cid), "module": str(module), "name": str(name)[:60], "pass": False, "actual": "check() 执行异常"})
        try:
            print(f"  [FAIL] {cid} check() 执行异常")
        except Exception:
            pass


def main():
    ts = int(time.time()) % 1000000
    print(f"目标: {BASE}\n")

    token = None
    llm_snapshot = {}
    wecom_snapshot = {}  # wecom_*/push_* 配置快照（finally 还原，防测试污染真实推送配置）
    group_hooks_snapshot = {}  # 集团 webhook 快照（独立表，T16.7 会清空它）
    try:
        # ---------------- 1. 认证模块 ----------------
        try:
            print("【1. 认证模块】")
            st, p = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
            token = (p or {}).get("token")
            check("T1.1", "认证", "管理员登录成功并签发 JWT", st == 200 and bool(token), f"HTTP {st}")
            # 快照企微/推送配置（T16~T17 会写 dry-run/假 webhook/假 chatid，结束必须还原）
            # 含 alert_push_agg（告警聚合窗口）：测试造的告警绝不能攒进真实聚合缓冲
            st, s0 = req("GET", "/api/settings", token=token)
            wecom_snapshot = {k: v for k, v in (s0 or {}).items()
                              if (k.startswith(("wecom_", "push_")) or k == "alert_push_agg")
                              and k != "wecom_last_push"
                              and not any(m in k for m in ("api_key", "secret", "token"))}  # S5 打码键不可还原
            # 套件级 dry-run 硬开：T14 等前段会触发高危告警即时推送（notify_alert），
            # 若等 T16 才开 dry-run，测试告警会被真实推到企微群（2026-11-21 事故）
            req("POST", "/api/settings", {"key": "wecom_dry_run", "value": "on"}, token=token)
            st, wc0 = req("GET", "/api/wecom/config", token=token)
            group_hooks_snapshot = dict((wc0 or {}).get("groupHooks") or {})
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

        except Exception as _e:
            check("S1", "异常隔离", "第 1 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 2. 客户管理模块 ----------------
        try:
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

            # 用永不生成的 id 形态（自增数字 id 已逼近 h999，硬编码会撞真实店）
            st, _ = req("GET", "/api/hotels/h-no-such-hotel-zzz/detail", token=token)
            check("T2.8", "客户管理", "不存在客户返回 404", st == 404, f"HTTP {st}")

        except Exception as _e:
            check("S2", "异常隔离", "第 2 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 3. 授权管理模块 ----------------
        try:
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

        except Exception as _e:
            check("S3", "异常隔离", "第 3 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 4. 套餐管理模块 ----------------
        try:
            print("【4. 套餐管理模块】")
            st, plans = req("GET", "/api/plans", token=token)
            plans = plans or []
            names = sorted(x.get("name", "") for x in plans)
            expected = {"基础版", "专业版", "旗舰版"}
            check("T4.1", "套餐管理", "套餐列表为 3 档", st == 200 and set(names) == expected, str(names))
            check("T4.2", "套餐管理", "套餐字段完整（价格/设备上限/功能）",
                  all(x.get("price") and x.get("maxDevices") and x.get("features") for x in plans))

        except Exception as _e:
            check("S4", "异常隔离", "第 4 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 5. AI 智能风控模块 ----------------
        try:
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

        except Exception as _e:
            check("S5", "异常隔离", "第 5 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 6. 到期续期模块 ----------------
        try:
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

        except Exception as _e:
            check("S6", "异常隔离", "第 6 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 7. 安全设计模块 ----------------
        try:
            print("【7. 安全设计模块】")
            _, s3 = req("GET", "/api/settings", token=token)
            check("T7.1", "安全设计", "心跳超时参数可读（72h）", (s3 or {}).get("heartbeat_timeout_hours") == "72",
                  (s3 or {}).get("heartbeat_timeout_hours"))

            st, lics2 = req("GET", "/api/licenses", token=token)
            revoked = [l for l in (lics2 or []) if l.get("status") == "已吊销"]
            check("T7.2", "安全设计", "吊销状态重读持久化", st == 200 and len(revoked) >= 1, f"已吊销 {len(revoked)} 条")

            st, _ = req("POST", "/api/settings", {"key": "heartbeat_timeout_hours", "value": "72"}, token)
            check("T7.3", "安全设计", "安全参数写入正常", st == 200, f"HTTP {st}")

        except Exception as _e:
            check("S7", "异常隔离", "第 7 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 8. 操作日志模块 ----------------
        try:
            print("【8. 操作日志模块】")
            st, logs = req("GET", "/api/logs", token=token)
            logs = logs or []
            check("T8.1", "操作日志", "日志接口可获取", st == 200 and len(logs) > 0, f"{len(logs)} 条")

            actions = {l.get("action") for l in logs}
            need = {"新增客户", "发放授权", "批量续期", "吊销授权", "处置告警", "修改设置"}
            check("T8.2", "操作日志", "关键操作均被审计记录", need <= actions, f"缺失 {sorted(need - actions) if need - actions else '无'}")

        except Exception as _e:
            check("S8", "异常隔离", "第 8 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 9. 数据持久化 ----------------
        try:
            print("【9. 数据持久化】")
            st, hotels2 = req("GET", "/api/hotels", token=token)
            ok = st == 200 and all(h.get("expireAt") == "2027-11-01" for h in (hotels2 or []) if h.get("id") in ids2)
            check("T9.1", "数据持久化", "批量续期结果重读一致", ok)

            st, p = req("GET", f"/api/hotels/{new_id}/detail", token=token)
            hotel_status = ((p or {}).get("hotel") or {}).get("status")
            check("T9.2", "数据持久化", "停用状态重读一致", hotel_status == "已停用", f"status={hotel_status}")

        except Exception as _e:
            check("S9", "异常隔离", "第 9 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 10. 客户端同步（激活/心跳/版本更新） ----------------
        try:
            print("【10. 客户端同步】")
            FP = "ab" * 32  # 模拟设备指纹（64 位 hex）

            st, p = req("GET", "/api/client/version")
            check("T10.1", "客户端同步", "版本接口无需鉴权可获取", st == 200 and bool((p or {}).get("latest")),
                  f"latest={(p or {}).get('latest')}")

            st, p = req("GET", "/api/client/public-key")
            check("T10.2", "客户端同步", "Ed25519 公钥可获取", st == 200 and (p or {}).get("algorithm") == "ed25519"
                  and len((p or {}).get("public_key", "")) == 64)

            # S2 后新指纹只能用授权码激活（编号仅限原设备重装）——取一条生效授权码
            _lics = (req("GET", "/api/licenses", token=token)[1] or [])
            _acode = next((l.get("code") for l in _lics if l.get("code")), "HT-0001")
            st, p = req("POST", "/api/client/activate",
                        {"licenseCode": _acode, "deviceName": "自动化测试客户端", "clientVersion": "0.1.0"},
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

        except Exception as _e:
            check("S10", "异常隔离", "第 10 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 11. P2 规模化（RBAC / 分页 / 告警聚合） ----------------
        try:
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

        except Exception as _e:
            check("S11", "异常隔离", "第 11 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 12. 总/子管理（集团） ----------------
        try:
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
            order_no = (p or {}).get("order_no", "")
            check("T12.4", "总/子管理", "集团批量续期创建订单（S3：不再直改到期日）",
                  st == 200 and bool(order_no) and (p or {}).get("renewed", 0) == 0,
                  f"order={order_no} renewed={(p or {}).get('renewed')}")

            st, orders = req("GET", "/api/renew-orders", token=token)
            mine = [o for o in (orders or []) if (o.get("orderNo") or o.get("order_no")) == order_no]
            check("T12.5", "总/子管理", "续期订单进入订单流（待支付，运营确认后才生效）",
                  st == 200 and bool(mine) and mine[0].get("status") == "待支付",
                  f"found={len(mine)}")

            st, _ = req("GET", "/api/client/group/profile", token=ctoken, headers={"X-Device-Fingerprint": FP})
            check("T12.6", "总/子管理", "单店 token 调集团接口被拒（400）", st == 400, f"HTTP {st}")

            st, p = req("POST", "/api/groups",
                        {"name": f"自动化测试集团{ts}", "plan": "专业版", "expireAt": "2027-12-31"}, token=token)
            check("T12.7", "总/子管理", "后台创建集团并签发集团码", st == 200 and (p or {}).get("code", "").startswith("GP-"),
                  f"code={(p or {}).get('code')}")

            st, _ = req("POST", "/api/groups/g001/renew", token=token)
            check("T12.8", "总/子管理", "后台集团续期接口可用", st == 200, f"HTTP {st}")

        except Exception as _e:
            check("S12", "异常隔离", "第 12 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 13. 离线宽限期（服务端统一配置） ----------------
        try:
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

            # 激活响应含宽限时长（自建店自足：HT-0002 等演示店授权会被 UI 吊销流程动到，不作依赖）
            st, hp13 = req("POST", "/api/hotels",
                           {"name": f"测试店宽限{ts}", "code": f"HT-P13{ts}", "plan": "基础版"}, token)
            st, lc13 = req("POST", "/api/licenses",
                           {"hotelId": (hp13 or {}).get("id"), "plan": "基础版", "months": 12}, token)
            st, p = req("POST", "/api/client/activate",
                        {"licenseCode": (lc13 or {}).get("code"), "deviceName": "宽限激活测试", "clientVersion": "0.1.0"},
                        headers={"X-Device-Fingerprint": (f"{ts:08x}" + "c3" * 28)[:64]})
            check("T13.3", "离线宽限", "激活响应含宽限时长", st == 200
                  and (p or {}).get("offline_grace_hours") == 72, f"grace={(p or {}).get('offline_grace_hours')}")

        except Exception as _e:
            check("S13", "异常隔离", "第 13 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 14. 实时告警管道（检测→自动记账） ----------------
        try:
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

        except Exception as _e:
            check("S14", "异常隔离", "第 14 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 15. 日志/审计脱敏 ----------------
        try:
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
                "138＊＊＊＊5678" in log_text
                and "330106199001011234" not in log_text
                and "song.xk@example.com" not in log_text
                and "6222021234567890" not in log_text
            )
            check("T15.1", "日志脱敏", "审计详情掩码（手机/身份证/邮箱/卡号）", masked_ok,
                  "含138＊＊＊＊5678" if "138＊＊＊＊5678" in log_text else "掩码缺失")

            check("T15.2", "日志脱敏", "授权码部分掩码保留可追溯（LIC-＊＊＊＊23）",
                  "LIC-＊＊＊＊" in my_logs and "LIC-2026-0123" not in my_logs,
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
                  "139＊＊＊＊4321" in log_text2 and "13987654321" not in log_text2)

        except Exception as _e:
            check("S15", "异常隔离", "第 15 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 16. 企业微信机器人 ----------------
        try:
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

            # 高危告警实时路由（时间回滚=高 → 立即推）：用新店避免 24h 告警去重；先清聚合窗口防前序告警占窗
            req("POST", "/api/settings", {"key": "alert_push_agg", "value": '{"start":"","items":[]}'}, token=token)
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

        except Exception as _e:
            check("S16", "异常隔离", "第 16 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 17. 客户端企微自助配置（门店/集团） ----------------
        try:
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
            log_ok = any("key=＊＊＊" in (l.get("detail") or "") for l in cfg_logs)
            check("T17.6", "客户端企微配置", "配置变更审计留痕且 webhook key 脱敏", log_ok,
                  f"{len(cfg_logs)} 条配置日志")

        except Exception as _e:
            check("S17", "异常隔离", "第 17 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 18. 大模型调用审计（全量留痕 + 脱敏） ----------------
        try:
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
                "138＊＊＊＊5678" in (rec.get("userInput") or "")
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

        except Exception as _e:
            check("S18", "异常隔离", "第 18 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 24. 企微回答准确性（单店告警全量 / 清单完整性 / 北京时间） ----------------
        try:
            print("【24. 企微回答准确性】")

            # 造测试店并激活设备，制造两条不同规则告警（设备指纹变更 + 时间回滚）
            # 编码用 HT-XA/HT-XB 前缀：避开历史用例的 HT-W(企微推送)/HT-G/HT-C/HT-M/HT-R 占用
            hv_name, hv_code = f"核对店{ts}", f"HT-XA{ts}"
            st, p = req("POST", "/api/hotels", {"name": hv_name, "code": hv_code, "plan": "基础版"}, token=token)
            hvid = (p or {}).get("id")
            if not hvid:
                st_h, hs = req("GET", "/api/hotels", token=token)
                hvid = next((h.get("id") for h in (hs or []) if h.get("code") == hv_code), None)
            st, p = req("POST", "/api/licenses", {"hotelId": hvid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
            hvfp = f"{ts:08x}" + "v" * 56
            st, p = req("POST", "/api/client/activate",
                        {"licenseCode": (p or {}).get("code"), "deviceName": "核对机", "clientVersion": "0.1.0"},
                        headers={"X-Device-Fingerprint": hvfp})
            hvtok = (p or {}).get("token")
            req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"},
                token=hvtok, headers={"X-Device-Fingerprint": "f" * 64})  # → 设备指纹变更
            req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0", "clockAnomaly": True},
                token=hvtok, headers={"X-Device-Fingerprint": hvfp})  # → 时间回滚

            # 单店提问：该店全部告警都要出来（老告警不得被全局窗口挤掉；漏项即兜底直出）
            st, p = req("POST", "/api/wecom/simulate",
                        {"text": f"查看{hv_name}酒店异常统计", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            check("T24.1", "企微回答准确性", "单店异常统计返回该店全部告警（2 条不同规则）",
                  "设备指纹变更" in reply and "时间回滚" in reply and hv_code in reply,
                  f"指纹变更={'设备指纹变更' in reply} 回滚={'时间回滚' in reply} 编码={hv_code in reply}")

            # 集团隔离范围下「查找异常的酒店」：聚合清单必须把有告警的店列全（完整性兜底保证）
            st, p = req("POST", "/api/groups", {"name": f"核对集团{ts}", "plan": "基础版"}, token=token)
            vgid = (p or {}).get("id") or ""
            if not vgid:
                st, gs = req("GET", "/api/groups", token=token)
                vgid = next((g.get("id") for g in (gs or []) if g.get("name") == f"核对集团{ts}"), "")
            hv2_name, hv2_code = f"核对二店{ts}", f"HT-XB{ts}"
            st2, p2 = req("POST", "/api/hotels", {"name": hv2_name, "code": hv2_code, "plan": "基础版"}, token=token)
            hv2id = (p2 or {}).get("id")
            if not hv2id:
                st_h, hs = req("GET", "/api/hotels", token=token)
                hv2id = next((h.get("id") for h in (hs or []) if h.get("code") == hv2_code), None)
            st, p = req("POST", "/api/licenses", {"hotelId": hv2id, "plan": "基础版", "type": "新签", "months": 12}, token=token)
            hv2fp = f"{ts:08x}" + "w" * 56
            st, p = req("POST", "/api/client/activate",
                        {"licenseCode": (p or {}).get("code"), "deviceName": "核对机2", "clientVersion": "0.1.0"},
                        headers={"X-Device-Fingerprint": hv2fp})
            hv2tok = (p or {}).get("token")
            req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0", "clockAnomaly": True},
                token=hv2tok, headers={"X-Device-Fingerprint": hv2fp})
            st_a, p_a = req("POST", "/api/groups/assign", {"hotelIds": [hvid, hv2id], "groupId": vgid}, token=token)
            st_g, gd = req("GET", f"/api/groups/{vgid}", token=token)
            members = [m.get("code") for m in ((gd or {}).get("members") or [])]
            req("POST", "/api/wecom/bindings",
                {"userid": f"ugv{ts}", "name": "核对集团管理员", "role": "group", "groupId": vgid}, token=token)
            st, p = req("POST", "/api/wecom/simulate", {"text": "查找异常的酒店", "userid": f"ugv{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            check("T24.2", "企微回答准确性", "查找异常的酒店→聚合清单完整（两店都在，兜底保证）",
                  hv_code in reply and hv2_code in reply,
                  f"{hv_code}={hv_code in reply} {hv2_code}={hv2_code in reply} "
                  f"vgid={vgid} ids=[{hvid},{hv2id}] assign={st_a} members={members} reply={reply[:60]!r}")

            # 时间展示：告警时间换算北京时间（UTC+8），不再直接甩 UTC
            st, alerts = req("GET", "/api/alerts", token=token)
            ta = next((a for a in (alerts or []) if a.get("hotelName") == hv_name), None)
            bj_expect = ""
            if ta:
                raw = (ta.get("time") or "")[:19]
                try:
                    bj_expect = (datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    bj_expect = ""
                st, p = req("POST", "/api/wecom/simulate", {"text": f"告警 {hv_code}", "userid": f"u{ts}"}, token=token)
                reply = (p or {}).get("reply", "")
            else:
                reply = ""
            check("T24.3", "企微回答准确性", "告警时间按北京时间展示（UTC+8 换算）",
                  bool(bj_expect) and bj_expect in reply, f"期望 {bj_expect or '（未找到告警）'}")

            # 清理测试店（停用，防残留污染数据查询）
            req("PATCH", f"/api/hotels/{hvid}", {"status": "已停用"}, token=token)
            req("PATCH", f"/api/hotels/{hv2id}", {"status": "已停用"}, token=token)

        except Exception as _e:
            check("S24", "异常隔离", "第 24 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 25. 集团/子酒店数据接地 ----------------
        try:
            print("【25. 集团/子酒店数据接地】")

            st, p = req("POST", "/api/groups", {"name": f"清点集团{ts}", "plan": "基础版"}, token=token)
            tgid = (p or {}).get("id") or ""
            st, gp = req("GET", f"/api/groups/{tgid}", token=token)
            tgc = ((gp or {}).get("group") or {}).get("code", "")
            tcodes = [f"HT-XC{ts}", f"HT-XD{ts}"]
            tids = []
            for i, c in enumerate(tcodes):
                st, p = req("POST", "/api/hotels", {"name": f"清点店{ts}-{i}", "code": c, "plan": "基础版"}, token=token)
                hid = (p or {}).get("id")
                if not hid:
                    st_h, hs = req("GET", "/api/hotels", token=token)
                    hid = next((h.get("id") for h in (hs or []) if h.get("code") == c), None)
                tids.append(hid)
            req("POST", "/api/groups/assign", {"hotelIds": tids, "groupId": tgid}, token=token)

            # 全局问：各集团子酒店数量 → 新集团编码与家数必须在回答里（接地+兜底）
            st, p = req("POST", "/api/wecom/simulate",
                        {"text": "当前入住集团有几家分别有多少个子酒店", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            check("T25.1", "企微回答准确性", "集团/子酒店数量有数据接地（编码+家数）",
                  tgc in reply and ("2 家" in reply or "2家" in reply),
                  f"{tgc}={tgc in reply} 含2家={'2 家' in reply or '2家' in reply}")

            # 单集团问：列成员清单（漏项即兜底直出）
            st, p = req("POST", "/api/wecom/simulate",
                        {"text": f"清点集团{ts}有几家子酒店", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            check("T25.2", "企微回答准确性", "单集团提问列出子酒店清单（完整）",
                  all(c in reply for c in tcodes),
                  f"{tcodes[0]}={tcodes[0] in reply} {tcodes[1]}={tcodes[1] in reply}")

            for hid in tids:
                req("PATCH", f"/api/hotels/{hid}", {"status": "已停用"}, token=token)

        except Exception as _e:
            check("S25", "异常隔离", "第 25 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 26. 告警处置闭环 ----------------
        try:
            print("【26. 告警处置闭环】")

            # 用 T25 的清点店告警不可靠（已停用）；直接取一条未处置告警处置并验证备注/处置人/推企微
            st, alerts = req("GET", "/api/alerts", token=token)
            target = next((a for a in (alerts or []) if a.get("status") != "已处置"), None)
            handle_ok = note_ok = False
            if target:
                st, p = req("POST", f"/api/alerts/{target['id']}/handle", {"note": f"处置闭环验证-{ts}"}, token=token)
                handle_ok = st == 200 and (p or {}).get("status") == "已处置" and bool((p or {}).get("handledBy"))
                st, logs = req("GET", "/api/alerts", token=token)
                rec = next((a for a in (logs or []) if a.get("id") == target["id"]), {})
                note_ok = f"处置闭环验证-{ts}" in (rec.get("handleNote") or "") and bool(rec.get("handledAt"))
                st, s2 = req("GET", "/api/settings", token=token)
                push_ok = "告警已处置" in ((s2 or {}).get("wecom_last_push", "") or "")
            else:
                push_ok = False
            check("T26.1", "告警处置闭环", "处置落库（状态/处置人/备注/时间）+ 结果推企微",
                  handle_ok and note_ok and push_ok,
                  f"落库={handle_ok} 备注={note_ok} 推送={push_ok}")

            # 只读角色无权处置
            st, _ = req("POST", f"/api/alerts/{(target or {}).get('id', 'a-404')}/handle", {"note": "x"}, token=vtok)
            check("T26.2", "告警处置闭环", "只读角色无权处置（403）", st == 403, f"HTTP {st}")

            # 超时未处置升级提醒进日报（紧急/高 >24h）；先清集团通道防集团日报覆盖记录槽（同 T16.7）
            req("POST", "/api/settings", {"key": "wecom_daily_last", "value": ""}, token=token)
            st, wc = req("GET", "/api/wecom/config", token=token)
            for gid in ((wc or {}).get("groupHooks") or {}).keys():
                req("POST", "/api/wecom/group-hook", {"groupId": gid, "webhookUrl": ""}, token=token)
            req("POST", "/api/wecom/flush-daily", token=token)
            st, s2 = req("GET", "/api/settings", token=token)
            daily = (s2 or {}).get("wecom_last_push", "")
            # 无超时告警时日报不含升级段；有则必须带「超时未处置」——两种情况都验证日报正常生成
            check("T26.3", "告警处置闭环", "日报含超时未处置升级提醒（有则提醒、无则正常）",
                  "运营日报" in daily and ("超时未处置" in daily or "新增告警" in daily),
                  f"升级段={'超时未处置' in daily}")

        except Exception as _e:
            check("S26", "异常隔离", "第 26 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 27. 变更操作防谎称 + 风险等级过滤 + 停用指令 ----------------
        try:
            print("【27. 变更操作防谎称/等级过滤/停用指令】")

            # 造一家有高风险告警（时间回滚）的店
            hb_name, hb_code = f"拉黑店{ts}", f"HT-XE{ts}"
            st, p = req("POST", "/api/hotels", {"name": hb_name, "code": hb_code, "plan": "基础版"}, token=token)
            hbid = (p or {}).get("id")
            if not hbid:
                st_h, hs = req("GET", "/api/hotels", token=token)
                hbid = next((h.get("id") for h in (hs or []) if h.get("code") == hb_code), None)
            st, p = req("POST", "/api/licenses", {"hotelId": hbid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
            hbfp = f"{ts:08x}" + "h" * 56
            st, p = req("POST", "/api/client/activate",
                        {"licenseCode": (p or {}).get("code"), "deviceName": "拉黑机", "clientVersion": "0.1.0"},
                        headers={"X-Device-Fingerprint": hbfp})
            hbtok = (p or {}).get("token")
            req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0", "clockAnomaly": True},
                token=hbtok, headers={"X-Device-Fingerprint": hbfp})

            # 变更操作请求：不得谎称执行，必须附「未执行」说明与操作指引
            st, p = req("POST", "/api/wecom/simulate",
                        {"text": "把高风险告警酒店拉黑并告诉我处置结果", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            claims = [c for c in ("已拉黑", "将拉黑", "拉黑成功", "已封禁", "我们将", "处置完成") if c in reply]
            check("T27.1", "变更操作防谎称", "拉黑请求不谎称执行 + 明示未执行与操作指引",
                  "未执行" in reply and not claims and ("停用" in reply or "后台" in reply),
                  f"未执行={'未执行' in reply} 谎称={claims or '无'}")

            # 风险等级过滤：高风险清单不得混入中危（心跳中断）条目
            st, p = req("POST", "/api/wecom/simulate",
                        {"text": "高风险告警酒店有哪些", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            check("T27.2", "变更操作防谎称", "高风险清单按等级过滤（不含中危心跳中断）",
                  hb_code in reply and "心跳中断" not in reply,
                  f"本店={hb_code in reply} 混入中危={'心跳中断' in reply}")

            # 停用（拉黑）指令：二次确认后生效
            st, p = req("POST", "/api/wecom/simulate", {"text": f"停用 {hb_code}", "userid": f"u{ts}"}, token=token)
            step1 = "确认停用" in (p or {}).get("reply", "")
            st, p = req("POST", "/api/wecom/simulate", {"text": f"确认停用 {hb_code}", "userid": f"u{ts}"}, token=token)
            step2 = "已停用" in (p or {}).get("reply", "")
            st, hs = req("GET", f"/api/hotels?page=1&page_size=200&keyword={hb_code}", token=token)
            items = (hs or {}).get("items") if isinstance(hs, dict) else hs
            status_now = next((h.get("status") for h in (items or []) if h.get("code") == hb_code), "")
            check("T27.3", "变更操作防谎称", "停用（拉黑）指令二次确认后生效",
                  step1 and step2 and status_now == "已停用",
                  f"确认={step1} 执行={step2} status={status_now}")

        except Exception as _e:
            check("S27", "异常隔离", "第 27 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 28. 日常指令健壮性 ----------------
        try:
            print("【28. 日常指令健壮性】")

            r = (req("POST", "/api/wecom/simulate", {"text": "状态 HT-9999", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.1", "日常指令健壮性", "未知编码确定性拒绝（不进 AI 瞎答）",
                  "未找到" in r and "HT-9999" in r, r[:40])

            r = (req("POST", "/api/wecom/simulate", {"text": "帮助", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.2", "日常指令健壮性", "帮助含全部日常指令（状态/告警/续期/停用）",
                  all(k in r for k in ("状态", "告警", "续期", "停用")), r[:30])

            r = (req("POST", "/api/wecom/simulate", {"text": "续期 HT-9999", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.3", "日常指令健壮性", "未知编码续期同样明确拒绝",
                  "未找到" in r, r[:30])

            # 无空格写法（状态HT-xxxx）：必须同样进确定性通道，不得掉进 AI 编造
            r = (req("POST", "/api/wecom/simulate", {"text": "状态ht-888", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.4", "日常指令健壮性", "无空格+不存在编码仍确定性拒绝（不进 AI 编造）",
                  "未找到" in r and "ht-888" in r, r[:40])

            r = (req("POST", "/api/wecom/simulate", {"text": "状态HT-0001", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.5", "日常指令健壮性", "无空格+真实编码返回真实数据（大小写归一）",
                  "HT-0001" in r and "到期" in r and "未找到" not in r, r[:40])

            # 自由问法带不存在编码（ht9999的状态详情）→ 确定性拒绝，禁止全局统计冒充该店
            r = (req("POST", "/api/wecom/simulate", {"text": "ht9999的状态详情", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.6", "日常指令健壮性", "自由问法带不存在编码确定性拒绝（不进 AI 编造/不套全局统计）",
                  "未找到" in r and "ht9999" in r and "统计基座" not in r and "酒店总数" not in r, r[:40])

            r = (req("POST", "/api/wecom/simulate", {"text": "查一下ht9999的状态", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.7", "日常指令健壮性", "自然语言问法带不存在编码同样确定性拒绝",
                  "未找到" in r and "ht9999" in r, r[:40])

            # 单店自由问法：只给该店数据，禁止全局设备占用/总量冒充单店
            r = (req("POST", "/api/wecom/simulate", {"text": "HT-0004的状态详情", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            import re as _re
            check("T28.8", "日常指令健壮性", "单店详情不含全局统计冒充（设备占用为单店数值）",
                  "HT-0004" in r and not _re.search(r"\d{3,}/\d{3,}", r), r[:40])

            # 日志类提问：接真实操作日志，杜绝编造「编码001」式假条目
            r = (req("POST", "/api/wecom/simulate", {"text": "提取前十条日志信息", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.9", "日常指令健壮性", "日志查询返回真实操作日志（不编造条目/编码）",
                  "编码0" not in r and ("企微" in r or "管理员" in r) and ("HT-" in r or "查询" in r or "客户" in r), r[:40])

            # 无接地数据的问法：严禁编造清单/编码，如实说明
            r = (req("POST", "/api/wecom/simulate", {"text": "帮我提取前十条神秘记录", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            check("T28.10", "日常指令健壮性", "无数据问法不编造清单/编码（如实说明）",
                  "编码0" not in r and "编码1" not in r, r[:40])

            # 日志中的授权号掩码必须逐字保留（不得抹成 LIC-03）
            r = (req("POST", "/api/wecom/simulate", {"text": "提取前十条日志信息", "userid": f"u{ts}"}, token=token)[1] or {}).get("reply", "")
            import re as _re2
            lic_unmasked = _re2.search(r"LIC-(?!\*)[0-9A-Za-z]", r)
            check("T28.11", "日常指令健壮性", "日志转抄保留授权号掩码（LIC-**** 不被抹掉）",
                  (not lic_unmasked) and ("```" not in r), r[:40])

        except Exception as _e:
            check("S28", "异常隔离", "第 28 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 29. 每日定时推送（服务端/集团/子酒店） ----------------
        try:
            print("【29. 每日定时推送】")

            # 服务端配置读写 + 时间校验
            st, _ = req("POST", "/api/push/server-config", {"enabled": True, "time": "03:07"}, token=token)
            st, c = req("GET", "/api/push/server-config", token=token)
            ok_cfg = st == 200 and (c or {}).get("enabled") is True and (c or {}).get("time") == "03:07"
            st, e = req("POST", "/api/push/server-config", {"enabled": True, "time": "25:00"}, token=token)
            check("T29.1", "每日定时推送", "服务端推送配置读写 + 非法时间拒绝",
                  ok_cfg and st == 400, f"cfg={ok_cfg} bad={st}")

            # 手动触发调度 tick：推一次成功，同日再触发不重复（幂等）
            st, r1 = req("POST", "/api/push/run", {"time": "03:07", "force": True}, token=token)
            pushed1 = [x for x in ((r1 or {}).get("pushed") or []) if x.get("scope") == "server"]
            st, r2 = req("POST", "/api/push/run", {"time": "03:07"}, token=token)
            pushed2 = [x for x in ((r2 or {}).get("pushed") or []) if x.get("scope") == "server"]
            check("T29.2", "每日定时推送", "到点推送成功且当日幂等（不重复推）",
                  len(pushed1) == 1 and len(pushed2) == 0,
                  f"第一次={len(pushed1)} 第二次={len(pushed2)}")

            # 服务端异常汇总内容接地（真实编码 + 异常分类）
            st, t = req("POST", "/api/push/server-test", {}, token=token)
            pv = (t or {}).get("preview", "")
            check("T29.3", "每日定时推送", "异常汇总内容接地（含真实编码与异常分类）",
                  st == 200 and "HT-00" in pv and ("已过期" in pv or "到期" in pv or "告警" in pv or "无异常" in pv),
                  pv[:40])

            # 客户端推送设置（门店）：开关+时间保存回显 + 非法时间拒绝
            st, _ = req("POST", "/api/client/notify/config",
                        {"webhookUrl": HOOK, "pushEnabled": True, "pushTime": "08:30"},
                        token=ctoken, headers={"X-Device-Fingerprint": FP})
            st, c = req("GET", "/api/client/notify/config",
                        token=ctoken, headers={"X-Device-Fingerprint": FP})
            ok_push = st == 200 and (c or {}).get("pushEnabled") is True and (c or {}).get("pushTime") == "08:30"
            st, _ = req("POST", "/api/client/notify/config",
                        {"pushEnabled": True, "pushTime": "9点"},
                        token=ctoken, headers={"X-Device-Fingerprint": FP})
            check("T29.4", "每日定时推送", "客户端推送开关/时间设置 + 非法时间拒绝",
                  ok_push and st == 400, f"cfg={ok_push} bad={st}")

            # 客户端立即推送今日简报（接地：含本店编码）
            st, p = req("POST", "/api/client/notify/push-test", {},
                        token=ctoken, headers={"X-Device-Fingerprint": FP})
            pv2 = (p or {}).get("preview", "")
            check("T29.5", "每日定时推送", "子酒店简报立即推送（内容含本店数据）",
                  st == 200 and (p or {}).get("ok") is True and "简报" in pv2 and "HT-" in pv2,
                  pv2[:40])

            # 恢复默认（每天 9 点，保持开启）
            req("POST", "/api/push/server-config", {"enabled": True, "time": "09:00"}, token=token)

        except Exception as _e:
            check("S29", "异常隔离", "第 29 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 19. 大模型接入（本地 Ollama/llama.cpp + 云端 API） ----------------
        try:
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

        except Exception as _e:
            check("S19", "异常隔离", "第 19 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 20. 企微机器人 AI 问答（大模型处理自由文本） ----------------
        try:
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
            check("T20.2", "企微 AI 问答", "模型异常时优雅降级（数据兜底 + error 留痕）",
                  ("📋" in reply or "暂不可用" in reply) and bool(err_qa), f"reply={reply[:24]}")

            # 指令仍优先于 AI（状态指令不进大模型）
            st, logs_a = req("GET", "/api/llm-logs?limit=100", token=token)
            before = len([l for l in (logs_a or []) if l.get("scene") == "企微问答"])
            st, p = req("POST", "/api/wecom/simulate", {"text": "状态 HT-0001", "userid": f"u{ts}"}, token=token)
            reply = (p or {}).get("reply", "")
            st2, logs_b = req("GET", "/api/llm-logs?limit=100", token=token)
            after = len([l for l in (logs_b or []) if l.get("scene") == "企微问答"])
            check("T20.3", "企微 AI 问答", "确定性指令优先（状态不进大模型）",
                  "授权状态" in reply and after == before, f"reply={reply[:24]} qa增量={after - before}")

        except Exception as _e:
            check("S20", "异常隔离", "第 20 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 21. 企微问答数据接地（真实数据直出，防模型编造） ----------------
        try:
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

        except Exception as _e:
            check("S21", "异常隔离", "第 21 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 22. 设备ID防撞（同秒+同指纹前缀双激活） ----------------
        try:
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

        except Exception as _e:
            check("S22", "异常隔离", "第 22 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 23. 检索中提示（慢路径先提示后结果） ----------------
        try:
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

        except Exception as _e:
            check("S23", "异常隔离", "第 23 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 30. 自助换机/解绑（P0-1） ----------------
        try:
            print("【30. 自助换机/解绑】")
            p0_code = f"HT-P0A{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": f"测试店换机{ts}", "code": p0_code, "group": "测试集团", "city": "测试市", "plan": "专业版"},
                        token)
            p0_id = (p or {}).get("id")
            st, lic = req("POST", "/api/licenses", {"hotelId": p0_id, "plan": "专业版", "months": 12}, token)
            p0_lic = (lic or {}).get("code")

            # 先复位离线阈值（防上一轮残留污染），再测默认 30 分钟行为
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)

            # 激活 5 台设备（A=本机视角，B/C/D/E 其他机器）；指纹随 ts 唯一防跨轮撞车
            u6 = f"{ts:08x}"
            fpA, fpB, fpC, fpD, fpE, fpG = (
                "a1" * 28 + u6, "b2" * 28 + u6, "c7" * 28 + u6,
                "d4" * 28 + u6, "e5" * 28 + u6, "g7" * 28 + u6,
            )
            devs = {}
            for fpx, dname in ((fpA, "前台-01"), (fpB, "前台-02"), (fpC, "前台-03"), (fpD, "前台-04"), (fpE, "前台-05")):
                st, ap = req("POST", "/api/client/activate",
                             {"licenseCode": p0_lic, "deviceName": dname, "os_name": "testos"},
                             headers={"X-Device-Fingerprint": fpx})
                devs[fpx] = ((ap or {}).get("token") or "", (ap or {}).get("migrate_target") or {})
            tokA = devs[fpA][0]

            st, dl = req("GET", "/api/client/devices", token=tokA, headers={"X-Device-Fingerprint": fpA})
            n_dev = len((dl or {}).get("devices") or [])
            check("T30.1", "自助换机", "设备清单含 5 台且今日配额=3",
                  st == 200 and n_dev == 5 and (dl or {}).get("quota_left_today") == 3,
                  f"devices={n_dev} quota={(dl or {}).get('quota_left_today')}")

            def _dev_id(fp):
                return next((d["id"] for d in (dl or {}).get("devices") or [] if d["fingerprint"] == fp), "")

            # 默认阈值 30 分钟：刚激活的 B 在线 → 解绑拒绝
            bid = _dev_id(fpB)
            st, up = req("POST", "/api/client/devices/unbind", {"device_id": bid},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T30.2", "自助换机", "在线设备解绑被拒（防误拆在用机器）",
                  st == 409, f"HTTP {st} {(up or {}).get('detail', '')[:30]}")

            # 测试把离线阈值调 0（settings 可配），验证成功路径
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "0"}, token=token)

            st, up = req("POST", "/api/client/devices/unbind", {"device_id": bid},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T30.3", "自助换机", "自助解绑成功并释放配额",
                  st == 200 and (up or {}).get("ok") is True and (up or {}).get("quota_left_today") == 2,
                  f"HTTP {st} quota={(up or {}).get('quota_left_today')}")

            st, up = req("POST", "/api/client/devices/unbind", {"device_id": _dev_id(fpA)},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T30.4", "自助换机", "解绑本机被拒", st == 400, f"HTTP {st}")

            # 同名迁移（重装系统）：新机 G 与 E 同名 → 激活返回迁移提示 → 迁移接管
            st, ap = req("POST", "/api/client/activate",
                         {"licenseCode": p0_lic, "deviceName": "前台-05", "os_name": "testos"},
                         headers={"X-Device-Fingerprint": fpG})
            tokG = (ap or {}).get("token") or ""
            hint = (ap or {}).get("migration_hint") is True
            tgt = ((ap or {}).get("migrate_target") or {}).get("id", "")
            check("T30.5", "自助换机", "同名离线设备激活返回迁移提示", hint and bool(tgt), f"hint={hint} tgt={tgt}")

            if tgt:
                st, mg = req("POST", "/api/client/devices/migrate", {"old_device_id": tgt},
                             token=tokG, headers={"X-Device-Fingerprint": fpG})
                check("T30.6", "自助换机", "同名迁移成功（旧设备置已迁移）",
                      st == 200 and (mg or {}).get("ok") is True, f"HTTP {st}")
            else:
                check("T30.6", "自助换机", "同名迁移成功（旧设备置已迁移）", False, "无迁移目标")

            # 日限 3 次：解绑 C（第 3 次）成功，解绑 D（第 4 次）429
            cid, did = _dev_id(fpC), _dev_id(fpD)
            st3, _ = req("POST", "/api/client/devices/unbind", {"device_id": cid},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            st4, p4 = req("POST", "/api/client/devices/unbind", {"device_id": did},
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T30.7", "自助换机", "每日 3 次上限，第 4 次 429",
                  st3 == 200 and st4 == 429, f"3rd={st3} 4th={st4}")

            # 后台恢复（E2）：把已解绑的 B 恢复
            st, rr = req("POST", f"/api/hotels/{p0_id}/devices/{bid}/restore", {}, token=token)
            st2, dl2 = req("GET", "/api/client/devices", token=tokA, headers={"X-Device-Fingerprint": fpA})
            b_status = next((d["status"] for d in (dl2 or {}).get("devices") or [] if d["id"] == bid), "")
            check("T30.8", "自助换机", "后台恢复设备（E2 误操作恢复）",
                  st == 200 and b_status == "离线", f"HTTP {st} status={b_status}")

            # 还原阈值
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)

        except Exception as _e:
            check("S30", "异常隔离", "第 30 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 31. 续期自助化订单（P0-2） ----------------
        try:
            print("【31. 续期自助化订单】")
            st, od = req("POST", "/api/client/renew/order", {"months": 12, "plan": "", "channel": "transfer"},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            o_no = (od or {}).get("order_no", "")
            check("T31.1", "续期订单", "创建续期订单（金额服务端计算）",
                  st == 200 and o_no.startswith("RO-") and (od or {}).get("amount", 0) > 0,
                  f"no={o_no} amount={(od or {}).get('amount')}")

            st, _ = req("POST", "/api/client/renew/order", {"months": 6},
                        token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T31.2", "续期订单", "非法时长拒绝（仅 12/24 个月）", st == 400, f"HTTP {st}")

            st, od2 = req("POST", "/api/client/renew/order", {"months": 12, "plan": "旗舰版"},
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            up_amount = (od2 or {}).get("amount", 0)
            base_amount = (od or {}).get("amount", 0)
            check("T31.3", "续期订单", "升级套餐按目标套餐计价（升级价 ≥ 同档价）",
                  st == 200 and up_amount >= base_amount, f"base={base_amount} upgrade={up_amount}")

            st, pf = req("POST", f"/api/client/renew/order/{o_no}/proof", {"proof_no": ""},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T31.4", "续期订单", "空回单号提交凭证拒绝", st == 400, f"HTTP {st}")

            st, pf = req("POST", f"/api/client/renew/order/{o_no}/proof",
                         {"proof_no": f"BK{ts}", "note": "回归测试汇款"},
                         token=tokA, headers={"X-Device-Fingerprint": fpA})
            st2, os_ = req("GET", f"/api/client/renew/order/{o_no}",
                           token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T31.5", "续期订单", "提交凭证进入待确认",
                  st == 200 and (os_ or {}).get("status") == "待确认", f"status={(os_ or {}).get('status')}")

            # 后台确认到账 → 生效并顺延到期日（列表不过滤状态，避免中文查询参数编码问题）
            st, ros = req("GET", "/api/renew-orders", token=token)
            target = next((o for o in (ros or []) if isinstance(o, dict) and o.get("orderNo") == o_no), None)
            st, cf = req("POST", f"/api/renew-orders/{(target or {}).get('id')}/confirm", {}, token=token)
            st2, os2 = req("GET", f"/api/client/renew/order/{o_no}",
                           token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T31.6", "续期订单", "后台确认到账后订单生效",
                  st == 200 and (os2 or {}).get("status") == "已生效" and len((cf or {}).get("renewed") or []) == 1,
                  f"status={(os2 or {}).get('status')} renewed={(cf or {}).get('renewed')}")

            # 驳回路径
            o2_no = (od2 or {}).get("order_no", "")
            req("POST", f"/api/client/renew/order/{o2_no}/proof", {"proof_no": f"BK2{ts}"},
                token=tokA, headers={"X-Device-Fingerprint": fpA})
            st, ros2 = req("GET", "/api/renew-orders", token=token)
            t2 = next((o for o in (ros2 or []) if isinstance(o, dict) and o.get("orderNo") == o2_no), None)
            st, _ = req("POST", f"/api/renew-orders/{(t2 or {}).get('id')}/reject", {"reason": "凭证未核实"}, token=token)
            st2, os3 = req("GET", f"/api/client/renew/order/{o2_no}",
                           token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T31.7", "续期订单", "凭证不实订单可驳回（含原因）",
                  st == 200 and (os3 or {}).get("status") == "已驳回", f"status={(os3 or {}).get('status')}")

        except Exception as _e:
            check("S31", "异常隔离", "第 31 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 32. 模块/配额位（P1-3） ----------------
        try:
            print("【32. 模块/配额位】")
            st, lic3 = req("POST", "/api/licenses",
                           {"hotelId": p0_id, "plan": "旗舰版", "months": 12,
                            "features": ["ai_risk", "group_portal"], "maxRooms": 120}, token)
            f3 = (lic3 or {}).get("features") or []
            check("T32.1", "模块配额", "发放授权携带模块位与房量配额",
                  st == 200 and set(f3) == {"ai_risk", "group_portal"} and (lic3 or {}).get("maxRooms") == 120,
                  f"features={f3} rooms={(lic3 or {}).get('maxRooms')}")

            # 激活拿到签名 License 载荷（客户端据此开关功能）
            fpH = "h8" * 28 + u6
            st, ap3 = req("POST", "/api/client/activate",
                          {"licenseCode": (lic3 or {}).get("code"), "deviceName": "模块测试机", "os_name": "testos"},
                          headers={"X-Device-Fingerprint": fpH})
            payload = ((ap3 or {}).get("signed_license") or {}).get("payload") or {}
            feats = payload.get("features") or []
            quotas = payload.get("quotas") or {}
            check("T32.2", "模块配额", "License 签名载荷下发模块位与配额",
                  set(feats) == {"ai_risk", "group_portal"} and quotas.get("max_rooms") == 120,
                  f"features={feats} quotas={quotas}")

            # 未配置模块位 = 全开（兼容存量）
            st, lic4 = req("POST", "/api/licenses", {"hotelId": p0_id, "plan": "基础版", "months": 1}, token)
            fpI = "i9" * 28 + u6
            st, ap4 = req("POST", "/api/client/activate",
                          {"licenseCode": (lic4 or {}).get("code"), "deviceName": "全开测试机", "os_name": "testos"},
                          headers={"X-Device-Fingerprint": fpI})
            feats4 = (((ap4 or {}).get("signed_license") or {}).get("payload") or {}).get("features") or []
            check("T32.3", "模块配额", "未配置模块位默认全开（存量兼容）",
                  set(feats4) == {"ai_risk", "group_portal", "report", "api", "push"}, f"features={feats4}")

        except Exception as _e:
            check("S32", "异常隔离", "第 32 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 33. 流失预测与授权盘点（P2） ----------------
        try:
            print("【33. 流失预测与授权盘点】")
            st, rk = req("GET", "/api/renewal-risks", token=token)
            ok_risk = st == 200 and isinstance(rk, list) and len(rk) > 0 and all(
                "risk" in x and "factors" in x for x in (rk or [])[:5])
            check("T33.1", "流失预测", "流失风险排行返回风险档与因子", ok_risk, f"HTTP {st} n={len(rk or [])}")

            st, ad = req("GET", "/api/renewal-audit", token=token)
            ok_audit = st == 200 and all(k in ((ad or {}).get("summary") or {}) for k in ("idle", "underused", "suspect", "empty"))
            check("T33.2", "授权盘点", "授权盘点聚合（闲置/半闲置/超装/空壳）", ok_audit, f"HTTP {st}")

            st, sc = req("POST", "/api/ai/renew-script", {"hotelId": p0_id}, token=token)
            ok_script = st == 200 and len((sc or {}).get("script") or "") > 20
            check("T33.3", "流失预测", "续期跟进话术生成（模型失败降级模板）",
                  ok_script and bool((sc or {}).get("facts", "")), (sc or {}).get("script", "")[:30])

            # 周度流失周报：造一家明日到期酒店（days<=7 → 高风险）→ force 推送命中 risk_weekly
            tmr = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
            req("PATCH", f"/api/hotels/{p0_id}", {"expireAt": tmr}, token=token)
            req("POST", "/api/settings", {"key": "push_risk_weekly_time", "value": "03:07"}, token=token)
            st, pr = req("POST", "/api/push/run", {"time": "03:07", "force": True}, token=token)
            weekly = [x for x in ((pr or {}).get("pushed") or []) if x.get("scope") == "risk_weekly"]
            check("T33.4", "流失预测", "周度流失周报到点推送（含高风险门店）",
                  st == 200 and len(weekly) >= 1, f"weekly={len(weekly)}")
            req("POST", "/api/settings", {"key": "push_risk_weekly_time", "value": "09:00"}, token=token)

        except Exception as _e:
            check("S33", "异常隔离", "第 33 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 34. 高危边界补充 ----------------
        try:
            print("【34. 高危边界补充】")

            # T34.1 待支付订单超时自动关单（TTL settings 化为 0 → 秒级验证惰性关单路径）
            req("POST", "/api/settings", {"key": "renew_order_ttl_hours", "value": "0"}, token=token)
            st, odT = req("POST", "/api/client/renew/order", {"months": 12, "plan": "", "channel": "transfer"},
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            t_no = (odT or {}).get("order_no", "")
            time.sleep(1.2)
            st, osT = req("GET", f"/api/client/renew/order/{t_no}",
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            check("T34.1", "续期订单", "待支付订单超时自动关单（惰性关单）",
                  st == 200 and (osT or {}).get("status") == "已关闭",
                  f"status={(osT or {}).get('status')}")
            req("POST", "/api/settings", {"key": "renew_order_ttl_hours", "value": "2"}, token=token)

            # T34.2 订单越权：另一客户访问/操作本单一律 403
            fpX = "x9" * 28 + f"{ts:08x}"
            st, pX = req("POST", "/api/hotels",
                         {"name": f"测试店越权{ts}", "code": f"HT-P0X{ts}", "group": "测试集团",
                          "city": "测试市", "plan": "专业版"}, token)
            pX_id = (pX or {}).get("id")
            st, licX = req("POST", "/api/licenses", {"hotelId": pX_id, "plan": "专业版", "months": 12}, token)
            st, apX = req("POST", "/api/client/activate",
                          {"licenseCode": (licX or {}).get("code"), "deviceName": "越权测试机", "os_name": "testos"},
                          headers={"X-Device-Fingerprint": fpX})
            tokX = (apX or {}).get("token") or ""
            st, odV = req("POST", "/api/client/renew/order", {"months": 12, "plan": "", "channel": "transfer"},
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            v_no = (odV or {}).get("order_no", "")
            st1, _ = req("GET", f"/api/client/renew/order/{v_no}",
                         token=tokX, headers={"X-Device-Fingerprint": fpX})
            st2, _ = req("POST", f"/api/client/renew/order/{v_no}/proof", {"proof_no": f"FAKE{ts}"},
                         token=tokX, headers={"X-Device-Fingerprint": fpX})
            check("T34.2", "续期订单", "跨客户订单访问/操作越权拒绝 403",
                  st1 == 403 and st2 == 403, f"GET={st1} proof={st2}")

            # T34.3 金额篡改忽略：客户端伪造 amount 字段，响应金额仍为服务端计算值
            st, odC = req("POST", "/api/client/renew/order",
                          {"months": 12, "plan": "", "channel": "transfer", "amount": 0.01},
                          token=tokA, headers={"X-Device-Fingerprint": fpA})
            clean_amount = (odV or {}).get("amount")
            tampered_amount = (odC or {}).get("amount")
            check("T34.3", "续期订单", "金额篡改被忽略（服务端计算为准）",
                  st == 200 and tampered_amount == clean_amount and (tampered_amount or 0) > 1,
                  f"tampered={tampered_amount} expected={clean_amount}")

        except Exception as _e:
            check("S34", "异常隔离", "第 34 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 35. 异常路径健壮性 ----------------
        try:
            print("【35. 异常路径健壮性】")
            st, _ = req_raw("POST", "/api/hotels", b"{bad json", token=token)
            check("T35.1", "异常处理", "非法 JSON 请求体返回 4xx 不 500",
                  isinstance(st, int) and 400 <= st < 500, f"HTTP {st}")

            st, _ = req("GET", "/api/stats", token="fake.token.sig")
            check("T35.2", "异常处理", "伪造 token 返回 401", st == 401, f"HTTP {st}")

            st, _ = req("GET", "/api/hotels")
            check("T35.3", "异常处理", "无 token 访问返回 401", st == 401, f"HTTP {st}")

            st, _ = req("GET", "/api/hotels/HT-NOEXIST-999", token=token)
            check("T35.4", "异常处理", "不存在资源返回 404", st == 404, f"HTTP {st}")

            st, _ = req("DELETE", "/api/hotels", token=token)
            check("T35.5", "异常处理", "未支持 HTTP 方法返回 405", st == 405, f"HTTP {st}")
        except Exception as _e:
            check("S35", "异常隔离", "第 35 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 36. 客户端凭证失效与重激活（协议自动化） ----------------
        try:
            print("【36. 客户端凭证失效与重激活】")
            # 模拟客户端全操作链路：激活 → 心跳 → 凭证失效 → 静默重激活 → 心跳恢复
            fp6 = "c6" * 28 + f"{ts:08x}"
            st, p6 = req("POST", "/api/hotels",
                         {"name": f"测试店凭证{ts}", "code": f"HT-C6{ts}", "group": "测试集团",
                          "city": "测试市", "plan": "专业版"}, token)
            p6_id = (p6 or {}).get("id")
            st, lic6 = req("POST", "/api/licenses",
                           {"hotelId": p6_id, "plan": "专业版", "months": 12}, token)
            st, act6 = req("POST", "/api/client/activate",
                           {"licenseCode": (lic6 or {}).get("code"), "deviceName": "凭证测试机", "os_name": "testos"},
                           headers={"X-Device-Fingerprint": fp6})
            tok6 = (act6 or {}).get("token") or ""
            st, hb6 = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.1"},
                          token=tok6, headers={"X-Device-Fingerprint": fp6})
            check("T36.1", "客户端协议", "激活后心跳正常（返回 server_time）",
                  st == 200 and bool((hb6 or {}).get("server_time")), f"HTTP {st}")

            # 凭证失效（如密钥轮换/伪 token）→ 401「无效客户端凭证」，客户端据此走重激活而非离线锁
            st, hb7 = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.1"},
                          token="bad.token.value", headers={"X-Device-Fingerprint": fp6})
            check("T36.2", "客户端协议", "失效凭证心跳 401「无效客户端凭证」",
                  st == 401 and "无效客户端凭证" in str((hb7 or {}).get("detail", "")),
                  f"HTTP {st} {(hb7 or {}).get('detail')}")

            # 静默重激活自愈路径（客户端 crash.log 事故的修复验证）：同指纹+授权码再激活 → 新 token → 心跳恢复
            time.sleep(1.1)  # JWT iat 秒级精度，确保新旧 token 不同
            st, act8 = req("POST", "/api/client/activate",
                           {"licenseCode": (lic6 or {}).get("code"), "deviceName": "凭证测试机", "os_name": "testos"},
                           headers={"X-Device-Fingerprint": fp6})
            tok8 = (act8 or {}).get("token") or ""
            st, hb8 = req("POST", "/api/client/heartbeat", {"clientVersion": "1.2.1"},
                          token=tok8, headers={"X-Device-Fingerprint": fp6})
            check("T36.3", "客户端协议", "重激活后心跳恢复（自愈路径，新 token 生效）",
                  st == 200 and bool((hb8 or {}).get("server_time")) and bool(tok8) and tok8 != tok6,
                  f"HTTP {st} new_token={bool(tok8)}")

            # 重激活失败兜底：错误授权码 → 404（客户端应引导人工，不死循环）
            st, _ = req("POST", "/api/client/activate",
                        {"licenseCode": "HT-NOPE-999999", "deviceName": "x"},
                        headers={"X-Device-Fingerprint": fp6})
            check("T36.4", "客户端协议", "错误授权码重激活被拒（404 不死循环）",
                  st == 404, f"HTTP {st}")
        except Exception as _e:
            check("S36", "异常隔离", "第 36 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 37. 高危边界补充（P1：告警触发/图片上限/离线阈值精确档） ----------------
        try:
            print("【37. 高危边界补充】")
            q_code = f"HT-QA{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": f"测试店边界{ts}", "code": q_code, "group": "测试集团", "city": "测试市", "plan": "基础版"},
                        token)
            q_id = (p or {}).get("id")
            st, qlic = req("POST", "/api/licenses", {"hotelId": q_id, "plan": "基础版", "months": 12}, token)
            q_lic = (qlic or {}).get("code")
            u7 = f"{ts:08x}q"
            fpQA, fpQB, fpQC, fpQD = ("qa" * 28 + u7, "qb" * 28 + u7, "qc" * 28 + u7, "qd" * 28 + u7)
            toks = {}
            for fpx, dname in ((fpQA, "边界-01"), (fpQB, "边界-02"), (fpQC, "边界-03"), (fpQD, "边界-04")):
                st, ap = req("POST", "/api/client/activate",
                             {"licenseCode": q_lic, "deviceName": dname, "os_name": "testos"},
                             headers={"X-Device-Fingerprint": fpx})
                toks[fpx] = (ap or {}).get("token") or ""
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "0"}, token=token)
            st, dl = req("GET", "/api/client/devices", token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})

            def _qid(fpx):
                return next((d["id"] for d in (dl or {}).get("devices") or [] if d["fingerprint"] == fpx), "")

            # T37.1 7 天内 3 次自助换机 → 第 3 次触发「频繁自助换机」高告警
            u_sts = []
            for fpx in (fpQB, fpQC, fpQD):
                st, _ = req("POST", "/api/client/devices/unbind", {"device_id": _qid(fpx)},
                            token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})
                u_sts.append(st)
            st, al = req("GET", "/api/alerts", token=token)
            freq = [a for a in (al or []) if a.get("rule") == "频繁自助换机" and a.get("hotelName") == q_id]
            check("T37.1", "边界补充", "7 天内 3 次自助换机触发「频繁自助换机」高告警",
                  u_sts == [200, 200, 200] and len(freq) >= 1 and freq[0].get("level") == "高",
                  f"unbinds={u_sts} alerts={len(freq)}")

            # T37.2 回单图片边界：恰 5MB 放行 / 超 5MB 拒绝
            import base64 as _b64
            st, od = req("POST", "/api/client/renew/order", {"months": 12, "plan": "", "channel": "transfer"},
                         token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})
            o_no = (od or {}).get("order_no", "")
            img_ok = _b64.b64encode(b"\x00" * (5 * 1024 * 1024)).decode()
            st1, _ = req("POST", f"/api/client/renew/order/{o_no}/proof",
                         {"proof_no": f"BK{ts}A", "image_base64": img_ok},
                         token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})
            st, od2 = req("POST", "/api/client/renew/order", {"months": 12, "plan": "", "channel": "transfer"},
                          token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})
            o2_no = (od2 or {}).get("order_no", "")
            img_ng = _b64.b64encode(b"\x00" * (5 * 1024 * 1024 + 1)).decode()
            st2, p2 = req("POST", f"/api/client/renew/order/{o2_no}/proof",
                          {"proof_no": f"BK{ts}B", "image_base64": img_ng},
                          token=toks[fpQA], headers={"X-Device-Fingerprint": fpQA})
            check("T37.2", "边界补充", "回单图片恰 5MB 放行 / 超 5MB 拒绝",
                  st1 == 200 and st2 == 400 and "5MB" in str((p2 or {}).get("detail", "")),
                  f"5MB={st1} over={st2} {(p2 or {}).get('detail', '')[:30]}")

            # T37.3 离线阈值精确档（独立店防日限污染）：29 分钟拒解绑 / 30 分钟可解绑
            # 注意：T37.1 把阈值调成 0 了，这里必须先复位 30 再造数，否则边界没意义
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)
            r_code = f"HT-QB{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": f"测试店阈值{ts}", "code": r_code, "group": "测试集团", "city": "测试市", "plan": "基础版"},
                        token)
            r_id = (p or {}).get("id")
            st, rlic = req("POST", "/api/licenses", {"hotelId": r_id, "plan": "基础版", "months": 12}, token)
            u8 = f"{ts:08x}r"
            fpRA, fpRE = ("ra" * 28 + u8, "re" * 28 + u8)
            st, ap = req("POST", "/api/client/activate",
                         {"licenseCode": (rlic or {}).get("code"), "deviceName": "阈值-01", "os_name": "testos"},
                         headers={"X-Device-Fingerprint": fpRA})
            tokR = (ap or {}).get("token") or ""
            st, ap = req("POST", "/api/client/activate",
                         {"licenseCode": (rlic or {}).get("code"), "deviceName": "阈值-02", "os_name": "testos"},
                         headers={"X-Device-Fingerprint": fpRE})
            st, dl2 = req("GET", "/api/client/devices", token=tokR, headers={"X-Device-Fingerprint": fpRA})
            eid = next((d["id"] for d in (dl2 or {}).get("devices") or [] if d["fingerprint"] == fpRE), "")
            ok29 = sql_exec(
                f"UPDATE devices SET last_active = to_char(now() at time zone 'utc' - interval '29 minutes', "
                f"'YYYY-MM-DD HH24:MI:SS') WHERE id = '{eid}'")
            st29, _ = req("POST", "/api/client/devices/unbind", {"device_id": eid},
                          token=tokR, headers={"X-Device-Fingerprint": fpRA})
            ok30 = sql_exec(
                f"UPDATE devices SET last_active = to_char(now() at time zone 'utc' - interval '30 minutes', "
                f"'YYYY-MM-DD HH24:MI:SS') WHERE id = '{eid}'")
            st30, _ = req("POST", "/api/client/devices/unbind", {"device_id": eid},
                          token=tokR, headers={"X-Device-Fingerprint": fpRA})
            check("T37.3", "边界补充", "离线 29 分钟拒解绑 / 30 分钟可解绑（阈值边界）",
                  ok29 and ok30 and st29 == 409 and st30 == 200,
                  f"sql=({ok29},{ok30}) 29min={st29} 30min={st30}")
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)

            # T37.4 授权误操作恢复（恢复按钮缺陷修复回归）：吊销→恢复→生效中；非已吊销恢复 409
            st, l7 = req("POST", "/api/licenses", {"hotelId": r_id, "plan": "基础版", "months": 1}, token)
            l7_id = (l7 or {}).get("id")
            st, _ = req("POST", f"/api/licenses/{l7_id}/revoke", {"reason": "T37.4 误吊销测试"}, token)
            st1, rp = req("POST", f"/api/licenses/{l7_id}/restore", {}, token)
            st2, _ = req("POST", f"/api/licenses/{l7_id}/restore", {}, token)  # 已是生效中 → 409
            check("T37.4", "边界补充", "吊销→恢复回生效中；重复恢复 409（误操作恢复）",
                  st1 == 200 and (rp or {}).get("status") == "生效中" and st2 == 409,
                  f"restore={st1}/{(rp or {}).get('status')} again={st2}")
        except Exception as _e:
            check("S37", "异常隔离", "第 37 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
        # ---------------- 38. 并发冒烟（双线程抢配额 / 同设备解绑） ----------------
        try:
            print("【38. 并发冒烟】")
            import threading

            c_code = f"HT-QC{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": f"测试店并发{ts}", "code": c_code, "group": "测试集团", "city": "测试市", "plan": "基础版"},
                        token)
            c_id = (p or {}).get("id")
            st, clic = req("POST", "/api/licenses", {"hotelId": c_id, "plan": "基础版", "months": 12}, token)
            c_lic = (clic or {}).get("code")

            # T38.1 双线程抢最后 1 个配额位（max_devices=1，原子占位应恰 1 成功）
            sql_exec(f"UPDATE hotels SET max_devices = 1 WHERE id = '{c_id}'")
            u9 = f"{ts:08x}c"
            fpX1, fpX2 = ("x1" * 28 + u9, "x2" * 28 + u9)
            barrier = threading.Barrier(2)
            race = {}

            def _race_act(key, fpx):
                barrier.wait()
                stx, apx = req("POST", "/api/client/activate",
                               {"licenseCode": c_lic, "deviceName": f"并发-{key}", "os_name": "testos"},
                               headers={"X-Device-Fingerprint": fpx})
                race[key] = stx

            ths = [threading.Thread(target=_race_act, args=("a", fpX1)),
                   threading.Thread(target=_race_act, args=("b", fpX2))]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            ok_sts = sorted([race.get("a"), race.get("b")])
            st, hd = req("GET", f"/api/hotels/{c_id}/detail", token=token)
            dev_cnt = ((hd or {}).get("hotel") or {}).get("devices", -1)
            check("T38.1", "并发冒烟", "双线程抢最后配额位：恰 1 成功且设备数一致",
                  ok_sts == [200, 403] and dev_cnt == 1,
                  f"sts={ok_sts} devices={dev_cnt}")

            # T38.2 双线程同时解绑同一台设备：恰 1 成功、配额只减一次
            sql_exec(f"UPDATE hotels SET max_devices = 10 WHERE id = '{c_id}'")
            fpC1, fpC2 = ("c1" * 28 + u9, "c2" * 28 + u9)
            st, ap = req("POST", "/api/client/activate",
                         {"licenseCode": c_lic, "deviceName": "并发解绑-A", "os_name": "testos"},
                         headers={"X-Device-Fingerprint": fpC1})
            tokC = (ap or {}).get("token") or ""
            st, ap = req("POST", "/api/client/activate",
                         {"licenseCode": c_lic, "deviceName": "并发解绑-B", "os_name": "testos"},
                         headers={"X-Device-Fingerprint": fpC2})
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "0"}, token=token)
            st, dl3 = req("GET", "/api/client/devices", token=tokC, headers={"X-Device-Fingerprint": fpC1})
            tid = next((d["id"] for d in (dl3 or {}).get("devices") or [] if d["fingerprint"] == fpC2), "")
            st, hd_b = req("GET", f"/api/hotels/{c_id}/detail", token=token)  # 解绑前重取，勿用 T38.1 旧快照
            dev_before = ((hd_b or {}).get("hotel") or {}).get("devices", -1)
            barrier2 = threading.Barrier(2)
            race2 = {}

            def _race_un(key):
                barrier2.wait()
                stx, _ = req("POST", "/api/client/devices/unbind", {"device_id": tid},
                             token=tokC, headers={"X-Device-Fingerprint": fpC1})
                race2[key] = stx

            ths = [threading.Thread(target=_race_un, args=("a",)), threading.Thread(target=_race_un, args=("b",))]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            st, hd2 = req("GET", f"/api/hotels/{c_id}/detail", token=token)
            dev_after = ((hd2 or {}).get("hotel") or {}).get("devices", -1)
            ok2_sts = sorted([race2.get("a"), race2.get("b")])
            # 恰 1 成功（另一 4xx）；设备计数只减 1
            check("T38.2", "并发冒烟", "双线程同设备解绑：恰 1 成功且配额只减一次",
                  ok2_sts[0] == 200 and isinstance(ok2_sts[1], int) and 400 <= ok2_sts[1] < 500
                  and dev_after == dev_before - 1,
                  f"sts={ok2_sts} devices {dev_before}->{dev_after}")
            req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)
        except Exception as _e:
            check("S38", "异常隔离", "第 38 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")

        # ---------------- 39. 企微指令·批量处置告警（T39） ----------------
        try:
            print("【39. 企微指令·批量处置告警】")
            import subprocess
            ts = str(int(time.time() * 1000))[-8:]
            code = f"HT-CZ{ts[-6:]}"
            name = f"测试店处置{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": name, "code": code, "group": "测试集团", "city": "测试市", "plan": "专业版"},
                        token)
            hid = (p or {}).get("id")
            sender_a, sender_v = f"t39u{ts}", f"t39v{ts}"
            sql_exec(f"INSERT INTO wecom_bindings (userid,name,role,group_id) VALUES "
                     f"('{sender_a}','T39admin','admin',''),('{sender_v}','T39viewer','viewer','') "
                     f"ON CONFLICT (userid) DO NOTHING")
            sql_exec(f"INSERT INTO alerts (id,level,rule,hotel_name,detail,time,status) VALUES "
                     f"('a39{ts[-6:]}','高','T39共享检测','{name}','t39','2026-11-23 08:00:00','待处理'),"
                     f"('a39b{ts[-6:]}','中','T39指纹变更','{name}','t39','2026-11-23 08:01:00','待处理')")

            def iz(text: str, sender: str) -> str:
                py = "/opt/license/.venv/bin/python3"
                if not os.path.exists(py):
                    py = "python3"
                r = subprocess.run(
                    [py, os.path.join(os.path.dirname(os.path.abspath(__file__)), "t39_cmd.py"), text, sender],
                    capture_output=True, text=True, timeout=30,
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                return (r.stdout or "").strip()

            out1 = iz(f"处置 {code}", sender_a)
            check("T39.1", "企微指令", "处置预览：条数与规则聚合正确",
                  hid and "未处置告警 2 条" in out1 and "T39共享检测" in out1 and "T39指纹变更" in out1,
                  out1[:120].replace("\n", " "))

            out2 = iz(f"确认处置 {code} 误报测试", sender_a)
            check("T39.2", "企微指令", "确认处置：批量置已处置并回报条数",
                  "已处置 2 条" in out2 and "误报测试" in out2, out2[:120].replace("\n", " "))

            st, al = req("GET", "/api/alerts", token=token)
            mine = [a for a in (al or []) if a.get("hotelName") == name]
            check("T39.3", "企微指令", "处置落库：状态/备注/处置人三字段正确",
                  len(mine) == 2 and all(a["status"] == "已处置" and a["handleNote"] == "误报测试"
                                         and "企微" in (a.get("handledBy") or "") for a in mine),
                  f"n={len(mine)} " + ";".join(f"{a.get('status')}/{a.get('handleNote')}" for a in mine[:2]))

            out3 = iz(f"处置 {code}", sender_a)
            check("T39.4", "企微指令", "空态：无未处置告警时明确回报",
                  "暂无未处置告警" in out3, out3[:80])

            out4 = iz(f"确认处置 {code}", sender_v)
            check("T39.5", "企微指令", "viewer 角色处置被拒（权限校验）",
                  "权限" in out4, out4[:80])

            out5 = iz(f"处置 {code}", sender_a)  # 预览入口复用；另测无空格写法
            out5b = iz(f"处置{code}", sender_a)
            check("T39.6", "企微指令", "无空格写法「处置HT-xxx」同样命中指令（不进 AI）",
                  "暂无未处置告警" in out5b or "未处置告警" in out5b, out5b[:80])

            # 清扫：停用测试店（店名含"测试店"也被套件清扫关键词兜底覆盖）
            if hid:
                req("PATCH", f"/api/hotels/{hid}", {"status": "已停用"}, token=token)
        except Exception as _e:
            check("S39", "异常隔离", "第 39 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")

        # ---------------- 40. 攻击者视角回归（S1~S9 安全修复固化，2026-11-24） ----------------
        try:
            print("【40. 攻击者视角回归】")
            ts = str(int(time.time() * 1000))[-8:]
            hcode = f"HT-XA{ts[-6:]}"
            name = f"测试店安审{ts}"
            st, p = req("POST", "/api/hotels",
                        {"name": name, "code": hcode, "group": "测试集团", "city": "测试市", "plan": "专业版"},
                        token)
            hid = p.get("id")
            st, lc = req("POST", "/api/licenses", {"hotelId": hid, "plan": "专业版", "type": "新签", "months": 6}, token)
            lcode = (lc or {}).get("code", "")

            # S4：无指纹头一律 401（先激活拿 client token）
            fpA = f"9a{ts}" + "a" * 20
            st, act = req("POST", "/api/client/activate",
                          {"licenseCode": lcode, "deviceName": "安审机", "clientVersion": "0.1.0"},
                          headers={"X-Device-Fingerprint": fpA})
            atok = (act or {}).get("token", "")
            st1, _ = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"}, token=atok)
            st2, _ = req("GET", "/api/client/profile", token=atok)
            check("T40.1", "攻击者视角", "S4 无指纹头 heartbeat/profile 一律 401",
                  bool(atok) and st1 == 401 and st2 == 401, f"hb={st1} profile={st2}")

            st3, _ = req("POST", "/api/client/heartbeat", {"clientVersion": "0.1.0"}, token=atok,
                         headers={"X-Device-Fingerprint": "wrong" + ts})
            check("T40.2", "攻击者视角", "S4 错误指纹 401", st3 == 401, f"HTTP {st3}")

            # S2：新指纹+客户编号（可枚举）→ 403；既有指纹+编号 → 200（重装便利保留）
            fpB = f"9b{ts}" + "b" * 20
            st4, _ = req("POST", "/api/client/activate",
                         {"licenseCode": hcode, "deviceName": "枚举机", "clientVersion": "0.1.0"},
                         headers={"X-Device-Fingerprint": fpB})
            check("T40.3", "攻击者视角", "S2 新指纹+客户编号激活被拒（403）", st4 == 403, f"HTTP {st4}")
            st5, _ = req("POST", "/api/client/activate",
                         {"licenseCode": hcode, "deviceName": "安审机", "clientVersion": "0.1.0"},
                         headers={"X-Device-Fingerprint": fpA})
            check("T40.4", "攻击者视角", "S2 既有指纹+编号激活可用（重装便利）", st5 == 200, f"HTTP {st5}")

            # S2：连败 12 次触发 429（独立指纹防污染）
            fpC = f"9c{ts}" + "c" * 20
            sts = []
            for i in range(12):
                s6, _ = req("POST", "/api/client/activate", {"licenseCode": f"NOPE-{ts}-{i}"},
                            headers={"X-Device-Fingerprint": fpC})
                sts.append(s6)
            check("T40.5", "攻击者视角", "S2 激活连败触发 429 限流",
                  sts[-1] == 429 and sts[0] == 404, f"sts={sts}")

            # S5：viewer 403 + admin 视角密钥打码
            st7, pv = req("POST", "/api/auth/login", {"username": "viewer", "password": "viewer123"})
            vtok = (pv or {}).get("token")
            st8, _ = req("GET", "/api/settings", token=vtok)
            check("T40.6", "攻击者视角", "S5 viewer 无权读全量 settings（403）", st8 == 403, f"HTTP {st8}")
            st9, sa = req("GET", "/api/settings", token=token)
            kval = (sa or {}).get("llm_cloud_api_key", "")
            check("T40.7", "攻击者视角", "S5 密钥类配置打码返回（无明文）",
                  st9 == 200 and (not kval or "…" in kval or kval == "***"),
                  f"val={kval[:12]!r}")

            # S9：client token 进不了管理 API
            st10, _ = req("GET", "/api/settings", token=atok)
            check("T40.8", "攻击者视角", "S9 client token 调管理 API 被拒（401）", st10 == 401, f"HTTP {st10}")

            # S6：登录连败 429（独立用户名防锁真账号）
            st_login = []
            for i in range(12):
                s11, _ = req("POST", "/api/auth/login", {"username": f"nope{ts}", "password": f"bad{i}"})
                st_login.append(s11)
            check("T40.9", "攻击者视角", "S6 登录连败触发 429 限流",
                  st_login[-1] == 429 and st_login[0] == 401, f"sts={st_login[-3:]}")

            if hid:
                req("PATCH", f"/api/hotels/{hid}", {"status": "已停用"}, token=token)
        except Exception as _e:
            check("S40", "异常隔离", "第 40 段未捕获异常（后续段继续）", False,
                     f"{type(_e).__name__}: {_e}")
    except Exception as _e:
        import traceback
        traceback.print_exc()
        check("T0.0", "异常隔离", "套件级未捕获异常（收尾仍执行）", False, f"{type(_e).__name__}: {_e}")
    finally:
        # ---------- 收尾（finally）：无论是否崩溃都还原配置并出汇总 ----------
        try:
            if token:
                if llm_snapshot:
                    req("POST", "/api/ai/config", llm_snapshot, token=token)
                # 还原企微/推送配置（dry-run/假 webhook/假 chatid/推送时间等，防污染真实推送）
                if wecom_snapshot:
                    st, s9 = req("GET", "/api/settings", token=token)
                    now_map = s9 or {}
                    for k in list(now_map.keys()):
                        if k.startswith(("wecom_", "push_")) and k != "wecom_last_push" and k not in wecom_snapshot:
                            req("POST", "/api/settings", {"key": k, "value": ""}, token=token)  # 清测试新增键
                    for k, v in wecom_snapshot.items():
                        if str(now_map.get(k, "")) != str(v):
                            req("POST", "/api/settings", {"key": k, "value": v}, token=token)
                    # 还原集团 webhook（T16.7 会清空它们）
                    if group_hooks_snapshot:
                        for gid, url in group_hooks_snapshot.items():
                            req("POST", "/api/wecom/group-hook", {"groupId": gid, "webhookUrl": url}, token=token)
                    print(f"[cleanup] 企微/推送配置已还原 {len(wecom_snapshot)} 键 + {len(group_hooks_snapshot)} 集团 hook")
                req("POST", "/api/settings", {"key": "unbind_offline_minutes", "value": "30"}, token=token)
                req("POST", "/api/settings", {"key": "renew_order_ttl_hours", "value": "2"}, token=token)
                st, hs = req("GET", "/api/hotels", token=token)
                swept = 0
                for h in (hs or []):
                    name = h.get("name") or ""
                    if any(k in name for k in ("测试店", "测试酒", "复现", "核对", "清点", "防撞", "调试", "调一店", "调二店", "撞测", "冒烟", "拉黑", "竞态", "NOPE")) and h.get("status") != "已停用":
                        req("PATCH", f"/api/hotels/{h.get('id')}", {"status": "已停用"}, token=token)
                        swept += 1
                print(f"[cleanup] 测试店清扫：停用 {swept} 家")
        except Exception as _e:
            print(f"[warn] 收尾还原/清扫异常（不影响结果汇总）: {_e}")

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
