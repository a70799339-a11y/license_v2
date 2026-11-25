# -*- coding: utf-8 -*-
"""R2/R3 手工验证：SEED_DEMO_DATA 门禁 + 集团激活码生成（scratch 库）"""
import os
import sys

sys.path.insert(0, "/mnt/c/songxiankun/projects/license/backend")
os.environ["DATABASE_URL"] = "postgresql+psycopg2://postgres@127.0.0.1:5432/license_seedtest"
# 同 t39_cmd：加载部署 env.file（JWT_SECRET、DATABASE_URL 口令等）
_env_db = ""
try:
    with open("/opt/license/env.file", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                if _k == "DATABASE_URL":
                    _env_db = _v
                else:
                    os.environ.setdefault(_k, _v)
except Exception:
    pass
# scratch 库：沿用主库口令，仅换库名
SCRATCH_URL = (_env_db or "postgresql+psycopg2://postgres@127.0.0.1:5432/postgres").rsplit("/", 1)[0] + "/license_seedtest"
os.environ["DATABASE_URL"] = SCRATCH_URL

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.seed import seed  # noqa: E402
from app.models import Hotel, Group, License, User  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

seed(db)
print("无 SEED_DEMO_DATA → hotels =", db.query(Hotel).count(), "(应为 0)")

db.query(User).delete()
db.commit()
os.environ["SEED_DEMO_DATA"] = "1"
seed(db)
hs = db.query(Hotel).count()
gs = db.query(Group).all()
print("SEED_DEMO_DATA=1 → hotels =", hs, "(应为 40)")
print("集团激活码非空:", all(g.activation_key for g in gs),
      f"({sum(1 for g in gs if g.activation_key)}/{len(gs)})")
lic = db.query(License).first()
print("随机授权码示例:", lic.code)
from app.models import Device
print("随机指纹示例:", db.query(Device).first().fingerprint)
db.close()
