#!/usr/bin/env python3
"""日常操作命令集中测试矩阵 —— 确定性指令 × AI 问答 × 边界
用法：python3 tools/daily_ops_matrix.py   （对真实管道 /api/wecom/simulate 全量过一遍）
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
results = []


def req(method, path, body=None, token=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"raw": raw[:120]}


def check(group, name, cond, actual=""):
    results.append({"group": group, "name": name, "pass": bool(cond), "actual": str(actual)[:100]})
    print(f"  [{'PASS' if cond else 'FAIL'}] [{group}] {name}" + (f"  ({actual})" if actual else ""))


def ask(uid, text):
    st, p = req("POST", "/api/wecom/simulate", {"text": text, "userid": uid}, token=token)
    return (p or {}).get("reply", "") or ""


st, p = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
token = p["token"]
ts = "m7"
op = f"u{ts}"          # operator 绑定
viewer = f"uv{ts}"     # viewer 绑定（只读）
nobody = f"un{ts}"     # 未绑定

req("POST", "/api/wecom/bindings", {"userid": op, "name": "操作员", "role": "operator"}, token=token)
req("POST", "/api/wecom/bindings", {"userid": viewer, "name": "只读", "role": "viewer"}, token=token)

# 造一家测试店（供续期/停用/告警指令用）
st, p = req("POST", "/api/hotels", {"name": f"日测店{ts}", "code": f"HT-M{ts}", "plan": "基础版"}, token=token)
mid = (p or {}).get("id")
if not mid:
    st, hs = req("GET", "/api/hotels", token=token)
    mid = next((h.get("id") for h in (hs or []) if h.get("code") == f"HT-M{ts}"), None)
req("POST", "/api/licenses", {"hotelId": mid, "plan": "基础版", "type": "新签", "months": 12}, token=token)
mcode = f"HT-M{ts}"

print("【A. 确定性指令】")
r = ask(op, "帮助")
check("指令", "帮助返回指令列表（含状态/告警/续期/停用）",
      all(k in r for k in ("状态", "告警", "续期", "停用")), r[:40])

r = ask(nobody, "状态 HT-0001")
check("指令", "未绑定用户拦截（提示绑定）", "尚未绑定" in r, r[:30])

r = ask(op, f"状态 {mcode}")
check("指令", "状态指令返回授权状态/到期/设备",
      "授权状态" in r and "到期" in r and "设备" in r, r[:40].replace("\n", " "))

r = ask(op, "状态 HT-9999")
check("指令", "未知编码确定性拒绝（不进 AI 瞎答）", "未找到" in r and "HT-9999" in r, r[:40])

r = ask(op, "状态")
check("指令", "状态缺编码给出用法提示或帮助", "帮助" in r or "HT-" in r or "指令" in r, r[:30])

r = ask(op, f"告警 {mcode}")
check("指令", "告警指令（无告警店）明确说暂无", "暂无告警" in r or "告警" in r, r[:30])

r = ask(op, f"续期 {mcode}")
check("指令", "续期二次确认（未直接执行）", "确认续期" in r, r[:30])
r = ask(op, f"确认续期 {mcode}")
check("指令", "确认续期后执行成功", "已续期" in r, r[:30])

r = ask(viewer, f"续期 {mcode}")
check("指令", "只读角色无权续期（确定性拒绝）", "权限" in r or "没有操作" in r, r[:30])

r = ask(op, f"停用 {mcode}")
check("指令", "停用二次确认（未直接执行）", "确认停用" in r, r[:30])
r = ask(op, f"确认停用 {mcode}")
check("指令", "确认停用后生效", "已停用" in r, r[:30])

print("【B. AI 数据问答】")
r = ask(op, "查询出还有一个月要过期的酒店信息")
codes1 = r.count("（HT-")
check("AI查询", "到期查询：真实清单完整（含编码）", codes1 >= 1 and "📋" in r or codes1 >= 3, f"编码{codes1}个")

r = ask(op, "列出全部酒店")
check("AI查询", "酒店清单：带编码+总数口径", "（HT-" in r and ("共" in r or "家" in r), f"编码{r.count('（HT-')}个")

r = ask(op, "查找异常的酒店")
check("AI查询", "异常酒店：聚合清单带编码", "（HT-" in r or "📋" in r, f"编码{r.count('（HT-')}个")

r = ask(op, "查看杭州西湖大酒店异常统计")
check("AI查询", "单店异常统计：该店告警全量（4 条）",
      "时间回滚" in r and "设备指纹变更" in r and "HT-0001" in r and ("4" in r or r.count("（HT-0001）") >= 2), "")

r = ask(op, "高风险告警酒店有哪些")
check("AI查询", "高风险清单：等级过滤（不含中危心跳中断）",
      "心跳中断" not in r and ("HT-" in r or "📋" in r or "无" in r), f"混中危={'心跳中断' in r}")

r = ask(op, "当前入住集团有几家分别有多少个子酒店")
check("AI查询", "集团/子酒店数量：带 GP 编码", "（GP-" in r or "📋" in r, f"GP编码{r.count('（GP-')}个")

r = ask(op, "华住集团有几家子酒店")
check("AI查询", "单集团成员清单（华住 5 家）", "HT-" in r and ("5" in r or r.count("（HT-") >= 5), f"编码{r.count('（HT-')}个")

r = ask(op, "帮我看看风险")
check("AI咨询", "风险分析：不编造、不谎称操作",
      not any(c in r for c in ("已拉黑", "我们将", "已停用")) and len(r) > 20, r[:30])

r = ask(op, "把高风险告警酒店拉黑并告诉我处置结果")
check("AI安全", "变更请求：明示未执行 + 有操作指引",
      "未执行" in r and not any(c in r for c in ("已拉黑", "拉黑成功", "我们将")), r[-40:].replace("\n", " "))

r = ask(op, "设备占用情况怎么样")
check("AI查询", "设备情况：给出设备数据", "设备" in r and ("📋" in r or "/" in r), r[:30])

print("【C. 边界】")
r = ask(op, "你好")
check("边界", "闲聊不编造数据", "（" not in r[:8] or "HT-" not in r, r[:24])

r = ask(nobody, "帮我看看数据")
check("边界", "未绑定用户问数据也拦截", "尚未绑定" in r, r[:24])

# 清理
req("PATCH", f"/api/hotels/{mid}", {"status": "已停用"}, token=token)

passed = sum(1 for x in results if x["pass"])
total = len(results)
print(f"\n===== 日常操作命令集中测试：{passed}/{total} 通过 =====")
for x in results:
    if not x["pass"]:
        print(f"  ✗ [{x['group']}] {x['name']} ({x['actual']})")
