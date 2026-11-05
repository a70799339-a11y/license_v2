"""License 授权管理系统 —— FastAPI 主入口
自研后端，数据存自建 PostgreSQL，单进程同时托管前端构建产物与 API
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import create_token, get_current_user, require_roles, verify_password
from .database import Base, engine, get_db
from .models import Alert, AuditLog, Device, Group, Hotel, License, Plan, Setting, User
from .schemas import (
    AlertOut, AuditLogOut, BatchDisableRequest, BatchRenewRequest, DeviceOut,
    HotelCreate, HotelOut, HotelUpdate, LicenseCreate, LicenseOut,
    LoginRequest, PlanOut, RevokeRequest, TokenResponse,
)
from .seed import PLAN_MAXDEV, seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed(db)
    finally:
        db.close()
    # 企微智能机器人长连接（配置齐备时自动启动，读 backend/.env 或环境变量）
    try:
        from .wecom_ws import start_ws

        start_ws()
    except Exception:
        pass
    yield


app = FastAPI(title="酒店AI管理 License 授权管理系统", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产收敛为你们自己的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def write_log(db: Session, user: User, action: str, target: str, detail: str) -> None:
    """审计写入口 —— 落库前统一脱敏（手机/身份证/邮箱/卡号/令牌/指纹/授权码）"""
    from .masking import mask_text

    db.add(
        AuditLog(
            operator=user.name,
            action=action,
            target=mask_text(target),
            detail=mask_text(detail),
        )
    )


def next_code(db: Session, model, prefix: str) -> str:
    """生成不与现有记录冲突的编号：取现有最大数字序号 +1"""
    import re
    max_seq = 0
    for (code,) in db.query(model.code).all():
        m = re.search(r"(\d+)$", code or "")
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return f"{prefix}{max_seq + 1:04d}"


# ---------- 认证 ----------
@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return TokenResponse(token=create_token(user.username, user.role), username=user.username, name=user.name, role=user.role)


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "name": user.name, "role": user.role}


# ---------- 客户（酒店） ----------
@app.get("/api/hotels", response_model=list[HotelOut])
def list_hotels(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [HotelOut.from_orm_model(h) for h in db.query(Hotel).order_by(Hotel.id).all()]


@app.get("/api/hotels/page")
def list_hotels_page(
    page: int = 1,
    page_size: int = 50,
    keyword: str = "",
    status: str = "",
    plan: str = "",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """分页 + 筛选（P2 规模化：支撑 1000+ 家，前端配虚拟滚动）"""
    q = db.query(Hotel)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            (Hotel.name.like(like)) | (Hotel.code.like(like)) | (Hotel.group.like(like))
        )
    if status:
        q = q.filter(Hotel.status == status)
    if plan:
        q = q.filter(Hotel.plan == plan)
    total = q.count()
    items = (
        q.order_by(Hotel.id)
        .offset(max(page - 1, 0) * page_size)
        .limit(min(page_size, 500))
        .all()
    )
    return {
        "items": [HotelOut.from_orm_model(h).model_dump() for h in items],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


class HotelCreateOut(BaseModel):
    ok: bool
    id: str


@app.post("/api/hotels", response_model=HotelCreateOut)
def create_hotel(req: HotelCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    if db.query(Hotel).filter(Hotel.code == req.code).first():
        raise HTTPException(status_code=400, detail="客户编号已存在")
    max_num = 0
    for (hid,) in db.query(Hotel.id).all():
        import re
        m = re.search(r"(\d+)$", hid or "")
        if m:
            max_num = max(max_num, int(m.group(1)))
    hid = f"h{max_num + 1:03d}"
    expire = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d")
    db.add(Hotel(
        id=hid, name=req.name, code=req.code, group=req.group, city=req.city,
        contact=req.contact, phone=req.phone, plan=req.plan, status="正常",
        devices=0, max_devices=PLAN_MAXDEV.get(req.plan, 30), expire_at=expire,
        auto_renew=False, created_at=datetime.utcnow().strftime("%Y-%m-%d"), last_active="",
    ))
    write_log(db, user, "新增客户", req.name, f"套餐：{req.plan}")
    db.commit()
    return HotelCreateOut(ok=True, id=hid)


@app.post("/api/hotels/batch-renew", response_model=list[HotelOut])
def batch_renew(req: BatchRenewRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    hotels = db.query(Hotel).filter(Hotel.id.in_(req.ids)).all()
    for h in hotels:
        h.expire_at = req.expireAt
        h.status = "正常"
        h.auto_renew = True
    write_log(db, user, "批量续期", f"{len(hotels)} 家酒店", f"续期至 {req.expireAt}")
    db.commit()
    return [HotelOut.from_orm_model(h) for h in hotels]


@app.post("/api/hotels/batch-disable", response_model=list[HotelOut])
def batch_disable(req: BatchDisableRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    hotels = db.query(Hotel).filter(Hotel.id.in_(req.ids)).all()
    for h in hotels:
        h.status = "已停用"
    write_log(db, user, "批量停用", f"{len(hotels)} 家酒店", "批量停用授权")
    db.commit()
    return [HotelOut.from_orm_model(h) for h in hotels]


@app.patch("/api/hotels/{hotel_id}", response_model=HotelOut)
def update_hotel(hotel_id: str, upd: HotelUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    h = db.query(Hotel).filter(Hotel.id == hotel_id).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if upd.status is not None:
        h.status = upd.status
    if upd.autoRenew is not None:
        h.auto_renew = upd.autoRenew
    if upd.expireAt is not None:
        h.expire_at = upd.expireAt
    if upd.plan is not None:
        h.plan = upd.plan
    write_log(db, user, "编辑客户", h.name, "更新客户信息")
    db.commit()
    return HotelOut.from_orm_model(h)


@app.get("/api/hotels/{hotel_id}/detail")
def hotel_detail(hotel_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    h = db.query(Hotel).filter(Hotel.id == hotel_id).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    return {
        "hotel": HotelOut.from_orm_model(h),
        "devices": [DeviceOut.from_orm_model(d) for d in db.query(Device).filter(Device.hotel_id == hotel_id).all()],
        "licenses": [LicenseOut.from_orm_model(l) for l in db.query(License).filter(License.hotel_name == h.name).all()],
        "alerts": [AlertOut.from_orm_model(a) for a in db.query(Alert).filter(Alert.hotel_name == h.name).all()],
    }


# ---------- 授权 ----------
@app.get("/api/licenses", response_model=list[LicenseOut])
def list_licenses(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [LicenseOut.from_orm_model(l) for l in db.query(License).order_by(License.id).all()]


@app.post("/api/licenses", response_model=LicenseOut)
def create_license(req: LicenseCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    h = db.query(Hotel).filter(Hotel.id == req.hotelId).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    seq = db.query(License).count() + 1
    issued = datetime.utcnow()
    expire = issued + timedelta(days=30 * req.months)
    lic = License(
        id=f"ln{seq:04d}", code=next_code(db, License, f"LIC-{issued.year}-"), hotel_name=h.name,
        plan=req.plan, type=req.type, issued_at=issued.strftime("%Y-%m-%d"),
        expire_at=expire.strftime("%Y-%m-%d"), status="生效中",
        devices=0, max_devices=PLAN_MAXDEV.get(req.plan, 30),
    )
    db.add(lic)
    h.expire_at = lic.expire_at
    h.status = "正常"
    write_log(db, user, "发放授权", h.name, f"签发{req.plan}授权 {lic.code}，有效期 {req.months} 个月")
    db.commit()
    return LicenseOut.from_orm_model(lic)


@app.post("/api/licenses/{license_id}/revoke")
def revoke_license(license_id: str, req: RevokeRequest | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    lic = db.query(License).filter(License.id == license_id).first()
    if lic is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    lic.status = "已吊销"
    reason = req.reason if req else "手动吊销"
    write_log(db, user, "吊销授权", lic.hotel_name, f"授权 {lic.code}，原因：{reason}")
    db.commit()
    return {"ok": True, "status": lic.status, "reason": reason}


# ---------- 告警 ----------
@app.get("/api/alerts")
def list_alerts(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from .alerting import scan_stale_heartbeats

    scan_stale_heartbeats(db)  # 读取时惰性扫描长期无心跳的在册酒店
    try:  # 企微日报惰性触发（仅真实推送开启时）
        from .wecom import flush_daily, push_enabled

        if push_enabled(db):
            flush_daily(db)
    except Exception:
        pass
    return [AlertOut.from_orm_model(a) for a in db.query(Alert).order_by(Alert.time.desc()).all()]


@app.post("/api/alerts/{alert_id}/handle")
def handle_alert(alert_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    a.status = "已处置"
    write_log(db, user, "处置告警", a.rule, f"{a.hotel_name}：{a.detail}")
    db.commit()
    return {"ok": True, "status": a.status}


@app.get("/api/alerts/groups")
def alert_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """告警聚合（P2）：按规则聚合，规模上去后一眼看清哪类问题最多"""
    groups: dict[str, dict] = {}
    for a in db.query(Alert).order_by(Alert.time.desc()).all():
        g = groups.setdefault(
            a.rule,
            {
                "rule": a.rule,
                "level": a.level,
                "count": 0,
                "pending": 0,
                "hotels": set(),
                "latest": "",
                "sample": a.detail,
            },
        )
        g["count"] += 1
        if a.status != "已处置":
            g["pending"] += 1
        g["hotels"].add(a.hotel_name)
        if not g["latest"]:
            g["latest"] = a.time
    out = []
    for g in groups.values():
        g["hotelCount"] = len(g.pop("hotels"))
        out.append(g)
    out.sort(key=lambda x: (-x["pending"], -x["count"]))
    return out


# ---------- 套餐 ----------
@app.get("/api/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [PlanOut.from_orm_model(p) for p in db.query(Plan).order_by(Plan.id).all()]


# ---------- 设置 ----------
class SettingUpdate(BaseModel):
    key: str
    value: str


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    # 下划线前缀为敏感配置（如签名公钥指纹等），不对外返回
    return {s.key: s.value for s in db.query(Setting).all() if not s.key.startswith("_")}


# ---------- 用户与权限（admin） ----------
@app.get("/api/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    return [
        {"username": u.username, "name": u.name, "role": u.role}
        for u in db.query(User).order_by(User.username).all()
    ]


class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    role: str = "operator"


@app.post("/api/users")
def create_user(req: UserCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if req.role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="角色必须是 admin/operator/viewer")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    from .auth import hash_password

    db.add(User(username=req.username, password_hash=hash_password(req.password), name=req.name, role=req.role))
    write_log(db, user, "创建用户", req.username, f"角色：{req.role}")
    db.commit()
    return {"ok": True}


# ---------- 集团管理（总/子关系） ----------
class GroupCreate(BaseModel):
    name: str
    contact: str = ""
    phone: str = ""
    plan: str = "旗舰版"
    expireAt: str = "2027-12-31"


class GroupAssignRequest(BaseModel):
    hotelIds: list[str]
    groupId: str


def _group_out(db: Session, g) -> dict:
    members = db.query(Hotel).filter(Hotel.group_id == g.id).all()
    return {
        "id": g.id,
        "name": g.name,
        "code": g.code,
        "contact": g.contact,
        "phone": g.phone,
        "status": g.status,
        "plan": g.plan,
        "expireAt": g.expire_at,
        "autoRenew": bool(g.auto_renew),
        "memberCount": len(members),
        "deviceCount": sum(m.devices or 0 for m in members),
        "expiringSoon": sum(1 for m in members if m.status in ("即将到期", "已过期")),
    }


@app.get("/api/groups")
def list_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [_group_out(db, g) for g in db.query(Group).order_by(Group.id).all()]


@app.post("/api/groups")
def create_group(req: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    if db.query(Group).filter(Group.name == req.name).first():
        raise HTTPException(status_code=400, detail="集团名称已存在")
    seq = db.query(Group).count() + 1
    while db.query(Group).filter(Group.code == f"GP-{seq:04d}").first():
        seq += 1
    g = Group(
        id=f"g{seq:03d}",
        name=req.name,
        code=f"GP-{seq:04d}",
        contact=req.contact,
        phone=req.phone,
        plan=req.plan,
        expire_at=req.expireAt,
        created_at=datetime.utcnow().strftime("%Y-%m-%d"),
    )
    db.add(g)
    write_log(db, user, "创建集团", req.name, f"集团码 {g.code}")
    db.commit()
    return {"ok": True, "id": g.id, "code": g.code}


@app.get("/api/groups/{group_id}")
def group_detail(group_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")
    members = db.query(Hotel).filter(Hotel.group_id == group_id).order_by(Hotel.id).all()
    return {
        "group": _group_out(db, g),
        "members": [HotelOut.from_orm_model(h).model_dump() for h in members],
    }


@app.post("/api/groups/assign")
def assign_hotels(req: GroupAssignRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    g = db.query(Group).filter(Group.id == req.groupId).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")
    for hid in req.hotelIds:
        h = db.query(Hotel).filter(Hotel.id == hid).first()
        if h is not None:
            h.group_id = g.id
            h.group = g.name
    write_log(db, user, "调整门店归属", g.name, f"{len(req.hotelIds)} 家门店划入")
    db.commit()
    return {"ok": True}


@app.post("/api/groups/{group_id}/renew")
def group_renew(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    g = db.query(Group).filter(Group.id == group_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")
    members = db.query(Hotel).filter(Hotel.group_id == group_id).all()
    for h in members:
        h.expire_at = "2027-12-31"
        if h.status in ("即将到期", "已过期"):
            h.status = "正常"
    write_log(db, user, "集团续期", g.name, f"{len(members)} 家门店续期至 2027-12-31")
    db.commit()
    return {"ok": True, "renewed": len(members)}


# ---------- 企业微信机器人 ----------
class WecomConfigUpdate(BaseModel):
    opsWebhook: str = ""
    pushEnabled: bool = False
    dryRun: bool = True


class WecomGroupHookReq(BaseModel):
    groupId: str
    webhookUrl: str


class WecomSimulateReq(BaseModel):
    text: str
    userid: str = "admin-sim"


class WecomBindingReq(BaseModel):
    userid: str
    name: str = ""
    role: str = "viewer"
    groupId: str = ""


def _mask_url(url: str) -> str:
    from .masking import mask_text

    return mask_text(url) if url else ""


@app.get("/api/wecom/config")
def wecom_config(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "operator"))):
    from .wecom import group_hooks, is_dry_run, ops_webhook, push_enabled, get_setting
    from .wecom_ws import ws_status

    secret = get_setting(db, "wecom_bot_secret")
    return {
        "opsWebhook": _mask_url(ops_webhook(db)),
        "pushEnabled": push_enabled(db),
        "dryRun": is_dry_run(db),
        "groupHooks": {gid: _mask_url(u) for gid, u in group_hooks(db).items()},
        "botId": get_setting(db, "wecom_bot_id"),
        "botSecretMasked": (secret[:4] + "***") if secret else "",
        "wsEnabled": get_setting(db, "wecom_bot_enabled") == "on",
        "wsStatus": ws_status(),
    }


@app.post("/api/wecom/config")
def wecom_config_update(
    req: WecomConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    from .wecom import set_setting

    if req.opsWebhook.strip():
        set_setting(db, "wecom_ops_webhook", req.opsWebhook.strip())
    set_setting(db, "wecom_push_enabled", "on" if req.pushEnabled else "off")
    set_setting(db, "wecom_dry_run", "off" if not req.dryRun else "on")
    write_log(db, user, "企微配置", "wecom", f"推送={'开' if req.pushEnabled else '关'} dry-run={'开' if req.dryRun else '关'}")
    db.commit()
    return {"ok": True}


@app.post("/api/wecom/group-hook")
def wecom_group_hook(
    req: WecomGroupHookReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    from .models import WecomGroupHook

    row = db.query(WecomGroupHook).filter(WecomGroupHook.group_id == req.groupId).first()
    if row is None:
        db.add(WecomGroupHook(group_id=req.groupId, webhook_url=req.webhookUrl.strip()))
    else:
        row.webhook_url = req.webhookUrl.strip()
    write_log(db, user, "企微配置", req.groupId, "配置集团群 webhook")
    db.commit()
    return {"ok": True}


@app.post("/api/wecom/test")
def wecom_test(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    from .wecom import push_message

    r = push_message(db, "**✅ 企微推送测试**\nLicense 授权管理系统通知链路正常。")
    write_log(db, user, "企微测试", "wecom", f"测试推送 {r.get('sent')}")
    db.commit()
    return {"ok": True, "sent": r.get("sent"), "dryRun": r.get("dry_run")}


@app.get("/api/wecom/ws-status")
def wecom_ws_status(_: User = Depends(require_roles("admin", "operator"))):
    from .wecom_ws import ws_status

    return ws_status()


@app.post("/api/wecom/flush-daily")
def wecom_flush_daily(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from .wecom import flush_daily

    return flush_daily(db)


@app.post("/api/wecom/simulate")
def wecom_simulate(
    req: WecomSimulateReq,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    """调试入口：模拟企微消息进入指令管道（生产接自建应用回调后走 /api/wecom/callback）"""
    from .wecom import process_command

    return {"reply": process_command(db, req.text, req.userid)}


@app.get("/api/wecom/bindings")
def wecom_bindings(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    from .models import WecomBinding

    return [
        {"userid": b.userid, "name": b.name, "role": b.role, "groupId": b.group_id}
        for b in db.query(WecomBinding).all()
    ]


@app.post("/api/wecom/bindings")
def wecom_binding_create(
    req: WecomBindingReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    from .models import WecomBinding

    row = db.query(WecomBinding).filter(WecomBinding.userid == req.userid).first()
    if row is None:
        db.add(WecomBinding(userid=req.userid, name=req.name, role=req.role, group_id=req.groupId))
    else:
        row.name, row.role, row.group_id = req.name, req.role, req.groupId
    write_log(db, user, "企微绑定", req.userid, f"角色：{req.role} 集团：{req.groupId or '-'}")
    db.commit()
    return {"ok": True}


@app.post("/api/settings")
def update_setting(req: SettingUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    s = db.query(Setting).filter(Setting.key == req.key).first()
    if s is None:
        db.add(Setting(key=req.key, value=req.value))
    else:
        s.value = req.value
    write_log(db, user, "修改设置", req.key, f"设置为 {req.value}")
    db.commit()
    return {"ok": True}


# ---------- 操作日志 ----------
@app.get("/api/logs", response_model=list[AuditLogOut])
def list_logs(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(100).all()
    return [AuditLogOut.from_orm_model(a) for a in logs]


# ---------- 统计（经营总览） ----------
@app.get("/api/stats")
def stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from .alerting import scan_stale_heartbeats

    scan_stale_heartbeats(db)
    hotels = db.query(Hotel).all()
    today = datetime.utcnow()
    expire_soon = {"d7": 0, "d30": 0, "d60": 0, "d90": 0}
    for h in hotels:
        try:
            days = (datetime.strptime(h.expire_at, "%Y-%m-%d") - today).days
        except Exception:
            continue
        if days <= 7:
            expire_soon["d7"] += 1
        if days <= 30:
            expire_soon["d30"] += 1
        if days <= 60:
            expire_soon["d60"] += 1
        if days <= 90:
            expire_soon["d90"] += 1
    return {
        "total": len(hotels),
        "devices": sum(h.devices for h in hotels),
        "expiring": sum(1 for h in hotels if h.status == "即将到期"),
        "alerts": db.query(Alert).filter(Alert.status != "已处置").count(),
        "expireSoon": expire_soon,
    }


# ---------- 酒店客户端 API（激活/心跳/版本同步，须注册在 SPA 兜底之前） ----------
from .client_api import router as client_router

app.include_router(client_router)


# ---------- 静态资源（前端构建产物） ----------
import os
from fastapi.responses import FileResponse
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    index_file = os.path.join(static_dir, "index.html")

    # SPA history 模式兜底：非 /api 路径一律回 index.html（直接访问/刷新子路由不 404）
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.join(static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_file)

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
