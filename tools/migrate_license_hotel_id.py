#!/usr/bin/env python3
"""T5 迁移：licenses.hotel_id 外键关联（幂等，可重跑）

- ALTER 加列/索引（IF NOT EXISTS）
- 按 hotel_name 匹配 hotels.name 回填 hotel_id（只填空位）
- 对账：输出总数/已关联/未匹配清单——未匹配 >0 即零丢失对账未过（退出码 1）

用法（WSL，需 DATABASE_URL）：
  /opt/license/.venv/bin/python3 tools/migrate_license_hotel_id.py [--dry-run]
"""
import os
import sys

from sqlalchemy import create_engine, text


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: 缺 DATABASE_URL")
        return 2
    dry = "--dry-run" in sys.argv
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS hotel_id VARCHAR(16) DEFAULT ''"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_licenses_hotel_id ON licenses(hotel_id)"))
        if dry:
            n = c.execute(text(
                "SELECT count(*) FROM licenses WHERE (hotel_id = '' OR hotel_id IS NULL)"
                " AND hotel_name IN (SELECT name FROM hotels)"
            )).scalar()
            print(f"[dry-run] 待回填 {n} 条")
        else:
            r = c.execute(text("""
                UPDATE licenses SET hotel_id = h.id FROM hotels h
                WHERE licenses.hotel_name = h.name AND (licenses.hotel_id = '' OR licenses.hotel_id IS NULL)
            """))
            print(f"回填 {r.rowcount} 条")
        total = c.execute(text("SELECT count(*) FROM licenses")).scalar()
        filled = c.execute(text("SELECT count(*) FROM licenses WHERE hotel_id != '' AND hotel_id IS NOT NULL")).scalar()
        # 对账口径：名字无对应酒店 = 回填永远填不上的行（dry-run/实跑同一口径）
        unmatched = c.execute(text(
            "SELECT code, hotel_name FROM licenses"
            " WHERE hotel_name NOT IN (SELECT name FROM hotels) ORDER BY code"
        )).fetchall()
        unfilled = c.execute(text(
            "SELECT count(*) FROM licenses WHERE hotel_id = '' OR hotel_id IS NULL"
        )).scalar()
        tail = "" if dry else f" 未回填={unfilled}"
        print(f"对账: 总数={total} 已关联={filled} 名字无对应酒店={len(unmatched)}{tail}")
        for code, name in unmatched[:20]:
            print(f"  [未匹配] {code} <- {name!r}")
        if unmatched:
            print("✗ 零丢失对账未过：存在 hotel_name 无对应酒店的授权，请人工核对上方清单")
            return 1
        if not dry and unfilled:
            print(f"✗ 零丢失对账未过：仍有 {unfilled} 条未回填")
            return 1
        print("✓ 预检通过：所有授权名都能对上酒店" if dry else "✓ 对账通过：存量授权全部回填（零丢失）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
