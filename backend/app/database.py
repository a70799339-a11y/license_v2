"""数据库连接 —— PostgreSQL（自建服务器），通过 DATABASE_URL 环境变量注入"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# 生产必须显式注入连接串：缺失直接启动失败（禁止内置口令回落）
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 未配置。请在部署环境变量（/opt/license/env.file）中设置后重启服务。"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
