"""数据库连接 —— PostgreSQL（自建服务器），DATABASE_URL 可覆盖"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# 默认连接本机自建 PostgreSQL（端口 5433）；生产通过环境变量 DATABASE_URL 指向你们自己的服务器
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:license123@127.0.0.1:5433/license",
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
