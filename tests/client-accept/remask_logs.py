# -*- coding: utf-8 -*-
"""存量审计日志回刷掩码（一次性运维脚本）"""
import os, sys
sys.path.insert(0, "/mnt/c/songxiankun/projects/license/backend")
try:
    with open("/opt/license/env.file", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)
except Exception:
    pass
from app.database import SessionLocal
from app.models import AuditLog
from app.masking import mask_text
db = SessionLocal()
n = 0
for row in db.query(AuditLog).all():
    t2 = mask_text(row.target or "")
    d2 = mask_text(row.detail or "")
    if t2 != (row.target or "") or d2 != (row.detail or ""):
        row.target = t2
        row.detail = d2
        n += 1
db.commit()
print("回刷行数:", n)
db.close()
