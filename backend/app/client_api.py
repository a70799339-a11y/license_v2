"""酒店客户端 API —— 激活 / 心跳同步 / 自助数据 / 版本同步更新

客户端认证：激活后签发 typ=client 的 JWT（绑定 hotel_id + 设备指纹），
心跳与自助接口均校验指纹一致，实现「和后端同步更新」的状态联动：
吊销、停用、到期、自动续期、新版本发布都会通过心跳即时下发。
"""
import platform
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import ALGORITHM, JWT_SECRET
from .alerting import raise_alert
from .database import get_db
from .license_crypto import sign_license
from .models import Alert, AuditLog, Device, Group, Hotel, License, Setting

router = APIRouter(prefix="/api/client", tags=["client"])
client_security = HTTPBearer(auto_error=False)

CLIENT_TOKEN_DAYS = 90
CLIENT_VERSION = "0.2.1"  # 当前打包客户端版本；settings.client_version 为服务端要求的最新版


# ---------- schemas ----------
class ActivateRequest(BaseModel):
    licenseCode: str
    deviceName: str = ""
    os_name: str = ""
    clientVersion: str = CLIENT_VERSION


class HeartbeatRequest(BaseModel):
    clientVersion: str = CLIENT_VERSION
    clockAnomaly: bool = False  # 客户端自检发现本机时钟回滚时上报 → 触发「时间回滚」告警


class ClientContext(BaseModel):
    hotel_id: str  # 集团形态下为 group_id
    fingerprint: str
    scope: str = "hotel"  # hotel=单店客户端 / group=集团总端


# ---------- 依赖 ----------
def get_client_context(
    creds: HTTPAuthorizationCredentials = Depends(client_security),
    db: Session = Depends(get_db),
) -> ClientContext:
    if creds is None:
        raise HTTPException(status_code=401, detail="客户端未激活")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="客户端凭证已过期，请重新激活")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效客户端凭证")
    if payload.get("typ") != "client":
        raise HTTPException(status_code=401, detail="凭证类型错误")
    return ClientContext(
        hotel_id=payload["sub"],
        fingerprint=payload.get("fp", ""),
        scope=payload.get("scope", "hotel"),
    )


def get_fingerprint_header(
    x_device_fingerprint: Optional[str] = Header(None, alias="X-Device-Fingerprint"),
) -> str:
    return x_device_fingerprint or ""


# ---------- 工具 ----------
def create_client_token(hotel_id: str, fingerprint: str, scope: str = "hotel") -> str:
    now = datetime.utcnow()
    payload = {
        "sub": hotel_id,
        "fp": fingerprint,
        "typ": "client",
        "scope": scope,
        "iat": now,
        "exp": now + timedelta(days=CLIENT_TOKEN_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def license_payload_for(hotel: Hotel, lic: License) -> dict:
    return {
        "v": 1,
        "license_id": lic.id,
        "code": lic.code,
        "hotel_id": hotel.id,
        "hotel_name": hotel.name,
        "plan": lic.plan,
        "max_devices": lic.max_devices,
        "issued_at": lic.issued_at,
        "expire_at": lic.expire_at,
    }


def find_active_license(db: Session, hotel: Hotel) -> Optional[License]:
    """优先取未吊销授权；若全部吊销则返回最近一条（供 revoked 状态判定）"""
    lic = (
        db.query(License)
        .filter(License.hotel_name == hotel.name, License.status != "已吊销")
        .order_by(License.expire_at.desc())
        .first()
    )
    if lic is not None:
        return lic
    return (
        db.query(License)
        .filter(License.hotel_name == hotel.name)
        .order_by(License.expire_at.desc())
        .first()
    )


def license_status(hotel: Hotel, lic: Optional[License]) -> str:
    """valid / expiring / revoked / expired / disabled"""
    if hotel.status == "已停用":
        return "disabled"
    if lic is None:
        return "expired"
    if lic.status == "已吊销":
        return "revoked"
    try:
        days = (datetime.strptime(lic.expire_at, "%Y-%m-%d") - datetime.utcnow()).days
    except Exception:
        return "valid"
    if days < 0:
        return "expired"
    if days <= 30:
        return "expiring"
    return "valid"


def latest_version_info(db: Session) -> dict:
    def val(key: str, default: str) -> str:
        row = db.query(Setting).filter(Setting.key == key).first()
        return row.value if row and row.value else default

    return {
        "latest": val("client_version", CLIENT_VERSION),
        "min_supported": val("client_min_version", "0.1.0"),
        "download_url": val("client_download_url", ""),
        "notes": val("client_version_notes", ""),
    }


def offline_grace_hours(db: Session) -> int:
    """离线宽限小时数（后台 settings 的 heartbeat_timeout_hours 配置，默认 72）"""
    row = db.query(Setting).filter(Setting.key == "heartbeat_timeout_hours").first()
    try:
        return max(1, int(row.value)) if row and row.value else 72
    except ValueError:
        return 72


# ---------- 接口 ----------
@router.post("/activate")
def activate(
    req: ActivateRequest,
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    if not fingerprint:
        raise HTTPException(status_code=400, detail="缺少设备指纹（X-Device-Fingerprint）")

    # 集团码（GP-xxxx）→ 总端激活
    group = db.query(Group).filter(Group.code == req.licenseCode).first()
    if group is not None:
        if group.status == "已停用":
            raise HTTPException(status_code=403, detail="该集团已停用，无法激活")
        member_count = db.query(Hotel).filter(Hotel.group_id == group.id).count()
        signed = sign_license(
            db,
            {
                "v": 1,
                "scope": "group",
                "group_id": group.id,
                "code": group.code,
                "name": group.name,
                "plan": group.plan,
                "expire_at": group.expire_at,
                "member_count": member_count,
            },
        )
        version = latest_version_info(db)
        return {
            "token": create_client_token(group.id, fingerprint, scope="group"),
            "scope": "group",
            "hotel": {"id": group.id, "name": group.name, "plan": group.plan, "status": group.status},
            "license": {
                "code": group.code,
                "plan": group.plan,
                "expire_at": group.expire_at,
                "max_devices": 0,
                "status": "valid",
            },
            "signed_license": signed,
            "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "client_version": version["latest"],
            "offline_grace_hours": offline_grace_hours(db),
        }

    # 用授权码或客户编号定位（单店）
    lic = db.query(License).filter(License.code == req.licenseCode).first()
    hotel: Optional[Hotel] = None
    if lic is not None:
        hotel = db.query(Hotel).filter(Hotel.name == lic.hotel_name).first()
    if hotel is None:
        hotel = db.query(Hotel).filter(Hotel.code == req.licenseCode).first()
        if hotel is not None:
            lic = find_active_license(db, hotel)
    if hotel is None:
        raise_alert(
            db, level="中", rule="无效激活尝试", hotel_name=req.licenseCode or "未知",
            detail=f"无效授权码/客户编号激活尝试（指纹 {fingerprint[:12]}…）",
        )
        db.commit()
        raise HTTPException(status_code=404, detail="授权码/客户编号无效")
    if hotel.status == "已停用":
        raise HTTPException(status_code=403, detail="该客户已停用，无法激活")
    if lic is None or lic.status == "已吊销":
        raise_alert(
            db, level="高", rule="吊销后使用", hotel_name=hotel.name,
            detail=f"已吊销/无效授权仍尝试激活（授权码 {req.licenseCode}）",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="授权已吊销或不存在，无法激活")

    # 指纹唯一性：同一设备只能绑定一家酒店
    other = (
        db.query(Device)
        .filter(Device.fingerprint == fingerprint, Device.hotel_id != hotel.id)
        .first()
    )
    if other is not None:
        raise_alert(
            db, level="高", rule="License 共享检测", hotel_name=hotel.name,
            detail=f"设备已绑定「{other.name}」又尝试激活本店，疑似授权共享（指纹 {fingerprint[:12]}…）",
        )
        db.commit()
        raise HTTPException(status_code=409, detail=f"该设备已绑定酒店「{other.name}」，如需变更请联系服务商")

    device = (
        db.query(Device)
        .filter(Device.hotel_id == hotel.id, Device.fingerprint == fingerprint)
        .first()
    )
    if device is None:
        if hotel.devices >= hotel.max_devices:
            raise_alert(
                db, level="中", rule="设备数超限", hotel_name=hotel.name,
                detail=f"设备数已达上限 {hotel.max_devices} 仍尝试激活新设备",
            )
            db.commit()
            raise HTTPException(status_code=403, detail="设备数已达套餐上限，请先解绑或升级套餐")
        device = Device(
            id=f"cd{datetime.utcnow().strftime('%H%M%S')}{fingerprint[:6]}",
            hotel_id=hotel.id,
            name=req.deviceName or f"客户端-{fingerprint[:6]}",
            os=req.os_name or platform.platform(),
            fingerprint=fingerprint,
            last_active=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            status="在线",
        )
        db.add(device)
        hotel.devices = (hotel.devices or 0) + 1
        # 多设备并发激活：24h 内已有 ≥2 台活跃设备，又新增一台
        day_ago = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        active_recent = (
            db.query(Device)
            .filter(Device.hotel_id == hotel.id, Device.last_active >= day_ago)
            .count()
        )
        if active_recent >= 2:
            raise_alert(
                db, level="高", rule="多设备并发激活", hotel_name=hotel.name,
                detail=f"24 小时内第 {active_recent + 1} 台设备激活，疑似授权共享",
            )
    else:
        device.last_active = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        device.status = "在线"
        if req.deviceName:
            device.name = req.deviceName

    hotel.last_active = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.commit()

    signed = sign_license(db, license_payload_for(hotel, lic))
    version = latest_version_info(db)
    return {
        "token": create_client_token(hotel.id, fingerprint),
        "scope": "hotel",
        "hotel": {"id": hotel.id, "name": hotel.name, "plan": hotel.plan, "status": hotel.status},
        "license": {
            "code": lic.code,
            "plan": lic.plan,
            "expire_at": lic.expire_at,
            "max_devices": lic.max_devices,
            "status": license_status(hotel, lic),
        },
        "signed_license": signed,
        "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "client_version": version["latest"],
        "offline_grace_hours": offline_grace_hours(db),
    }


@router.post("/heartbeat")
def heartbeat(
    req: HeartbeatRequest,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    # 指纹一致性校验（防凭证拷贝到其他机器）
    if fingerprint and fingerprint != ctx.fingerprint:
        # 实时告警：凭证挪机/指纹变更
        owner = (
            db.query(Hotel).filter(Hotel.id == ctx.hotel_id).first()
            if ctx.scope == "hotel"
            else db.query(Group).filter(Group.id == ctx.hotel_id).first()
        )
        raise_alert(
            db, level="高", rule="设备指纹变更",
            hotel_name=owner.name if owner else ctx.hotel_id,
            detail=f"心跳指纹 {fingerprint[:12]}… 与激活指纹 {ctx.fingerprint[:12]}… 不一致，疑似凭证拷贝",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="设备指纹不一致，请重新激活")

    # 客户端上报本机时钟回滚 → 实时告警
    if req.clockAnomaly:
        owner = (
            db.query(Hotel).filter(Hotel.id == ctx.hotel_id).first()
            if ctx.scope == "hotel"
            else db.query(Group).filter(Group.id == ctx.hotel_id).first()
        )
        raise_alert(
            db, level="高", rule="时间回滚",
            hotel_name=owner.name if owner else ctx.hotel_id,
            detail="客户端上报本机时钟回滚（检测到系统时间早于上次同步）",
        )
        db.commit()

    version = latest_version_info(db)
    update_available = req.clientVersion != version["latest"]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 集团总端心跳 ----------
    if ctx.scope == "group":
        g = db.query(Group).filter(Group.id == ctx.hotel_id).first()
        if g is None:
            raise HTTPException(status_code=401, detail="集团不存在")
        members = db.query(Hotel).filter(Hotel.group_id == g.id).all()
        # 集团状态取成员最差状态
        status = "valid"
        for m in members:
            s = license_status(m, find_active_license(db, m))
            rank = {"valid": 0, "expiring": 1, "expired": 2, "revoked": 3, "disabled": 4}
            if rank.get(s, 0) > rank.get(status, 0):
                status = s
        return {
            "scope": "group",
            "status": "valid" if g.status != "已停用" else "disabled",
            "member_status": status,
            "plan": g.plan,
            "expire_at": g.expire_at,
            "auto_renew": bool(g.auto_renew),
            "member_count": len(members),
            "server_time": now_str,
            "update_available": update_available,
            "latest_version": version["latest"],
            "download_url": version["download_url"] if update_available else "",
            "notes": version["notes"] if update_available else "",
            "offline_grace_hours": offline_grace_hours(db),
        }

    # ---------- 单店客户端心跳 ----------
    hotel = db.query(Hotel).filter(Hotel.id == ctx.hotel_id).first()
    if hotel is None:
        raise HTTPException(status_code=401, detail="客户不存在")

    device = (
        db.query(Device)
        .filter(Device.hotel_id == hotel.id, Device.fingerprint == ctx.fingerprint)
        .first()
    )
    if device is not None:
        if device.status == "已禁用":
            raise HTTPException(status_code=401, detail="该设备已被禁用")
        device.last_active = now_str
        device.status = "在线"
    hotel.last_active = now_str

    lic = find_active_license(db, hotel)
    status = license_status(hotel, lic)

    # 实时告警：心跳中断后恢复 / 吊销与过期授权仍在使用
    gap = (datetime.utcnow() - datetime.strptime(hotel.last_active, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600 if hotel.last_active else 0
    if hotel.last_active and gap > offline_grace_hours(db):
        raise_alert(
            db, level="中", rule="心跳中断", hotel_name=hotel.name,
            detail=f"中断 {gap:.0f} 小时后恢复心跳（宽限 {offline_grace_hours(db)}h）",
        )
    if status == "revoked":
        raise_alert(
            db, level="紧急", rule="吊销后使用", hotel_name=hotel.name,
            detail="已吊销授权的客户端仍在发起心跳",
        )
    elif status == "expired":
        raise_alert(
            db, level="高", rule="吊销后使用", hotel_name=hotel.name,
            detail="已过期授权的客户端仍在发起心跳",
        )

    db.commit()
    return {
        "scope": "hotel",
        "status": status,  # valid / expiring / revoked / expired / disabled
        "plan": hotel.plan,
        "expire_at": lic.expire_at if lic else "",
        "auto_renew": bool(hotel.auto_renew),
        "server_time": now_str,
        "update_available": update_available,
        "latest_version": version["latest"],
        "download_url": version["download_url"] if update_available else "",
        "notes": version["notes"] if update_available else "",
        "offline_grace_hours": offline_grace_hours(db),
    }


@router.get("/profile")
def profile(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    hotel = db.query(Hotel).filter(Hotel.id == ctx.hotel_id).first()
    if hotel is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    lic = find_active_license(db, hotel)
    devices = db.query(Device).filter(Device.hotel_id == hotel.id).all()
    alerts = (
        db.query(Alert)
        .filter(Alert.hotel_name == hotel.name)
        .order_by(Alert.time.desc())
        .limit(20)
        .all()
    )
    return {
        "hotel": {
            "id": hotel.id,
            "name": hotel.name,
            "code": hotel.code,
            "group": hotel.group,
            "city": hotel.city,
            "contact": hotel.contact,
            "phone": hotel.phone,
            "plan": hotel.plan,
            "status": hotel.status,
            "auto_renew": bool(hotel.auto_renew),
            "expire_at": hotel.expire_at,
            "max_devices": hotel.max_devices,
        },
        "license": {
            "code": lic.code,
            "plan": lic.plan,
            "issued_at": lic.issued_at,
            "expire_at": lic.expire_at,
            "status": license_status(hotel, lic),
            "max_devices": lic.max_devices,
        }
        if lic
        else None,
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "os": d.os,
                "fingerprint": d.fingerprint,
                "last_active": d.last_active,
                "status": d.status,
            }
            for d in devices
        ],
        "alerts": [
            {
                "id": a.id,
                "level": a.level,
                "rule": a.rule,
                "detail": a.detail,
                "time": a.time,
                "status": a.status,
            }
            for a in alerts
        ],
        "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/version")
def version(db: Session = Depends(get_db)):
    """版本同步更新：客户端启动/手动检查时调用，无需认证"""
    return latest_version_info(db)


@router.get("/public-key")
def public_key(db: Session = Depends(get_db)):
    """Ed25519 公钥（客户端验签用；打包客户端时内嵌此值）"""
    from .license_crypto import get_public_key_hex

    return {"algorithm": "ed25519", "public_key": get_public_key_hex(db)}


# ---------- 集团总端（总/子管理） ----------
class GroupRenewRequest(BaseModel):
    hotelIds: list[str] = []  # 空 = 全部门店


@router.get("/group/profile")
def group_profile(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """集团总端自助数据：本集团旗下门店一览（数据隔离：只含本集团）"""
    if ctx.scope != "group":
        raise HTTPException(status_code=400, detail="该接口仅限集团总端调用")
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    g = db.query(Group).filter(Group.id == ctx.hotel_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")

    members = db.query(Hotel).filter(Hotel.group_id == g.id).order_by(Hotel.id).all()
    member_names = [m.name for m in members]
    alerts = (
        db.query(Alert)
        .filter(Alert.hotel_name.in_(member_names))
        .order_by(Alert.time.desc())
        .limit(50)
        .all()
    )
    return {
        "scope": "group",
        "group": {
            "id": g.id,
            "name": g.name,
            "code": g.code,
            "plan": g.plan,
            "status": g.status,
            "expire_at": g.expire_at,
            "auto_renew": bool(g.auto_renew),
        },
        "summary": {
            "memberCount": len(members),
            "deviceCount": sum(m.devices or 0 for m in members),
            "expiringSoon": sum(1 for m in members if m.status in ("即将到期", "已过期")),
            "alertCount": sum(1 for a in alerts if a.status != "已处置"),
        },
        "members": [
            {
                "id": m.id,
                "name": m.name,
                "code": m.code,
                "city": m.city,
                "plan": m.plan,
                "status": license_status(m, find_active_license(db, m)),
                "expire_at": m.expire_at,
                "devices": m.devices,
                "max_devices": m.max_devices,
            }
            for m in members
        ],
        "alerts": [
            {
                "id": a.id,
                "level": a.level,
                "rule": a.rule,
                "hotel_name": a.hotel_name,
                "detail": a.detail,
                "time": a.time,
                "status": a.status,
            }
            for a in alerts
        ],
        "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.post("/group/renew")
def group_renew(
    req: GroupRenewRequest,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """集团总端批量续期（只作用于本集团门店）"""
    if ctx.scope != "group":
        raise HTTPException(status_code=400, detail="该接口仅限集团总端调用")
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    g = db.query(Group).filter(Group.id == ctx.hotel_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")

    q = db.query(Hotel).filter(Hotel.group_id == g.id)
    if req.hotelIds:
        q = q.filter(Hotel.id.in_(req.hotelIds))
    members = q.all()
    for m in members:
        m.expire_at = "2027-12-31"
        if m.status in ("即将到期", "已过期"):
            m.status = "正常"
    db.add(
        AuditLog(
            operator=f"集团端({g.name})",
            action="集团批量续期",
            target=g.name,
            detail=f"{len(members)} 家门店续期至 2027-12-31",
        )
    )
    db.commit()
    return {"ok": True, "renewed": len(members)}


# ---------- 消息通知自助配置（门店/集团在客户端内配置自己的企微群 webhook） ----------
class NotifyConfigReq(BaseModel):
    webhookUrl: str = ""


@router.get("/notify/config")
def notify_config_get(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """读取本实体的通知配置（scope 隔离：只见自己）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    from .wecom import group_hooks, hotel_hook

    if ctx.scope == "group":
        url = group_hooks(db).get(ctx.hotel_id, "")
    else:
        url = hotel_hook(db, ctx.hotel_id)
    return {"scope": ctx.scope, "webhookUrl": url, "entityId": ctx.hotel_id}


@router.post("/notify/config")
def notify_config_set(
    req: NotifyConfigReq,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """保存本实体的通知 webhook（只写自己的配置；域名白名单防 SSRF）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    from .wecom import set_group_hook, set_hotel_hook, validate_webhook_url
    from .masking import mask_text

    url = (req.webhookUrl or "").strip()
    if url and not validate_webhook_url(url):
        raise HTTPException(status_code=400, detail="仅支持企业微信官方机器人 webhook（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）")
    if ctx.scope == "group":
        set_group_hook(db, ctx.hotel_id, url)
        target = f"集团:{ctx.hotel_id}"
    else:
        set_hotel_hook(db, ctx.hotel_id, url)
        target = f"门店:{ctx.hotel_id}"
    db.add(
        AuditLog(
            operator=f"客户端({target})",
            action="配置企微通知",
            target=target,
            detail=mask_text(f"webhook {url or '（已清空）'}"),
        )
    )
    db.commit()
    return {"ok": True, "saved": bool(url)}


@router.post("/notify/test")
def notify_test(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """向本实体配置的 webhook 发测试消息（dry-run 时仅记录）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    from .wecom import hotel_hook, group_hooks, push_message

    if ctx.scope == "group":
        gid, hid = ctx.hotel_id, ""
    else:
        gid, hid = "", ctx.hotel_id
    r = push_message(
        db,
        "**✅ 消息通知测试**\n来自客户端的测试消息，本实体告警/提醒将推送到这里。",
        group_id=gid,
        hotel_id=hid,
    )
    return {"ok": True, "sent": r.get("sent"), "dryRun": r.get("dry_run")}
