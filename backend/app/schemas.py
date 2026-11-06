"""Pydantic 请求/响应模型"""
from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    username: str
    name: str
    role: str = "operator"


class HotelOut(BaseModel):
    id: str
    name: str
    code: str
    group: str
    city: str
    contact: str
    phone: str
    plan: str
    status: str
    devices: int
    maxDevices: int
    expireAt: str
    autoRenew: bool
    createdAt: str
    lastActive: str

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_model(cls, h):
        return cls(
            id=h.id, name=h.name, code=h.code, group=h.group, city=h.city,
            contact=h.contact, phone=h.phone, plan=h.plan, status=h.status,
            devices=h.devices, maxDevices=h.max_devices, expireAt=h.expire_at,
            autoRenew=h.auto_renew, createdAt=h.created_at, lastActive=h.last_active,
        )


class HotelUpdate(BaseModel):
    status: Optional[str] = None
    autoRenew: Optional[bool] = None
    expireAt: Optional[str] = None
    plan: Optional[str] = None


class HotelCreate(BaseModel):
    name: str
    code: str
    group: str = ""
    city: str = ""
    contact: str = ""
    phone: str = ""
    plan: str = "基础版"


class BatchRenewRequest(BaseModel):
    ids: list[str]
    expireAt: str = "2027-11-01"


class BatchDisableRequest(BaseModel):
    ids: list[str]


class LicenseCreate(BaseModel):
    hotelId: str
    plan: str = "基础版"
    type: str = "新签"
    months: int = 12


class RevokeRequest(BaseModel):
    reason: str = "手动吊销"


class LicenseOut(BaseModel):
    id: str
    code: str
    hotelName: str
    plan: str
    type: str
    issuedAt: str
    expireAt: str
    status: str
    devices: int
    maxDevices: int

    @classmethod
    def from_orm_model(cls, l):
        return cls(
            id=l.id, code=l.code, hotelName=l.hotel_name, plan=l.plan, type=l.type,
            issuedAt=l.issued_at, expireAt=l.expire_at, status=l.status,
            devices=l.devices, maxDevices=l.max_devices,
        )


class AlertOut(BaseModel):
    id: str
    level: str
    rule: str
    hotelName: str
    detail: str
    time: str
    status: str
    handledBy: str = ""
    handleNote: str = ""
    handledAt: str = ""

    @classmethod
    def from_orm_model(cls, a):
        return cls(
            id=a.id, level=a.level, rule=a.rule, hotelName=a.hotel_name,
            detail=a.detail, time=a.time, status=a.status,
            handledBy=getattr(a, "handled_by", "") or "",
            handleNote=getattr(a, "handle_note", "") or "",
            handledAt=getattr(a, "handled_at", "") or "",
        )


class DeviceOut(BaseModel):
    id: str
    name: str
    os: str
    fingerprint: str
    lastActive: str
    status: str

    @classmethod
    def from_orm_model(cls, d):
        return cls(
            id=d.id, name=d.name, os=d.os, fingerprint=d.fingerprint,
            lastActive=d.last_active, status=d.status,
        )


class AuditLogOut(BaseModel):
    id: int
    operator: str
    action: str
    target: str
    detail: str
    createdAt: str

    @classmethod
    def from_orm_model(cls, a):
        return cls(
            id=a.id, operator=a.operator, action=a.action, target=a.target,
            detail=a.detail, createdAt=a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
        )


class PlanOut(BaseModel):
    id: str
    name: str
    price: float
    yearlyPrice: float
    maxDevices: int
    features: list[str]
    hotels: int
    color: str

    @classmethod
    def from_orm_model(cls, p):
        import json
        try:
            feats = json.loads(p.features) if p.features else []
        except Exception:
            feats = []
        return cls(
            id=p.id, name=p.name, price=p.price, yearlyPrice=p.yearly_price,
            maxDevices=p.max_devices, features=feats, hotels=p.hotels, color=p.color,
        )
