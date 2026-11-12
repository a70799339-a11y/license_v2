"""酒店客户端 API —— 激活 / 心跳同步 / 自助数据 / 版本同步更新

客户端认证：激活后签发 typ=client 的 JWT（绑定 hotel_id + 设备指纹），
心跳与自助接口均校验指纹一致，实现「和后端同步更新」的状态联动：
吊销、停用、到期、自动续期、新版本发布都会通过心跳即时下发。
"""
import platform
import uuid
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
from .models import Alert, AuditLog, Device, Group, Hotel, License, Plan, RenewOrder, Setting

router = APIRouter(prefix="/api/client", tags=["client"])
client_security = HTTPBearer(auto_error=False)

CLIENT_TOKEN_DAYS = 90
CLIENT_VERSION = "1.2.2"  # 当前打包客户端版本；settings.client_version 为服务端要求的最新版


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


# P1-3 模块位（E5：先拆 ai_risk / group_portal 验证；未配置=全开，兼容存量客户）
MODULE_KEYS = ["ai_risk", "group_portal", "report", "api", "push"]
DEFAULT_FEATURES = list(MODULE_KEYS)


def effective_features(lic: License) -> list:
    raw = (lic.features or "").strip()
    if raw:
        try:
            import json

            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(x) for x in arr if str(x) in MODULE_KEYS]
        except Exception:
            pass
    return list(DEFAULT_FEATURES)


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
        # P1-3 模块/配额位（features 未配置=全开；max_rooms 0=不限）
        "features": effective_features(lic),
        "quotas": {
            "max_devices": lic.max_devices,
            "max_rooms": lic.max_rooms or 0,
        },
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

    # 指纹唯一性：同一设备只能绑定一家酒店（软删设备与已停用酒店不占指纹）
    other = None
    for cand in (
        db.query(Device)
        .filter(
            Device.fingerprint == fingerprint,
            Device.hotel_id != hotel.id,
            Device.status.in_(["在线", "离线"]),
        )
        .all()
    ):
        oh = db.query(Hotel).filter(Hotel.id == cand.hotel_id).first()
        if oh is not None and oh.status != "已停用":
            other = cand
            break
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
    # P0-1 同名迁移候选：同名、离线 ≥30 分钟、非本机（重装系统场景）
    migrate_target = None
    if req.deviceName:
        for cand in (
            db.query(Device)
            .filter(
                Device.hotel_id == hotel.id,
                Device.name == req.deviceName,
                Device.status.in_(["在线", "离线"]),
                Device.fingerprint != fingerprint,
            )
            .all()
        ):
            last = _parse_ts(cand.last_active)
            if last is None or (datetime.utcnow() - last).total_seconds() >= _offline_minutes(db) * 60:
                migrate_target = {
                    "id": cand.id,
                    "name": cand.name,
                    "last_active": cand.last_active,
                }
                break

    if device is None:
        # T38.1 并发防超卖：行锁串行化同店激活；populate_existing 强制刷新，
        # 防 SQLAlchemy 身份映射返回旧 devices 值（check-then-act 失效根因）
        locked = (
            db.query(Hotel)
            .filter(Hotel.id == hotel.id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if locked is not None:
            hotel = locked
        if hotel.devices >= hotel.max_devices and migrate_target is None:
            raise_alert(
                db, level="中", rule="设备数超限", hotel_name=hotel.name,
                detail=f"设备数已达上限 {hotel.max_devices} 仍尝试激活新设备",
            )
            db.commit()
            raise HTTPException(status_code=403, detail="设备数已达套餐上限，请先解绑或升级套餐")
        device = Device(
            id=f"cd{uuid.uuid4().hex[:12]}",
            hotel_id=hotel.id,
            name=req.deviceName or f"客户端-{fingerprint[:6]}",
            os=req.os_name or platform.platform(),
            fingerprint=fingerprint,
            last_active=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            status="在线",
            created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
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
        # P1-2 激活速度异常：24h 内新建设备（含本次）≥5 台
        created_recent = (
            db.query(Device)
            .filter(Device.hotel_id == hotel.id, Device.created_at >= day_ago)
            .count()
        )
        if created_recent >= 5:
            raise_alert(
                db, level="高", rule="激活速度异常", hotel_name=hotel.name,
                detail=f"24 小时内新建激活设备达 {created_recent} 台，疑似批量滥用授权",
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
        # P0-1 同名迁移提示（重装系统场景）：客户端确认后调 /devices/migrate
        "migration_hint": migrate_target is not None,
        "migrate_target": migrate_target,
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
        # P1-2 僵尸复活：离线 ≥90 天设备突然心跳（疑似授权转卖）
        old_active = _parse_ts(device.last_active)
        if (
            old_active is not None
            and device.status in ("在线", "离线")
            and (datetime.utcnow() - old_active).days >= 90
        ):
            raise_alert(
                db, level="中", rule="僵尸复活", hotel_name=hotel.name,
                detail=f"设备「{device.name}」离线 {(datetime.utcnow() - old_active).days} 天后突然心跳，疑似授权转卖或长期闲置后启用",
            )
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
        "plan": (lic.plan if lic and lic.plan else hotel.plan),  # F5：授权级套餐优先
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
    pushEnabled: Optional[bool] = None  # 每日定时推送开关（None=不修改）
    pushTime: str = ""  # 推送时间 HH:MM（默认 09:00）


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
    from .push_daily import get_push_config

    scope = "group" if ctx.scope == "group" else "hotel"
    if ctx.scope == "group":
        url = group_hooks(db).get(ctx.hotel_id, "")
    else:
        url = hotel_hook(db, ctx.hotel_id)
    cfg = get_push_config(db, scope, ctx.hotel_id)
    return {
        "scope": ctx.scope,
        "webhookUrl": url,
        "entityId": ctx.hotel_id,
        "pushEnabled": cfg["enabled"],
        "pushTime": cfg["time"],
        "lastPushDate": cfg["lastPushDate"],
    }


@router.post("/notify/config")
def notify_config_set(
    req: NotifyConfigReq,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """保存本实体的通知 webhook + 每日推送设置（只写自己的配置；域名白名单防 SSRF）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    from .wecom import set_group_hook, set_hotel_hook, validate_webhook_url
    from .masking import mask_text
    from .push_daily import get_push_config, set_push_config, valid_hhmm

    url = (req.webhookUrl or "").strip()
    if url and not validate_webhook_url(url):
        raise HTTPException(status_code=400, detail="仅支持企业微信官方机器人 webhook（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）")
    scope = "group" if ctx.scope == "group" else "hotel"
    if req.pushEnabled is not None or req.pushTime:
        t = (req.pushTime or "").strip() or get_push_config(db, scope, ctx.hotel_id)["time"]
        if not valid_hhmm(t):
            raise HTTPException(status_code=400, detail="推送时间格式应为 HH:MM（如 09:00）")
        enabled = req.pushEnabled if req.pushEnabled is not None else get_push_config(db, scope, ctx.hotel_id)["enabled"]
        set_push_config(db, scope, ctx.hotel_id, enabled=bool(enabled), push_time=t)
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
            detail=mask_text(f"webhook {url or '（已清空）'}；每日推送 {'开' if (req.pushEnabled is not None and req.pushEnabled) else '保持'} {req.pushTime or ''}"),
        )
    )
    db.commit()
    return {"ok": True, "saved": bool(url)}


@router.post("/notify/push-test")
def notify_push_test(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """立即生成并推送本实体今日日报（不影响每日定时；dry-run 时仅记录）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    from .push_daily import content_group, content_hotel, _push_one

    if ctx.scope == "group":
        content = content_group(db, ctx.hotel_id)
    else:
        content = content_hotel(db, ctx.hotel_id)
    if not content:
        raise HTTPException(status_code=404, detail="实体不存在")
    r = _push_one(db, "group" if ctx.scope == "group" else "hotel", ctx.hotel_id, content)
    return {"ok": True, "sent": r.get("sent"), "dryRun": r.get("dry_run"), "preview": content}


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


# ============================================================
# P0-1 自助换机/解绑 ｜ P0-2 续期自助化 ｜ P1-2 异常激活检测
# ============================================================
UNBIND_OFFLINE_MINUTES = 30  # 默认离线 ≥30 分钟才可自助解绑（settings.unbind_offline_minutes 可调，测试/运营用）
UNBIND_DAILY_LIMIT = 3       # 单店每日自助解绑/迁移合计上限
UNBIND_WEEK_ALERT = 3        # 7 天内 ≥3 次 → 升「高」告警并企微推送
RENEW_ORDER_TTL_HOURS = 2    # 待支付订单 2 小时自动关闭


def _offline_minutes(db: Session) -> int:
    from .wecom import get_setting

    try:
        return max(0, int(get_setting(db, "unbind_offline_minutes", str(UNBIND_OFFLINE_MINUTES))))
    except (TypeError, ValueError):
        return UNBIND_OFFLINE_MINUTES


class UnbindRequest(BaseModel):
    device_id: str


class MigrateRequest(BaseModel):
    old_device_id: str


class RenewOrderCreateReq(BaseModel):
    months: int = 12
    plan: str = ""            # 目标套餐（空=同档续期；非空=升级）
    channel: str = "transfer"  # transfer=对公凭证（P0）/ wxpay（预留）
    hotel_ids: list[str] = []  # 集团批量：门店 ID 列表（空=全部成员）


class RenewProofReq(BaseModel):
    proof_no: str = ""
    note: str = ""
    image_base64: str = ""    # 可选：回单图片（base64，不含 data: 前缀）


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(s: str) -> Optional[datetime]:
    """兼容秒级/分级/日期三种历史时间格式"""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _active_device_filters(q):
    return q.filter(Device.status.in_(["在线", "离线"]))


def _check_unbind_limits(db: Session, hotel_id: str) -> None:
    """每日 ≤3 次；7 天内 ≥3 次触发「频繁自助换机」高告警 + 企微推送"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    day_count = (
        db.query(Device)
        .filter(
            Device.hotel_id == hotel_id,
            Device.unbound_by.in_(["self", "migration"]),
            Device.unbound_at >= f"{today} 00:00:00",
        )
        .count()
    )
    if day_count >= UNBIND_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"今日自助换机/解绑已达 {UNBIND_DAILY_LIMIT} 次上限，请联系服务商处理",
        )
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    week_count = (
        db.query(Device)
        .filter(
            Device.hotel_id == hotel_id,
            Device.unbound_by.in_(["self", "migration"]),
            Device.unbound_at >= week_ago,
        )
        .count()
    )
    if week_count + 1 >= UNBIND_WEEK_ALERT:
        raise_alert(
            db, level="高", rule="频繁自助换机", hotel_name=hotel_id,
            detail=f"7 天内第 {week_count + 1} 次自助换机/解绑，建议人工核实（防自助能力被滥用）",
        )
        from .wecom import push_message

        push_message(
            db,
            f"**🚨 频繁自助换机告警**\n客户 `{hotel_id}` 7 天内已自助换机 {week_count + 1} 次，请核实是否正常设备更替。",
            hotel_id=hotel_id,
        )


def _resolve_device_for_ctx(db: Session, ctx: ClientContext, device_id: str) -> tuple:
    """按作用域取设备 + 所属酒店（单店=本店；集团=本集团成员店）"""
    d = db.query(Device).filter(Device.id == device_id).first()
    if d is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    hotel = db.query(Hotel).filter(Hotel.id == d.hotel_id).first()
    if ctx.scope == "hotel":
        if d.hotel_id != ctx.hotel_id:
            raise HTTPException(status_code=403, detail="无权操作其他酒店的设备")
    else:
        if hotel is None or hotel.group_id != ctx.hotel_id:
            raise HTTPException(status_code=403, detail="无权操作非本集团的设备")
    return d, hotel


@router.get("/devices")
def list_devices(
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """设备清单（含软删记录），当前设备标 is_self；集团总端返回全集团设备"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    q = db.query(Device)
    if ctx.scope == "hotel":
        q = q.filter(Device.hotel_id == ctx.hotel_id)
    else:
        member_ids = [h.id for h in db.query(Hotel).filter(Hotel.group_id == ctx.hotel_id).all()]
        q = q.filter(Device.hotel_id.in_(member_ids or [""]))
    rows = q.order_by(Device.last_active.desc()).all()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    used = (
        db.query(Device)
        .filter(
            Device.hotel_id == ctx.hotel_id if ctx.scope == "hotel" else Device.hotel_id.in_(member_ids or [""]),
            Device.unbound_by.in_(["self", "migration"]),
            Device.unbound_at >= f"{today} 00:00:00",
        )
        .count()
    )
    return {
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "os": d.os,
                "fingerprint": d.fingerprint,
                "last_active": d.last_active,
                "status": d.status,
                "unbound_at": d.unbound_at,
                "unbound_by": d.unbound_by,
                "hotel_id": d.hotel_id,
                "is_self": d.fingerprint == ctx.fingerprint,
            }
            for d in rows
        ],
        "quota_left_today": max(0, UNBIND_DAILY_LIMIT - used),
    }


@router.post("/devices/unbind")
def unbind_device(
    req: UnbindRequest,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """自助解绑：非本机 + 离线 ≥30 分钟；日限 3 次；软删保留痕迹可后台恢复"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    d, hotel = _resolve_device_for_ctx(db, ctx, req.device_id)
    if d.fingerprint == ctx.fingerprint:
        raise HTTPException(status_code=400, detail="不能解绑本机，请在其他设备上操作或使用「退出激活」")
    if d.status not in ("在线", "离线"):
        raise HTTPException(status_code=409, detail=f"设备状态为「{d.status}」，不可解绑")
    last = _parse_ts(d.last_active)
    if last is not None and (datetime.utcnow() - last).total_seconds() < _offline_minutes(db) * 60:
        raise HTTPException(
            status_code=409,
            detail=f"该设备最近 {_offline_minutes(db)} 分钟内仍活跃，暂不可解绑（防误拆在用机器）",
        )
    _check_unbind_limits(db, hotel.id if hotel else d.hotel_id)

    # T38.2 并发防双解绑：条件更新（仅 在线/离线 → 已解绑）保证只有一个请求能改到行
    won = (
        db.query(Device)
        .filter(Device.id == d.id, Device.status.in_(["在线", "离线"]))
        .update(
            {"status": "已解绑", "unbound_at": _now_str(), "unbound_by": "self"},
            synchronize_session=False,
        )
    )
    if not won:
        raise HTTPException(status_code=409, detail=f"设备状态为「{d.status}」，不可解绑")
    if hotel:
        db.query(Hotel).filter(Hotel.id == hotel.id, Hotel.devices > 0).update(
            {"devices": Hotel.devices - 1}, synchronize_session=False
        )
    raise_alert(
        db, level="中", rule="自助换机", hotel_name=hotel.name if hotel else d.hotel_id,
        detail=f"客户自助解绑设备「{d.name}」（{d.id}），配额已释放",
    )
    db.add(
        AuditLog(
            operator=f"客户端({hotel.name if hotel else d.hotel_id})",
            action="自助解绑",
            target=d.name,
            detail=f"设备 {d.id} 解绑（指纹 {d.fingerprint[:12]}…）",
        )
    )
    db.commit()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    used = (
        db.query(Device)
        .filter(
            Device.hotel_id == d.hotel_id,
            Device.unbound_by.in_(["self", "migration"]),
            Device.unbound_at >= f"{today} 00:00:00",
        )
        .count()
    )
    return {"ok": True, "quota_left_today": max(0, UNBIND_DAILY_LIMIT - used)}


@router.post("/devices/migrate")
def migrate_device(
    req: MigrateRequest,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """同名迁移（重装系统场景）：把旧同名离线设备置「已迁移」，本机占用其配额"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    d, hotel = _resolve_device_for_ctx(db, ctx, req.old_device_id)
    me = (
        db.query(Device)
        .filter(Device.hotel_id == d.hotel_id, Device.fingerprint == ctx.fingerprint)
        .first()
    )
    if me is None:
        raise HTTPException(status_code=400, detail="本机尚未注册为设备，请先激活")
    if d.fingerprint == ctx.fingerprint:
        raise HTTPException(status_code=400, detail="目标设备就是本机")
    if d.status not in ("在线", "离线"):
        raise HTTPException(status_code=409, detail=f"设备状态为「{d.status}」，不可迁移")
    if d.name != me.name:
        raise HTTPException(status_code=409, detail="仅支持同名设备迁移（重装系统场景）")
    last = _parse_ts(d.last_active)
    if last is not None and (datetime.utcnow() - last).total_seconds() < _offline_minutes(db) * 60:
        raise HTTPException(status_code=409, detail="旧设备仍在线活跃，暂不可迁移")
    _check_unbind_limits(db, hotel.id if hotel else d.hotel_id)

    # T38.2 并发防重复迁移：条件更新（仅 在线/离线 → 已迁移）保证只有一个请求能改到行
    won = (
        db.query(Device)
        .filter(Device.id == d.id, Device.status.in_(["在线", "离线"]))
        .update(
            {"status": "已迁移", "unbound_at": _now_str(), "unbound_by": "migration"},
            synchronize_session=False,
        )
    )
    if not won:
        raise HTTPException(status_code=409, detail=f"设备状态为「{d.status}」，不可迁移")
    if hotel:
        db.query(Hotel).filter(Hotel.id == hotel.id, Hotel.devices > 0).update(
            {"devices": Hotel.devices - 1}, synchronize_session=False
        )
    raise_alert(
        db, level="中", rule="自助换机", hotel_name=hotel.name if hotel else d.hotel_id,
        detail=f"同名迁移：旧设备「{d.name}」({d.id}) 已由新机 {me.id} 接管",
    )
    db.add(
        AuditLog(
            operator=f"客户端({hotel.name if hotel else d.hotel_id})",
            action="同名迁移",
            target=d.name,
            detail=f"旧设备 {d.id} → 新设备 {me.id}",
        )
    )
    db.commit()
    return {"ok": True}


# ---------- P0-2 续期自助化 ----------
def _plan_price(db: Session, plan_name: str, months: int) -> float:
    """金额服务端计算：按 Plan.yearly_price 折算月数（升级按目标套餐全价）"""
    p = db.query(Plan).filter(Plan.name == plan_name).first()
    yearly = float(p.yearly_price or p.price or 0) if p else 0.0
    return round(yearly * months / 12.0, 2)


def _order_ttl_hours(db: Session) -> float:
    """待支付订单超时时长（小时），settings 可配（测试/运营调优用），默认 2"""
    from .wecom import get_setting

    try:
        return max(0.0, float(get_setting(db, "renew_order_ttl_hours", str(RENEW_ORDER_TTL_HOURS))))
    except (TypeError, ValueError):
        return RENEW_ORDER_TTL_HOURS


def _close_stale_orders(db: Session) -> None:
    """惰性关单：待支付超 TTL（默认 2 小时）自动关闭"""
    deadline = (datetime.utcnow() - timedelta(hours=_order_ttl_hours(db))).strftime("%Y-%m-%d %H:%M:%S")
    stale = (
        db.query(RenewOrder)
        .filter(RenewOrder.status == "待支付", RenewOrder.created_at < deadline)
        .all()
    )
    for o in stale:
        o.status = "已关闭"
        o.closed_at = _now_str()
    if stale:
        db.commit()


@router.post("/renew/order")
def create_renew_order(
    req: RenewOrderCreateReq,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """创建续期订单（金额服务端计算）。单店=本店；集团=批量（hotel_ids 空=全部成员）"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    if req.months not in (12, 24):
        raise HTTPException(status_code=400, detail="仅支持续期 12 或 24 个月")
    _close_stale_orders(db)

    targets: list = []
    target_type = "hotel"
    if ctx.scope == "group":
        target_type = "group"
        g = db.query(Group).filter(Group.id == ctx.hotel_id).first()
        if g is None:
            raise HTTPException(status_code=404, detail="集团不存在")
        q = db.query(Hotel).filter(Hotel.group_id == g.id)
        if req.hotel_ids:
            q = q.filter(Hotel.id.in_(req.hotel_ids))
        targets = q.all()
        if not targets:
            raise HTTPException(status_code=400, detail="没有可续期的门店")
    else:
        hotel = db.query(Hotel).filter(Hotel.id == ctx.hotel_id).first()
        if hotel is None:
            raise HTTPException(status_code=404, detail="客户不存在")
        targets = [hotel]

    amount = 0.0
    for h in targets:
        plan_name = req.plan or h.plan
        amount += _plan_price(db, plan_name, req.months)

    order = RenewOrder(
        order_no=f"RO-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        target_type=target_type,
        target_id=ctx.hotel_id,
        target_ids=",".join(h.id for h in targets),
        target_name=(targets[0].name if target_type == "hotel" else f"{len(targets)} 家门店"),
        plan=req.plan,
        months=req.months,
        amount=amount,
        channel="wxpay" if req.channel == "wxpay" else "transfer",
        status="待支付",
        created_at=_now_str(),
    )
    db.add(order)
    db.add(
        AuditLog(
            operator=f"客户端({order.target_name})",
            action="创建续期订单",
            target=order.order_no,
            detail=f"{req.months} 个月 · ¥{amount:.2f} · {order.channel}",
        )
    )
    db.commit()
    return {
        "ok": True,
        "order_no": order.order_no,
        "amount": order.amount,
        "months": order.months,
        "channel": order.channel,
        "status": order.status,
    }


def _my_order(db: Session, ctx: ClientContext, order_no: str) -> RenewOrder:
    _close_stale_orders(db)
    o = db.query(RenewOrder).filter(RenewOrder.order_no == order_no).first()
    if o is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if o.target_id != ctx.hotel_id:
        raise HTTPException(status_code=403, detail="无权查看该订单")
    return o


@router.get("/renew/order/{order_no}")
def get_renew_order(
    order_no: str,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    o = _my_order(db, ctx, order_no)
    return {
        "order_no": o.order_no,
        "status": o.status,
        "amount": o.amount,
        "months": o.months,
        "plan": o.plan,
        "channel": o.channel,
        "created_at": o.created_at,
        "reject_reason": o.reject_reason,
    }


@router.post("/renew/order/{order_no}/proof")
def submit_renew_proof(
    order_no: str,
    req: RenewProofReq,
    ctx: ClientContext = Depends(get_client_context),
    fingerprint: str = Depends(get_fingerprint_header),
    db: Session = Depends(get_db),
):
    """对公转账：上传回单号/备注/可选图片 → 订单进入「待确认」，服务商确认到账后生效"""
    if fingerprint and fingerprint != ctx.fingerprint:
        raise HTTPException(status_code=401, detail="设备指纹不一致")
    o = _my_order(db, ctx, order_no)
    if o.status != "待支付":
        raise HTTPException(status_code=409, detail=f"订单状态为「{o.status}」，不可提交凭证")
    if not req.proof_no.strip():
        raise HTTPException(status_code=400, detail="请填写汇款回单号/流水号")

    proof_file = ""
    if req.image_base64:
        try:
            import base64
            from pathlib import Path

            raw = base64.b64decode(req.image_base64.split(",")[-1])
            if len(raw) > 5 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="回单图片不能超过 5MB")
            save_dir = Path(__file__).resolve().parent.parent / "uploads" / "renew_proof"
            save_dir.mkdir(parents=True, exist_ok=True)
            fpath = save_dir / f"{o.order_no}.bin"
            fpath.write_bytes(raw)
            proof_file = str(fpath)
        except HTTPException:
            raise
        except Exception:
            proof_file = ""  # 图片落盘失败不阻断凭证提交

    o.proof_no = req.proof_no.strip()
    o.proof_note = req.note.strip()[:256]
    o.proof_file = proof_file
    o.status = "待确认"
    db.add(
        AuditLog(
            operator=f"客户端({o.target_name})",
            action="提交续期凭证",
            target=o.order_no,
            detail=f"回单号 {o.proof_no}（{o.months} 个月 · ¥{o.amount:.2f}）",
        )
    )
    db.commit()
    return {"ok": True, "status": o.status}
