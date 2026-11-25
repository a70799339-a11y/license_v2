#!/usr/bin/env python3
"""T43 辅助：F1 回归——种子基础数据（套餐/设置）与演示数据分离验证。
在全新临时库 license_seed_test 上模拟「生产启动（SEED_DEMO_DATA 关）」：
  1. 首次 seed()：3 个套餐 + 系统设置齐全，且不生成演示酒店/授权/告警
  2. 再次 seed()：幂等不重复、不报错
  3. 清空套餐/设置后 seed()：已部署环境自动补齐（回归 F1 的补数路径）
用法: /opt/license/.venv/bin/python3 tests/t43_seed.py
输出: 最后一行 RESULT_JSON:{...}，run_tests 解析
"""
import json
import os
import subprocess
import sys

# 注入部署环境变量（DATABASE_URL 在 /opt/license/env.file，服务与直连同源）
_env_file = "/opt/license/env.file"
if os.path.exists(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

TEST_DB = "license_seed_test"


def psql(sql: str) -> bool:
    for cmd in (["sudo", "-u", "postgres", "psql", "-c", sql],
                ["psql", "-U", "postgres", "-c", sql]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def main() -> int:
    base_url = os.environ.get("DATABASE_URL", "")
    if not base_url:
        print("RESULT_JSON:" + json.dumps({"ok": False, "error": "DATABASE_URL 未配置"}))
        return 1

    from sqlalchemy.engine import make_url

    u = make_url(base_url)
    app_user = u.username or "postgres"
    # 全新临时库，owner=应用账号（免 PG15+ public schema 权限问题）
    psql(f"DROP DATABASE IF EXISTS {TEST_DB}")
    if not psql(f"CREATE DATABASE {TEST_DB} OWNER {app_user}"):
        print("RESULT_JSON:" + json.dumps({"ok": False, "error": f"无法创建 {TEST_DB}"}))
        return 1

    try:
        test_url = u.set(database=TEST_DB) if hasattr(u, "set") else u._replace(database=TEST_DB)
        # SQLAlchemy 2.x str(URL) 会把密码渲染成 ***——必须 render_as_string 显式保留密码
        os.environ["DATABASE_URL"] = (
            test_url.render_as_string(hide_password=False)
            if hasattr(test_url, "render_as_string") else str(test_url)
        )
        os.environ.pop("SEED_DEMO_DATA", None)  # 模拟生产：演示数据关闭

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
        from app.database import Base, SessionLocal, engine  # noqa: E402
        from app.models import Alert, AuditLog, Hotel, License, Plan, Setting, User  # noqa: E402
        from app.seed import seed  # noqa: E402

        Base.metadata.create_all(bind=engine)

        def snapshot(db):
            return {
                "users": db.query(User).count(),
                "plans": db.query(Plan).count(),
                "settings": sorted(s.key for s in db.query(Setting).all()),
                "hotels": db.query(Hotel).count(),
                "licenses": db.query(License).count(),
                "alerts": db.query(Alert).count(),
                "logs": db.query(AuditLog).count(),
                "plan_prices": sorted(p.price for p in db.query(Plan).all()),
            }

        # ---- 1. 全新生产启动（无演示数据）----
        db = SessionLocal()
        try:
            seed(db)
            s1 = snapshot(db)
        finally:
            db.close()

        # ---- 2. 再次 seed()（已初始化分支，幂等）----
        db = SessionLocal()
        try:
            seed(db)
            s2 = snapshot(db)
        finally:
            db.close()

        # ---- 3. 已部署库缺套餐/设置 → 重启补数（F1 补数路径）----
        db = SessionLocal()
        try:
            db.query(Plan).delete()
            db.query(Setting).delete()
            db.commit()
        finally:
            db.close()
        db = SessionLocal()
        try:
            seed(db)
            s3 = snapshot(db)
        finally:
            db.close()

        # ---- 4. 运营改价后重启不被重置（F1 补数只补缺失不覆盖）----
        db = SessionLocal()
        try:
            p1 = db.query(Plan).filter(Plan.id == "p1").first()
            p1.price = 12345
            db.commit()
        finally:
            db.close()
        db = SessionLocal()
        try:
            seed(db)
            price_kept = db.query(Plan).filter(Plan.id == "p1").first().price
        finally:
            db.close()

        need_settings = {"auto_renew", "renew_days_before", "heartbeat_timeout_hours"}
        checks = {
            "plans_fresh": s1["plans"] == 3 and all(p > 0 for p in s1["plan_prices"]),
            "settings_fresh": need_settings.issubset(set(s1["settings"])),
            "no_demo": s1["hotels"] == 0 and s1["licenses"] == 0 and s1["alerts"] == 0,
            "users_fresh": s1["users"] >= 3,
            "idempotent": s2["plans"] == 3 and s2["settings"] == s1["settings"],
            "backfill": s3["plans"] == 3 and need_settings.issubset(set(s3["settings"])),
            "price_keep": float(price_kept) == 12345.0,
        }
        ok = all(checks.values())
        print("RESULT_JSON:" + json.dumps({
            "ok": ok, "checks": checks,
            "s1": s1, "s2_plans": s2["plans"], "s3_plans": s3["plans"],
            "price_kept": float(price_kept),
        }, ensure_ascii=False))
        return 0 if ok else 1
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        psql(f"DROP DATABASE IF EXISTS {TEST_DB}")


if __name__ == "__main__":
    sys.exit(main())
