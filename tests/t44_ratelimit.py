#!/usr/bin/env python3
"""T44 辅助：G4 白盒——限流计数满额时拒新键（fail-closed）、不淘汰窗口内活跃计数。
用法: /opt/license/.venv/bin/python3 tests/t44_ratelimit.py
输出: 最后一行 RESULT_JSON:{...}
"""
import json
import os
import sys

# 注入部署环境变量（同 t39/t43）
_env_file = "/opt/license/env.file"
if os.path.exists(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    import app.client_api as c
    import app.main as m

    checks = {}

    # ---- 登录限流 ----
    m._LOGIN_FAIL_MAX_KEYS = 3
    with m._login_lock:
        m._login_failures.clear()
        m._login_ip_failures.clear()
        m._login_trusted.clear()
    for i in range(3):
        m._login_record_failure(f"10.0.0.{i}|u{i}")
    checks["login_full_reject"] = m._login_too_often("10.0.0.9|new", None) is True
    checks["login_no_evict"] = len(m._login_failures) == 3
    m._login_record_failure("10.0.0.9|new")  # 满额应拒收，不挤掉活跃计数
    checks["login_record_skip"] = len(m._login_failures) == 3 and "10.0.0.9|new" not in m._login_failures
    with m._login_lock:
        m._login_failures.pop("10.0.0.0|u0", None)
        m._login_ip_failures.pop("10.0.0.0", None)
    m._login_record_failure("10.0.0.9|new")  # 释放一键后新键可入
    checks["login_after_free"] = len(m._login_failures) == 3 and "10.0.0.9|new" in m._login_failures
    # 已有键走正常判定（1 次失败 < 默认单键 10）
    checks["login_existing_ok"] = m._login_too_often("10.0.0.1|u1", None) is False

    # ---- 激活限流 ----
    c._ACTIVATE_FAIL_MAX_KEYS = 3
    with c._activate_lock:
        c._activate_failures.clear()
    for i in range(3):
        c._activate_record_failure(f"10.1.0.{i}", f"cred{i}")
    checks["act_full_reject"] = c._activate_too_often("10.1.0.9", 99) is True
    checks["act_no_evict"] = len(c._activate_failures) == 3
    c._activate_record_failure("10.1.0.9", "x")
    checks["act_record_skip"] = len(c._activate_failures) == 3 and "10.1.0.9" not in c._activate_failures
    checks["act_existing_ok"] = c._activate_too_often("10.1.0.0", 99) is False

    ok = all(checks.values())
    print("RESULT_JSON:" + json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
