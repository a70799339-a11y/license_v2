"""License 授权管理系统 —— FastAPI 主入口
自研后端，数据存自建 PostgreSQL，单进程同时托管前端构建产物与 API
"""
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
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
        # 幂等列迁移（老库升级补列，新库由 create_all 建全）
        for ddl in (
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS handled_by VARCHAR(64) DEFAULT ''",
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS handle_note VARCHAR(256) DEFAULT ''",
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS handled_at VARCHAR(32) DEFAULT ''",
            # P0-1/P1-2 设备自助换机痕迹 + 激活速度检测
            "ALTER TABLE devices ADD COLUMN IF NOT EXISTS created_at VARCHAR(32) DEFAULT ''",
            "ALTER TABLE devices ADD COLUMN IF NOT EXISTS unbound_at VARCHAR(32) DEFAULT ''",
            "ALTER TABLE devices ADD COLUMN IF NOT EXISTS unbound_by VARCHAR(16) DEFAULT ''",
            # P1-3 模块/配额位
            "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS features TEXT DEFAULT ''",
            "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS max_rooms INTEGER DEFAULT 0",
            # N1 集团高熵激活码（展示编号 GP-xxxx 与激活凭据分离）
            "ALTER TABLE groups ADD COLUMN IF NOT EXISTS activation_key VARCHAR(32) DEFAULT ''",
        ):
            db.execute(__import__("sqlalchemy").text(ddl))
        seed(db)
        # N1/R3 回填：存量集团补高熵激活码（幂等，仅补空）——放在 seed 之后，
        # 覆盖 ensure_groups 新建的集团；随后建唯一索引防碰撞
        import secrets as _secrets

        for g in db.query(Group).filter((Group.activation_key == "") | (Group.activation_key.is_(None))).all():
            g.activation_key = f"GPK-{_secrets.token_hex(8)}"
        db.commit()
        try:
            db.execute(__import__("sqlalchemy").text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_activation_key ON groups (activation_key)"
            ))
            db.commit()
        except Exception:
            db.rollback()  # 索引已存在/极端碰撞时跳过，不阻断启动
    finally:
        db.close()
    # 企微智能机器人长连接（配置齐备时自动启动，读 backend/.env 或环境变量）
    try:
        from .wecom_ws import start_ws

        start_ws()
    except Exception:
        pass
    # 每日定时推送调度线程（服务端异常汇总 / 集团日报 / 子酒店日报）
    try:
        import threading

        from .push_daily import push_scheduler_loop

        threading.Thread(target=push_scheduler_loop, daemon=True, name="push-daily").start()
    except Exception:
        pass
    yield


app = FastAPI(title="酒店AI管理 License 授权管理系统", version="1.1.0", lifespan=lifespan)

# S8：CORS 白名单化——CORS_ORIGINS 环境变量（逗号分隔）；默认 * 时不带凭证（* + credentials 组合非法）
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=_cors_origins != ["*"],
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


def new_license_code(db: Session, year: int) -> str:
    """S2：高熵授权码——LIC-YYYY-<8 位随机 hex>（32bit+，防顺序枚举），
    撞库重试 8 次；保留 LIC- 前缀供掩码与识别"""
    import secrets

    for _ in range(8):
        code = f"LIC-{year}-{secrets.token_hex(4).upper()}"
        if db.query(License).filter(License.code == code).first() is None:
            return code
    return f"LIC-{year}-{secrets.token_hex(8).upper()}"


# ---------- 认证 ----------
# S6：登录防爆破——同 账号+IP 15 分钟内失败 10 次 → 429（内存计数，单进程部署足够）
_LOGIN_FAIL_WINDOW = 900
_LOGIN_FAIL_LIMIT = 10
_LOGIN_FAIL_IP_LIMIT = 30  # R4：单 IP 总失败上限（防随机用户名刷键挤掉目标账号计数）
_LOGIN_FAIL_MAX_KEYS = 10000  # N12：键数上限，防乱填用户名撑爆内存
_LOGIN_TRUSTED_TTL = 30 * 86400  # G3：授信标记 30 天有效，过期视为不可信
_SWEEP_INTERVAL = 60  # L2：全量过期清理节流到每 60s 一次，请求路径只做惰性修剪
_login_failures: dict = {}       # key "ip|username" -> [失败时间戳]
_login_ip_failures: dict = {}    # ip -> [(失败时间戳, key)]（L2：IP 聚合计数独立索引，免全字典汇总）
_login_trusted: dict = {}        # L1：授信键 "ip|username" -> 最近成功时间（登录成功过，免 IP 聚合误伤）
_login_lock = threading.Lock()
_login_last_sweep = 0.0


def _login_rate_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{ip}|{username}"


def _login_limits(db) -> tuple:
    """登录限流阈值（settings login_fail_limit / login_fail_ip_limit 可调，测试/防护调优用）"""
    from .wecom import get_setting

    try:
        per_key = max(1, int(get_setting(db, "login_fail_limit", str(_LOGIN_FAIL_LIMIT))))
        per_ip = max(1, int(get_setting(db, "login_fail_ip_limit", str(_LOGIN_FAIL_IP_LIMIT))))
        return per_key, per_ip
    except Exception:
        return _LOGIN_FAIL_LIMIT, _LOGIN_FAIL_IP_LIMIT


def _login_sweep_locked(now: float) -> None:
    """L2：全量清理过期键（节流调用；请求路径不做全字典遍历）"""
    global _login_last_sweep
    if now - _login_last_sweep < _SWEEP_INTERVAL:
        return
    _login_last_sweep = now
    for k in list(_login_failures):
        kept = [t for t in _login_failures[k] if now - t < _LOGIN_FAIL_WINDOW]
        if kept:
            _login_failures[k] = kept
        else:
            _login_failures.pop(k, None)
    for ip in list(_login_ip_failures):
        kept = [(t, k) for t, k in _login_ip_failures[ip] if now - t < _LOGIN_FAIL_WINDOW]
        if kept:
            _login_ip_failures[ip] = kept
        else:
            _login_ip_failures.pop(ip, None)
    # G4：不再 FIFO 淘汰窗口内活跃计数——满额由 _login_too_often 拒新键兜底
    while len(_login_trusted) > _LOGIN_FAIL_MAX_KEYS:
        _login_trusted.pop(next(iter(_login_trusted)), None)


def _login_too_often(key: str, db) -> bool:
    now = time.time()
    ip = key.split("|", 1)[0]
    with _login_lock:
        _login_sweep_locked(now)
        # L2：惰性修剪只清本键/本 IP 的桶，不遍历全字典
        arr = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_FAIL_WINDOW]
        if arr:
            _login_failures[key] = arr
        else:
            _login_failures.pop(key, None)
        per_key, per_ip = _login_limits(db)
        if len(arr) >= per_key:
            return True
        # G4：计数键满额时拒新键（fail-closed），不再 FIFO 淘汰窗口内活跃计数——
        # 防攻击者刷大量随机键把别人的计数挤掉绕过限流
        if key not in _login_failures and len(_login_failures) >= _LOGIN_FAIL_MAX_KEYS:
            return True
        # L1：授信键（该 IP+账号登录成功过）不受 IP 聚合上限影响——
        # 防共用出口 IP 被乱填用户名刷满 30 次后，全公司连正确密码都进不来；
        # 单键 10 次上限仍然生效，不放松对指定账号的爆破防护
        # G3：授信 30 天过期，防长期运行后豁免面无限变大
        if key in _login_trusted:
            if now - _login_trusted[key] < _LOGIN_TRUSTED_TTL:
                return False
            _login_trusted.pop(key, None)
        ip_arr = [(t, k) for t, k in _login_ip_failures.get(ip, []) if now - t < _LOGIN_FAIL_WINDOW]
        if ip_arr:
            _login_ip_failures[ip] = ip_arr
        else:
            _login_ip_failures.pop(ip, None)
        return len(ip_arr) >= per_ip


def _login_record_failure(key: str) -> None:
    now = time.time()
    with _login_lock:
        # G4：满额不收新键（与 too_often 拒新对应），不淘汰活跃计数
        if key not in _login_failures and len(_login_failures) >= _LOGIN_FAIL_MAX_KEYS:
            return
        _login_failures.setdefault(key, []).append(now)
        ip = key.split("|", 1)[0]
        _login_ip_failures.setdefault(ip, []).append((now, key))


def _login_clear_key_locked(key: str) -> None:
    """清单个键的失败记录（成功登录/手动解除共用）——调用方持锁"""
    _login_failures.pop(key, None)
    ip = key.split("|", 1)[0]
    lst = _login_ip_failures.get(ip)
    if lst:
        kept = [(t, k) for t, k in lst if k != key]
        if kept:
            _login_ip_failures[ip] = kept
        else:
            _login_ip_failures.pop(ip, None)


class RateLimitClearIn(BaseModel):
    ip: str = ""
    username: str = ""
    scope: str = "all"  # login | activate | all
    confirm: bool = False  # G2：无筛选全清必须显式确认，防误触/被盗用一把梭


@app.post("/api/auth/rate-limit/clear")
def clear_rate_limit(req: RateLimitClearIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    """L1：手动解除限流（共用出口 IP 被误锁的恢复通道；也可清激活限流）。
    不带 ip/username = 全清（需 confirm=true）；带筛选则只清匹配项。仅 admin 可用。
    G2：每次调用写审计日志（操作人/范围/清除条数），后台可追溯。"""
    from .client_api import clear_activate_failures

    if not req.ip and not req.username and not req.confirm:
        raise HTTPException(status_code=400, detail="全部清空需显式传入 confirm=true 确认")
    cleared = {"login": 0, "activate": 0}
    if req.scope in ("login", "all"):
        with _login_lock:
            for k in list(_login_failures):
                ip, _, uname = k.partition("|")
                if req.ip and ip != req.ip:
                    continue
                if req.username and uname != req.username:
                    continue
                cleared["login"] += len(_login_failures.pop(k, ()))
            for ip in list(_login_ip_failures):
                if req.ip and ip != req.ip:
                    continue
                if req.username:
                    kept = [(t, k) for t, k in _login_ip_failures[ip]
                            if k.partition("|")[2] != req.username]
                    if kept:
                        _login_ip_failures[ip] = kept
                    else:
                        _login_ip_failures.pop(ip, None)
                else:
                    _login_ip_failures.pop(ip, None)
    if req.scope in ("activate", "all"):
        cleared["activate"] = clear_activate_failures(req.ip or None)
    write_log(
        db, user, "解除限流",
        f"{req.scope} ip={req.ip or '全部'} user={req.username or '全部'}",
        f"清除失败记录 login={cleared['login']} 条 activate={cleared['activate']} 条",
    )
    db.commit()
    return {"ok": True, "cleared": cleared}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # I3：限流键与用户查询统一用 strip 后的用户名，防「 admin 」命中计数却查不到用户
    username = (req.username or "").strip()
    key = _login_rate_key(request, username)
    if _login_too_often(key, db):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 15 分钟后再试")
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(req.password, user.password_hash):
        _login_record_failure(key)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    with _login_lock:
        _login_clear_key_locked(key)
        _login_trusted[key] = time.time()  # L1：授信该 IP+账号，后续免 IP 聚合误伤
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


def alloc_code_seq(db: Session, key: str) -> int:
    """T4：编号计数控位分配——设置表行锁递增（SELECT FOR UPDATE），
    与业务插入同一事务、同一 commit 原子生效，消除 count()+1 并发同号撞主键。
    控位行由 seed.ensure_code_seqs 启动时幂等初始化（存量最大编号）。
    populate_existing 必加：否则 SQLAlchemy 身份映射返回旧值，行锁形同虚设（T38 教训）。"""
    row = (
        db.query(Setting)
        .filter(Setting.key == key)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if row is None:
        # 防御路径：控位缺失（未经 seed）时从 1 起——生产入口都过 seed，正常不走这里
        row = Setting(key=key, value="0")
        db.add(row)
        db.flush()
    n = int(row.value or "0") + 1
    row.value = str(n)
    return n


@app.post("/api/hotels", response_model=HotelCreateOut)
def create_hotel(req: HotelCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    if db.query(Hotel).filter(Hotel.code == req.code).first():
        raise HTTPException(status_code=400, detail="客户编号已存在")
    hid = f"h{alloc_code_seq(db, 'seq_hotel'):03d}"
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
def hotel_detail(hotel_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    h = db.query(Hotel).filter(Hotel.id == hotel_id).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    lics = [LicenseOut.from_orm_model(l) for l in db.query(License).filter(License.hotel_name == h.name).all()]
    if user.role not in ("admin", "operator"):
        from .masking import mask_text

        for lo in lics:
            lo.code = mask_text(lo.code)  # N6：授权码=激活凭据，只读角色只见打码
    return {
        "hotel": HotelOut.from_orm_model(h),
        "devices": [DeviceOut.from_orm_model(d) for d in db.query(Device).filter(Device.hotel_id == hotel_id).all()],
        "licenses": lics,
        "alerts": [AlertOut.from_orm_model(a) for a in db.query(Alert).filter(Alert.hotel_name == h.name).all()],
    }


# ---------- 授权 ----------
@app.get("/api/licenses", response_model=list[LicenseOut])
def list_licenses(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = [LicenseOut.from_orm_model(l) for l in db.query(License).order_by(License.id).all()]
    if user.role not in ("admin", "operator"):
        from .masking import mask_text

        for lo in out:
            lo.code = mask_text(lo.code)  # N6：授权码=激活凭据，只读角色只见打码
    return out


@app.post("/api/licenses", response_model=LicenseOut)
def create_license(req: LicenseCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    h = db.query(Hotel).filter(Hotel.id == req.hotelId).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    seq = alloc_code_seq(db, "seq_license")  # T4：计数控位替代 count()+1
    issued = datetime.utcnow()
    expire = issued + timedelta(days=30 * req.months)
    lic = License(
        id=f"ln{seq:04d}", code=new_license_code(db, issued.year), hotel_name=h.name,
        plan=req.plan, type=req.type, issued_at=issued.strftime("%Y-%m-%d"),
        expire_at=expire.strftime("%Y-%m-%d"), status="生效中",
        devices=0, max_devices=PLAN_MAXDEV.get(req.plan, 30),
        # P1-3 模块/配额位（features 空=全开）
        features=__import__("json").dumps([f for f in (req.features or [])]) if req.features else "",
        max_rooms=req.maxRooms or 0,
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


@app.post("/api/licenses/{license_id}/restore")
def restore_license(license_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    """误操作恢复：已吊销授权恢复为生效中（过期授权恢复为已过期），对称设备 E2 恢复"""
    lic = db.query(License).filter(License.id == license_id).first()
    if lic is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    if lic.status != "已吊销":
        raise HTTPException(status_code=409, detail=f"授权状态为「{lic.status}」，无需恢复")
    lic.status = "生效中" if (lic.expire_at or "") >= datetime.utcnow().strftime("%Y-%m-%d") else "已过期"
    write_log(db, user, "恢复授权", lic.hotel_name, f"授权 {lic.code} 恢复为「{lic.status}」（误操作恢复）")
    db.commit()
    return {"ok": True, "status": lic.status}


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


class AlertHandleReq(BaseModel):
    note: str = ""  # 处置备注（可空）


@app.post("/api/alerts/{alert_id}/handle")
def handle_alert(
    alert_id: str,
    req: AlertHandleReq = AlertHandleReq(),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    a.status = "已处置"
    a.handled_by = user.name or user.username
    a.handle_note = (req.note or "").strip()[:250]
    a.handled_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    write_log(db, user, "处置告警", a.rule, f"{a.hotel_name}：{a.detail}｜备注：{a.handle_note or '（无）'}")
    db.commit()
    # 处置结果推企微（路由到该店/集团通道，失败不影响处置）
    try:
        from .wecom import notify_alert_handled

        notify_alert_handled(db, a, a.handled_by, a.handle_note)
    except Exception:
        pass
    return {"ok": True, "status": a.status, "handledBy": a.handled_by, "handledAt": a.handled_at}


def _csv_response(filename: str, rows: list) -> Response:
    """CSV 导出（带 BOM，Excel 直接打开中文不乱码）"""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows:
        w.writerow(row)
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reports/alerts.csv")
def export_alerts_csv(
    status: str = "",
    level: str = "",
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
):
    """告警报表导出（可按状态/等级过滤）"""
    q = db.query(Alert).order_by(Alert.time.desc())
    if status:
        q = q.filter(Alert.status == status)
    if level:
        q = q.filter(Alert.level == level)
    rows = [["等级", "规则", "酒店", "详情", "时间(UTC)", "状态", "处置人", "处置备注", "处置时间(UTC)"]]
    for a in q.limit(5000).all():
        rows.append([a.level, a.rule, a.hotel_name, a.detail, a.time, a.status,
                     getattr(a, "handled_by", "") or "", getattr(a, "handle_note", "") or "",
                     getattr(a, "handled_at", "") or ""])
    return _csv_response(f"alerts_{datetime.utcnow():%Y%m%d}.csv", rows)


@app.get("/api/reports/expiring.csv")
def export_expiring_csv(
    days: int = 90,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
):
    """到期预测报表导出：N 天内到期 + 已过期未续"""
    from datetime import date as _date, timedelta as _td

    today = (datetime.utcnow() + timedelta(hours=8)).date()  # 北京日期
    horizon = (today + _td(days=days)).isoformat()
    rows = [["客户编号", "客户名称", "集团", "套餐", "状态", "到期日", "剩余天数", "设备数", "自动续期"]]
    for h in db.query(Hotel).order_by(Hotel.expire_at).all():
        if not h.expire_at or h.status == "已停用":
            continue
        if h.expire_at <= horizon:
            try:
                left = (_date.fromisoformat(h.expire_at) - today).days
            except Exception:
                left = ""
            rows.append([h.code, h.name, h.group or "", h.plan, h.status, h.expire_at, left,
                         f"{h.devices}/{h.max_devices}", "是" if h.auto_renew else "否"])
    return _csv_response(f"expiring_{days}d_{datetime.utcnow():%Y%m%d}.csv", rows)


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
    # G1：门店数按 Hotel.plan 实时统计（Plan.hotels 是写入时快照，已废弃不作准）
    from sqlalchemy import func

    live = dict(db.query(Hotel.plan, func.count()).group_by(Hotel.plan).all())
    return [
        PlanOut.from_orm_model(p, hotels=live.get(p.name, 0))
        for p in db.query(Plan).order_by(Plan.id).all()
    ]


# ---------- 设置 ----------
class SettingUpdate(BaseModel):
    key: str
    value: str


# S5：敏感配置键（值打码返回，禁止明文出接口）
_SENSITIVE_KEY_MARKS = ("api_key", "secret", "token")


def _mask_setting_value(key: str, value: str) -> str:
    v = value or ""
    if any(m in key for m in _SENSITIVE_KEY_MARKS):
        if not v:
            return ""
        return (v[:2] + "…" + v[-4:]) if len(v) > 8 else "***"
    return v


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    # 下划线前缀为敏感配置（如签名公钥指纹等），不对外返回；S5：含密钥/令牌的键值打码
    return {
        s.key: _mask_setting_value(s.key, s.value)
        for s in db.query(Setting).all()
        if not s.key.startswith("_")
    }


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


def _group_out(db: Session, g, with_key: bool = False) -> dict:
    members = db.query(Hotel).filter(Hotel.group_id == g.id).all()
    out = {
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
    # N1/N6：激活码是凭据，只给 admin/operator 明文（viewer 不可见）
    if with_key:
        out["activationKey"] = g.activation_key
    return out


@app.get("/api/groups")
def list_groups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_group_out(db, g, with_key=user.role in ("admin", "operator")) for g in db.query(Group).order_by(Group.id).all()]


@app.post("/api/groups")
def create_group(req: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    if db.query(Group).filter(Group.name == req.name).first():
        raise HTTPException(status_code=400, detail="集团名称已存在")
    seq = alloc_code_seq(db, "seq_group")  # T4：计数控位替代 count()+1（控位含 GP 码空间，见 ensure_code_seqs）
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
    g.activation_key = f"GPK-{__import__('secrets').token_hex(8)}"  # N1：高熵激活码，与展示编号分离
    write_log(db, user, "创建集团", req.name, f"集团码 {g.code}")
    db.commit()
    return {"ok": True, "id": g.id, "code": g.code, "activationKey": g.activation_key}


@app.get("/api/groups/{group_id}")
def group_detail(group_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="集团不存在")
    members = db.query(Hotel).filter(Hotel.group_id == group_id).order_by(Hotel.id).all()
    return {
        "group": _group_out(db, g, with_key=user.role in ("admin", "operator")),
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


# ---------- 每日定时推送（服务端在后台设置；集团/子酒店在客户端设置） ----------
class PushServerConfigReq(BaseModel):
    enabled: bool
    time: str = "09:00"


class PushRunReq(BaseModel):
    time: str = ""  # 指定触发时刻（HH:MM），用于测试/补推；空=按当前时间
    force: bool = False  # 忽略「今天已推过」（补推/测试用）


@app.get("/api/push/server-config")
def push_server_config_get(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "operator"))):
    from .push_daily import get_push_config
    from .wecom import is_dry_run, ops_webhook

    cfg = get_push_config(db, "server")
    cfg["webhookConfigured"] = bool(ops_webhook(db))
    cfg["dryRun"] = is_dry_run(db)
    return cfg


@app.post("/api/push/server-config")
def push_server_config_set(
    req: PushServerConfigReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    from .push_daily import set_push_config, valid_hhmm

    if not valid_hhmm(req.time):
        raise HTTPException(status_code=400, detail="推送时间格式应为 HH:MM（如 09:00）")
    set_push_config(db, "server", "", enabled=req.enabled, push_time=req.time)
    write_log(db, user, "推送设置", "服务端", f"每日异常汇总 {'开启' if req.enabled else '关闭'}，推送时间 {req.time}")
    db.commit()
    return {"ok": True}


@app.post("/api/push/server-test")
def push_server_test(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    """立即生成并推送服务端异常汇总（不影响每日定时；dry-run 时仅记录）"""
    from .push_daily import _push_one, content_server

    content = content_server(db)
    r = _push_one(db, "server", "", content)
    write_log(db, user, "推送测试", "服务端", f"异常汇总预览 {r.get('sent')}")
    db.commit()
    return {"ok": True, "sent": r.get("sent"), "dryRun": r.get("dry_run"), "preview": content}


@app.post("/api/push/run")
def push_run(
    req: PushRunReq = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    """手动触发一次调度 tick（测试/补推）。body.time 指定触发时刻 HH:MM"""
    from .push_daily import now_bj, run_due_pushes, valid_hhmm

    t = now_bj()
    if req and req.time:
        if not valid_hhmm(req.time):
            raise HTTPException(status_code=400, detail="时间格式应为 HH:MM")
        hh, mm = req.time.strip().split(":")
        t = t.replace(hour=int(hh), minute=int(mm))
    pushed = run_due_pushes(db, now=t, force=bool(req and req.force))
    return {"ok": True, "pushed": pushed}


# ---------- 长连接机器人推送目标（服务端汇报走主动发送，无需群 webhook） ----------
class WsTargetReq(BaseModel):
    chatid: str


@app.get("/api/push/ws-target")
def push_ws_target_get(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "operator"))):
    import json

    from .wecom import get_setting
    from .wecom_ws import ws_status

    last_chat = {}
    try:
        last_chat = json.loads(get_setting(db, "wecom_last_chat", "") or "{}")
    except Exception:
        pass
    return {
        "chatid": get_setting(db, "wecom_ws_push_chatid"),
        "lastChat": last_chat,
        "ws": ws_status(),
    }


@app.post("/api/push/ws-target")
def push_ws_target_set(
    req: WsTargetReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    from .wecom import set_setting

    chatid = (req.chatid or "").strip()
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid 不能为空")
    set_setting(db, "wecom_ws_push_chatid", chatid)
    write_log(db, user, "推送设置", "长连接机器人", f"绑定推送目标会话 {chatid[:8]}***")
    db.commit()
    return {"ok": True}


@app.post("/api/push/ws-test")
def push_ws_test(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "operator"))):
    """向绑定会话真发一条测试消息（不走 dry-run，验证长连接主动发送链路）"""
    from .push_daily import now_bj
    from .wecom import get_setting
    from .wecom_ws import ws_send_markdown

    chatid = get_setting(db, "wecom_ws_push_chatid")
    if not chatid:
        raise HTTPException(status_code=400, detail="尚未绑定推送目标会话（先在群里 @机器人 发条消息，然后捕获绑定）")
    content = f"✅ **推送链路测试**\n服务端每日汇报将发送到此会话。\n（{now_bj().strftime('%Y-%m-%d %H:%M')}）"
    r = ws_send_markdown(chatid, content)
    write_log(db, user, "推送测试", "长连接机器人", f"发送到 {chatid[:8]}*** → {'ok' if r.get('ok') else 'fail ' + (r.get('error') or '')[:40]}")
    db.commit()
    return {"ok": r.get("ok"), "error": r.get("error"), "detail": r.get("detail")}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """健康探活（免认证，供自监控/负载均衡使用）：数据库连通 + 企微长连接状态"""
    from .wecom_ws import ws_status

    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    ws = ws_status()
    ok = db_ok and bool(ws.get("running"))
    return {"status": "ok" if ok else "degraded", "db": db_ok, "wecom_ws": ws}


@app.get("/api/wecom/ws-status")
def wecom_ws_status(_: User = Depends(require_roles("admin", "operator"))):
    from .wecom_ws import ws_status

    return ws_status()


@app.post("/api/wecom/flush-daily")
def wecom_flush_daily(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "operator"))):
    from .wecom import flush_daily

    return flush_daily(db)


@app.post("/api/wecom/simulate")
def wecom_simulate(
    req: WecomSimulateReq,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    """调试入口：模拟企微消息进入指令管道（生产接自建应用回调后走 /api/wecom/callback）"""
    from .wecom import ACK_TEXT, process_command

    slow = {"hit": False}

    def on_slow():
        slow["hit"] = True

    reply = process_command(db, req.text, req.userid, on_slow)
    return {
        "reply": reply,
        "ackSent": slow["hit"],
        "ack": ACK_TEXT if slow["hit"] else "",
    }


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
    # L3：`_` 前缀是内部键（GET 侧隐藏，如 _ed25519_public），写入侧同样拒绝——
    # 防覆盖签名公钥等内部键破坏验签链路
    if (req.key or "").startswith("_"):
        raise HTTPException(status_code=400, detail="内部配置键不允许通过接口写入")
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


# ---------- 大模型调用审计（集团/门店/后台 经服务端网关的每次 LLM 调用） ----------
@app.get("/api/llm-logs")
def list_llm_logs(
    sourceType: str = "",
    scene: str = "",
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from .models import LLMCallLog

    q = db.query(LLMCallLog).order_by(LLMCallLog.created_at.desc())
    if sourceType:
        q = q.filter(LLMCallLog.source_type == sourceType)
    if scene:
        q = q.filter(LLMCallLog.scene == scene)
    return [
        {
            "id": r.id,
            "sourceType": r.source_type,
            "sourceId": r.source_id,
            "sourceName": r.source_name,
            "userId": r.user_id,
            "scene": r.scene,
            "userInput": r.user_input,
            "modelInput": r.model_input,
            "modelOutput": r.model_output,
            "modelName": r.model_name,
            "tokensIn": r.tokens_in,
            "tokensOut": r.tokens_out,
            "latencyMs": r.latency_ms,
            "status": r.status,
            "error": r.error,
            "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        }
        for r in q.limit(max(1, min(limit, 200))).all()
    ]


class LLMSimulateReq(BaseModel):
    sourceType: str = "admin"       # group / hotel / admin
    sourceId: str = "demo"
    sourceName: str = "演示来源"
    userId: str = "admin"
    scene: str = "对话查询"
    userInput: str = "最近哪家酒店风险最高？联系人手机 13812345678"


@app.post("/api/llm-logs/simulate")
def simulate_llm_call(
    req: LLMSimulateReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    """演示/自测：走真实审计网关产生一条 LLM 调用记录（验证留痕与脱敏）"""
    from .llm_audit import llm_call

    prompt = f"请基于授权管理数据回答运营问题：{req.userInput}"
    r = llm_call(
        db,
        source_type=req.sourceType if req.sourceType in ("group", "hotel", "admin") else "admin",
        source_id=req.sourceId,
        source_name=req.sourceName,
        user_id=req.userId,
        scene=req.scene,
        user_input=req.userInput,
        prompt=prompt,
    )
    write_log(db, user, "大模型调用(模拟)", req.sourceName, f"场景：{req.scene}")
    db.commit()
    return {"ok": True, "result": r}


# ---------- 大模型接入（云端 API + 本地 Ollama / llama.cpp，经审计网关调用） ----------
class LLMSlotLocal(BaseModel):
    provider: str = ""          # ollama / llama.cpp / 空
    endpoint: str = ""
    model: str = ""
    fastModel: str = ""         # 可选快模型（短咨询提速，如 qwen2.5:3b）


class LLMSlotCloud(BaseModel):
    endpoint: str = ""          # OpenAI 兼容基址（含 /v1），如 DeepSeek / 腾讯混元
    model: str = ""
    apiKey: str = ""            # 空 = 不修改既有 Key


class LLMConfigUpdate(BaseModel):
    active: str = ""            # local / cloud / 空=未接入
    fallback: bool = True       # 激活槽位失败自动切另一套
    local: LLMSlotLocal = LLMSlotLocal()
    cloud: LLMSlotCloud = LLMSlotCloud()
    # 兼容旧版单槽位参数（映射到本地槽位）
    provider: str = ""
    endpoint: str = ""
    model: str = ""


@app.get("/api/ai/llm-status")
def llm_status(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "operator"))):
    from .llm_backend import llm_config, ping

    return {**llm_config(db), "ping": ping(db)}


@app.post("/api/ai/config")
def llm_config_update(
    req: LLMConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    from .llm_backend import save_config

    # 旧版参数兼容：provider/endpoint/model → 本地槽位
    local = req.local
    if req.provider or req.endpoint or req.model:
        local = LLMSlotLocal(provider=req.provider, endpoint=req.endpoint or req.local.endpoint, model=req.model or req.local.model)
    try:
        save_config(
            db,
            active=req.active or (local.provider and "local") or "",
            fallback=req.fallback,
            local={"provider": local.provider, "endpoint": local.endpoint, "model": local.model,
                   "fastModel": local.fastModel},
            cloud={"endpoint": req.cloud.endpoint, "model": req.cloud.model, "apiKey": req.cloud.apiKey},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    write_log(
        db, user, "配置大模型", req.active or (local.provider and "local") or "未接入",
        f"本地={local.provider or '-'} 云端={'已配Key' if req.cloud.apiKey else ('已配' if req.cloud.endpoint else '-')} fallback={'开' if req.fallback else '关'}",
    )
    db.commit()
    return {"ok": True}


class AlertExplainReq(BaseModel):
    alertId: str = ""
    rule: str = ""
    hotelName: str = ""
    detail: str = ""


@app.post("/api/ai/alert-explain")
def ai_explain_alert(
    req: AlertExplainReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    """告警 AI 解读（真实场景示例）：经审计网关调用本地大模型，全量留痕"""
    from .llm_audit import llm_call
    from .models import Alert

    a = db.query(Alert).filter(Alert.id == req.alertId).first() if req.alertId else None
    rule = a.rule if a else req.rule
    hotel = a.hotel_name if a else req.hotelName
    detail = a.detail if a else req.detail
    if not rule and not detail:
        raise HTTPException(status_code=400, detail="缺少告警内容")
    prompt = (
        "你是酒店授权管理系统的风控助手。请用中文简洁解读下面这条告警：可能原因、风险等级判断、"
        "建议的处置步骤（不超过 5 条要点）。\n"
        f"规则：{rule}\n酒店：{hotel}\n详情：{detail}"
    )
    r = llm_call(
        db,
        source_type="admin",
        source_id=user.username,
        source_name="管理后台",
        user_id=user.username,
        scene="告警解读",
        user_input=f"{rule}：{detail}",
        prompt=prompt,
    )
    return {"ok": True, "explain": r.get("output", ""), "audit": {"status": r["status"], "model": r["model"]}}


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
# ---------- P0-2 续期订单（后台确认/驳回）+ P0-1 设备恢复（E2） ----------
class RenewOrderConfirmReq(BaseModel):
    note: str = ""


class RenewOrderRejectReq(BaseModel):
    reason: str = ""


@app.get("/api/renew-orders")
def list_renew_orders(
    status: str = "",
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
):
    from .models import RenewOrder

    q = db.query(RenewOrder).order_by(RenewOrder.id.desc())
    if status:
        q = q.filter(RenewOrder.status == status)
    return [
        {
            "id": o.id,
            "orderNo": o.order_no,
            "targetType": o.target_type,
            "targetId": o.target_id,
            "targetIds": o.target_ids,
            "targetName": o.target_name,
            "plan": o.plan,
            "months": o.months,
            "amount": o.amount,
            "channel": o.channel,
            "status": o.status,
            "proofNo": o.proof_no,
            "proofNote": o.proof_note,
            "proofFile": o.proof_file,
            "confirmedBy": o.confirmed_by,
            "rejectReason": o.reject_reason,
            "createdAt": o.created_at,
            "paidAt": o.paid_at,
        }
        for o in q.limit(200).all()
    ]


@app.post("/api/renew-orders/{order_id}/confirm")
def confirm_renew_order(
    order_id: int,
    req: RenewOrderConfirmReq = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    """确认到账并生效：目标门店（target_ids）续期 months 个月（自 max(今天,原到期日) 顺延）"""
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from .models import RenewOrder

    o = db.query(RenewOrder).filter(RenewOrder.id == order_id).first()
    if o is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if o.status not in ("待确认", "待支付", "已支付"):
        raise HTTPException(status_code=409, detail=f"订单状态为「{o.status}」，不可确认生效")

    ids = [x for x in (o.target_ids or "").split(",") if x]
    today = _dt.utcnow().date()
    renewed = []
    for h in db.query(Hotel).filter(Hotel.id.in_(ids or [""])).all():
        try:
            old = _dt.strptime(h.expire_at, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            old = today
        base = old if old > today else today
        new_expire = (base + _td(days=30 * o.months)).strftime("%Y-%m-%d")
        h.expire_at = new_expire
        if h.status in ("即将到期", "已过期"):
            h.status = "正常"
        # 同步本店生效中授权的到期日
        from .client_api import find_active_license

        lic = find_active_license(db, h)
        if lic is not None and lic.status != "已吊销":
            lic.expire_at = new_expire
            if o.plan:
                lic.plan = o.plan
        renewed.append(f"{h.name}→{new_expire}")

    o.status = "已生效"
    o.paid_at = o.paid_at or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    o.confirmed_by = user.username
    write_log(db, user, "续期订单确认", o.order_no, f"{o.months} 个月 · ¥{o.amount:.2f} · {'；'.join(renewed) or '无门店'}")
    db.commit()
    return {"ok": True, "renewed": renewed}


@app.post("/api/renew-orders/{order_id}/reject")
def reject_renew_order(
    order_id: int,
    req: RenewOrderRejectReq = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    from .models import RenewOrder

    o = db.query(RenewOrder).filter(RenewOrder.id == order_id).first()
    if o is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if o.status not in ("待确认", "待支付"):
        raise HTTPException(status_code=409, detail=f"订单状态为「{o.status}」，不可驳回")
    o.status = "已驳回"
    o.reject_reason = (req.reason if req else "") or "凭证未核实"
    write_log(db, user, "续期订单驳回", o.order_no, o.reject_reason)
    db.commit()
    return {"ok": True}


@app.post("/api/hotels/{hotel_id}/devices/{device_id}/restore")
def restore_device(
    hotel_id: str,
    device_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    """误操作恢复（E2）：把「已解绑/已迁移」设备恢复为离线状态并归还配额"""
    d = (
        db.query(Device)
        .filter(Device.id == device_id, Device.hotel_id == hotel_id)
        .first()
    )
    if d is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    if d.status not in ("已解绑", "已迁移"):
        raise HTTPException(status_code=409, detail=f"设备状态为「{d.status}」，无需恢复")
    h = db.query(Hotel).filter(Hotel.id == hotel_id).first()
    d.status = "离线"
    d.unbound_at = ""
    d.unbound_by = ""
    if h is not None:
        h.devices = (h.devices or 0) + 1
    write_log(db, user, "恢复设备", f"{h.name if h else hotel_id}/{d.name}", f"设备 {d.id} 恢复绑定")
    db.commit()
    return {"ok": True}


# ---------- P2-1 授权盘点 + P2-2 续期流失预测 ----------
@app.get("/api/renewal-risks")
def renewal_risks(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
):
    """流失风险排行（低/中/高/紧急 + 因子），续期工作台按此排序置顶高风险"""
    from .renewal_risk import ranked_risks

    return ranked_risks(db)


@app.get("/api/renewal-audit")
def renewal_audit(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "operator")),
):
    """P2-1 授权盘点：闲置/半闲置/超装嫌疑/空壳门店（全部既有数据聚合）"""
    from .renewal_risk import _active_ratio, _idle_days

    hotels = db.query(Hotel).filter(Hotel.status != "已停用").all()
    idle_list, underused_list, empty_list = [], [], []
    for h in hotels:
        active_devices = (
            db.query(Device)
            .filter(Device.hotel_id == h.id, Device.status.in_(["在线", "离线"]))
            .count()
        )
        if active_devices == 0:
            empty_list.append({"hotel": h.name, "code": h.code, "group": h.group})
            continue
        idle = _idle_days(db, h)
        if idle >= 30:
            idle_list.append({"hotel": h.name, "code": h.code, "idleDays": idle, "expireAt": h.expire_at})
        ratio = _active_ratio(db, h)
        if ratio < 0.3:
            underused_list.append(
                {"hotel": h.name, "code": h.code, "activeRatio": round(ratio, 2), "devices": h.devices}
            )
    # 超装嫌疑：未处置的共享/激活速度/并发激活告警
    suspect_rows = (
        db.query(Alert)
        .filter(
            Alert.rule.in_(["License 共享检测", "激活速度异常", "多设备并发激活", "频繁自助换机"]),
            Alert.status != "已处置",
        )
        .all()
    )
    suspects = {}
    for a in suspect_rows:
        suspects.setdefault(a.hotel_name, []).append({"rule": a.rule, "detail": a.detail, "level": a.level})
    return {
        "summary": {
            "total": len(hotels),
            "idle": len(idle_list),
            "underused": len(underused_list),
            "suspect": len(suspects),
            "empty": len(empty_list),
        },
        "idle": idle_list[:50],
        "underused": underused_list[:50],
        "suspects": [{"hotel": k, "alerts": v} for k, v in list(suspects.items())[:50]],
        "empty": empty_list[:50],
    }


class RenewScriptReq(BaseModel):
    hotelId: str


@app.post("/api/ai/renew-script")
def ai_renew_script(
    req: RenewScriptReq,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "operator")),
):
    """P2-2 续期跟进话术生成：数据接地（数字来自 risk_score），模型只管表述；失败降级模板"""
    from .llm_audit import llm_call
    from .renewal_risk import risk_score

    h = db.query(Hotel).filter(Hotel.id == req.hotelId).first()
    if h is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    r = risk_score(db, h)
    facts = (
        f"酒店 {h.name}（{h.code}，套餐 {h.plan}），到期日 {h.expire_at}（剩余 {r['days_left']} 天），"
        f"近 30 天设备使用率 {r['active_ratio']:.0%}，最近心跳距今 {r['idle_days']} 天，"
        f"风险档 {r['risk']}，因子：{'；'.join(r['factors']) or '无'}。"
    )
    template = (
        f"{h.name} 您好，贵店授权将于 {h.expire_at} 到期（剩余 {r['days_left']} 天）。"
        f"为避免影响前台正常使用，建议尽快办理续期。"
        + ("近期贵店使用活跃，续期后可无缝衔接。" if r["active_ratio"] >= 0.5 else "如近期使用较少，也可沟通调整套餐。")
        + "需要我发送续期办理方式吗？"
    )
    out = template
    audit_status = "template"
    model = "template"
    try:
        res = llm_call(
            db,
            source_type="admin",
            source_id=user.username,
            source_name="管理后台",
            user_id=user.username,
            scene="续期话术",
            user_input=facts,
            prompt=(
                "你是酒店授权管理系统的续期顾问。基于以下事实数据写一段 80~120 字的企微续期跟进话术，"
                "语气礼貌、给出明确行动建议；严禁编造数据之外的数字或承诺。\n" + facts
            ),
        )
        if res.get("status") == "ok" and res.get("output"):
            out = res["output"].strip()
            audit_status = "ok"
            model = res.get("model", "")
    except Exception:
        pass  # 模型失败降级模板（数据仍真实）
    return {"ok": True, "script": out, "facts": facts, "audit": {"status": audit_status, "model": model}}


static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    index_file = os.path.join(static_dir, "index.html")

    # SPA history 模式兜底：非 /api 路径一律回 index.html（直接访问/刷新子路由不 404）
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # H1：realpath 包含校验——URL 解码出的 ../ 逃逸一律回落 index.html，
        # 防未认证任意文件读取（直连后端实测曾可读 /etc/* 与部署密钥）
        base = os.path.realpath(static_dir)
        candidate = os.path.realpath(os.path.join(static_dir, full_path))
        if full_path and candidate.startswith(base + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_file)

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
