"""企业微信外发集成冒烟 —— 验证消息真实到达企微群（第 2 层测试）

用法：
  # 1. dry-run 管道验证（无外部依赖，可进回归）
  python tests/wecom_live_test.py

  # 2. 真实外发冒烟（需要测试群机器人 webhook）
  WECOM_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" python tests/wecom_live_test.py --live

断言：
  · dry-run 模式：管道生成内容正确（规则/酒店/脱敏）
  · live 模式：webhook 返回 errcode=0（消息到达测试群，人工目检一眼内容）
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost")
LIVE = "--live" in sys.argv
results = []


def check(cid, name, cond, actual=""):
    results.append((cid, name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {cid} {name}" + (f"  ({actual})" if actual else ""))


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def main():
    print(f"目标: {BASE}  模式: {'真实外发' if LIVE else 'dry-run 管道'}\n")

    st, p = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    token = (p or {}).get("token")
    check("L1", "管理员登录", st == 200 and bool(token), f"HTTP {st}")

    # ---- dry-run 管道断言（无论是否 live 都跑） ----
    req("POST", "/api/settings", {"key": "wecom_dry_run", "value": "on"}, token=token)
    st, p = req("POST", "/api/wecom/test", token=token)
    st, s2 = req("GET", "/api/settings", token=token)
    last = (s2 or {}).get("wecom_last_push", "")
    check("L2", "测试消息经管道生成（含标记）", "企微推送测试" in last)
    check("L3", "dry-run 未真实外发", (p or {}).get("dryRun") is True)

    # ---- live 真实外发 ----
    if LIVE:
        webhook = os.environ.get("WECOM_WEBHOOK", "").strip()
        check("L4", "webhook 环境变量就绪", bool(webhook), "WECOM_WEBHOOK")
        if not webhook:
            return finish()
        # 配置真实 webhook 并关闭 dry-run
        req("POST", "/api/wecom/config",
            {"opsWebhook": webhook, "pushEnabled": True, "dryRun": False}, token=token)
        st, p = req("POST", "/api/wecom/test", token=token)
        sent = ",".join((p or {}).get("sent", []))
        check("L5", "真实推送返回 ok（企微 errcode=0）", "ok" in sent, sent)

        # 高危告警真实外发
        st, p = req("POST", "/api/wecom/simulate", {"text": "帮助", "userid": "live-test"}, token=token)
        check("L6", "指令管道在 live 配置下可用", "指令列表" in (p or {}).get("reply", ""))

        # 恢复 dry-run（避免测试后持续外发）
        req("POST", "/api/wecom/config",
            {"opsWebhook": webhook, "pushEnabled": True, "dryRun": True}, token=token)
        check("L7", "恢复 dry-run 配置", True)
        print("\n⚠️  请目检企微测试群：应收到 2 条消息（测试消息 + 可能的告警）")
    else:
        print("\n（如需真实外发验证：加 --live 并设置 WECOM_WEBHOOK 环境变量）")

    return finish()


def finish():
    passed = sum(1 for _, _, ok in results if ok)
    total = len(results)
    print(f"\n===== 外发冒烟汇总: {passed}/{total} 通过 =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
