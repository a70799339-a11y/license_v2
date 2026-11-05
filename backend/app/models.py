"""ORM 数据模型 —— groups / hotels / licenses / alerts / plans / users"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    name = Column(String(64), nullable=False)
    role = Column(String(32), default="operator")


class Group(Base):
    """集团（总端）—— 旗下 N 家门店，总端客户端用集团码激活后统一查看/管理"""

    __tablename__ = "groups"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)  # 集团码 GP-xxxx，总端激活用
    contact = Column(String(64), default="")
    phone = Column(String(32), default="")
    status = Column(String(16), default="正常")
    plan = Column(String(16), default="旗舰版")  # 集团套餐（决定功能上限）
    expire_at = Column(String(10), default="")
    auto_renew = Column(Boolean, default=False)
    created_at = Column(String(10), default="")


class Hotel(Base):
    __tablename__ = "hotels"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    code = Column(String(32), unique=True, nullable=False, index=True)
    group = Column(String(64), default="")
    group_id = Column(String(16), index=True, default="")  # 归属集团（总/子关系）
    city = Column(String(32), default="")
    contact = Column(String(64), default="")
    phone = Column(String(32), default="")
    plan = Column(String(16), default="基础版")
    status = Column(String(16), default="正常")
    devices = Column(Integer, default=0)
    max_devices = Column(Integer, default=30)
    expire_at = Column(String(10), default="")
    auto_renew = Column(Boolean, default=False)
    created_at = Column(String(10), default="")
    last_active = Column(String(32), default="")


class License(Base):
    __tablename__ = "licenses"
    id = Column(String(16), primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    hotel_name = Column(String(128), nullable=False)
    plan = Column(String(16), default="基础版")
    type = Column(String(16), default="新签")
    issued_at = Column(String(10), default="")
    expire_at = Column(String(10), default="")
    status = Column(String(16), default="生效中")
    devices = Column(Integer, default=0)
    max_devices = Column(Integer, default=30)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(String(16), primary_key=True, index=True)
    level = Column(String(8), default="中")
    rule = Column(String(64), default="")
    hotel_name = Column(String(128), default="")
    detail = Column(String(256), default="")
    time = Column(String(32), default="")
    status = Column(String(16), default="待处理")


class Plan(Base):
    __tablename__ = "plans"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(16), unique=True, nullable=False)
    price = Column(Float, default=0)
    yearly_price = Column(Float, default=0)
    max_devices = Column(Integer, default=30)
    features = Column(String(512), default="")  # JSON 数组字符串
    hotels = Column(Integer, default=0)
    color = Column(String(16), default="#3B82F6")


class Device(Base):
    __tablename__ = "devices"
    id = Column(String(32), primary_key=True, index=True)
    hotel_id = Column(String(16), index=True, nullable=False)
    name = Column(String(64), default="")
    os = Column(String(128), default="")
    fingerprint = Column(String(128), default="")
    last_active = Column(String(32), default="")
    status = Column(String(16), default="在线")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(String(256), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    operator = Column(String(64), default="")
    action = Column(String(64), default="")
    target = Column(String(128), default="")
    detail = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
