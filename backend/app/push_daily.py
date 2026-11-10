"""每日定时推送（v1.2）—— 服务端异常汇总 / 集团子酒店日报 / 子酒店本店日报

三种推送范围与配置入口：
- 服务端：推「异常酒店汇总」到运营群（后台「系统设置」配置开关与推送时间）
- 集团：推「所有子酒店营业信息」到本集团群（集团总端客户端配置）
- 子酒店：推「本店营业信息」到本店群（门店客户端配置）

配置存 settings 表（键值），无需迁移：
  push_server_enabled / push_server_time / push_server_last
  push_group_{gid}_enabled / push_group_{gid}_time / push_group_{gid}_last
  push_hotel_{hid}_enabled / push_hotel_{hid}_time / push_hotel_{hid}_last

调度：main.py 启动后台线程，每 30 秒 tick 一次 run_due_pushes，
「开关开 且 当前北京时间 HH:MM 等于设置时间 且 今天未推过」才推送（_last 防重）。
所有内容一律数据库接地生成，绝不编造。
"""
from datetime import datetime, timedelta, date

from sqlalchemy.orm import Session

from .models import Alert, Group, Hotel
from .wecom import _today_bj, get_setting, set_setting, push_message


# ---------- 时间与配置读写 ----------

def now_bj() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def valid_hhmm(v: str) -> bool:
    try:
        hh, mm = (v or "").strip().split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:
        return False


def norm_hhmm(v: str) -> str:
    hh, mm = (v or "").strip().split(":")
    return f"{int(hh):02d}:{int(mm):02d}"


def _enabled(db: Session, prefix: str, default: str = "off") -> bool:
    return get_setting(db, f"{prefix}_enabled", default) == "on"


def _time(db: Session, prefix: str, default: str = "09:00") -> str:
    t = get_setting(db, f"{prefix}_time", default)
    return norm_hhmm(t) if valid_hhmm(t) else default


def _last(db: Session, prefix: str) -> str:
    return get_setting(db, f"{prefix}_last", "")


def _mark_pushed(db: Session, prefix: str, day: str) -> None:
    set_setting(db, f"{prefix}_last", day)


def get_push_config(db: Session, scope: str, entity_id: str = "") -> dict:
    """读取某范围推送配置（client / admin 通用）"""
    prefix = {"server": "push_server", "group": f"push_group_{entity_id}", "hotel": f"push_hotel_{entity_id}"}[scope]
    return {
        "scope": scope,
        "entityId": entity_id,
        "enabled": _enabled(db, prefix),
        "time": _time(db, prefix),
        "lastPushDate": _last(db, prefix),
    }


def set_push_config(db: Session, scope: str, entity_id: str, *, enabled: bool, push_time: str) -> None:
    prefix = {"server": "push_server", "group": f"push_group_{entity_id}", "hotel": f"push_hotel_{entity_id}"}[scope]
    set_setting(db, f"{prefix}_enabled", "on" if enabled else "off")
    set_setting(db, f"{prefix}_time", norm_hhmm(push_time))


# ---------- 内容生成（全部数据库接地） ----------

def _unhandled_alert_count(db: Session, hotel_name: str) -> int:
    return db.query(Alert).filter(Alert.hotel_name == hotel_name, Alert.status != "已处置").count()


def _hotel_line(h: Hotel, un_alerts: int) -> str:
    return (
        f"- {h.name}（{h.code}）{h.status}｜{h.plan}｜到期 {h.expire_at}"
        f"｜设备 {h.devices or 0}/{h.max_devices or 0}｜未处置告警 {un_alerts} 条"
    )


def content_server(db: Session) -> str:
    """服务端异常酒店汇总：过期未续 / 30 天内到期 / 有未处置告警 / 设备满负荷"""
    today = _today_bj()
    hotels = [h for h in db.query(Hotel).all() if h.status != "已停用"]

    def days_left(h: Hotel):
        try:
            return (date.fromisoformat(h.expire_at) - today).days
        except Exception:
            return None

    overdue = [(h, d) for h in hotels if (d := days_left(h)) is not None and d < 0]
    soon = sorted(
        [(days_left(h), h) for h in hotels if days_left(h) is not None and 0 <= days_left(h) <= 30],
        key=lambda x: x[0],
    )
    alerted = []
    for h in hotels:
        n = _unhandled_alert_count(db, h.name)
        if n > 0:
            alerted.append((n, h))
    alerted.sort(key=lambda x: -x[0])
    full = [h for h in hotels if (h.max_devices or 0) > 0 and (h.devices or 0) >= h.max_devices]

    lines = [f"🌅 **服务端每日巡检**（{today.isoformat()} {now_bj().strftime('%H:%M')}）"]
    if not (overdue or soon or alerted or full):
        lines.append("今日无异常酒店 ✅")
        return "\n".join(lines)

    lines.append(f"异常酒店共 {len(set([h.code for h, _ in overdue] + [h.code for _, h in soon] + [h.code for _, h in alerted] + [h.code for h in full]))} 家：")
    if overdue:
        lines.append(f"【已过期未续】{len(overdue)} 家：")
        lines += [f"- {h.name}（{h.code}）到期 {h.expire_at}（已过期 {-d} 天）" for h, d in overdue[:15]]
    if soon:
        lines.append(f"【30 天内到期】{len(soon)} 家：")
        lines += [f"- {h.name}（{h.code}）到期 {h.expire_at}（剩余 {d} 天）" for d, h in soon[:15]]
    if alerted:
        lines.append(f"【未处置告警】{len(alerted)} 家：")
        lines += [f"- {h.name}（{h.code}）{n} 条" for n, h in alerted[:15]]
    if full:
        lines.append(f"【设备满负荷】{len(full)} 家：")
        lines += [f"- {h.name}（{h.code}）{h.devices}/{h.max_devices}" for h in full[:15]]
    lines.append("（处置请到后台「风控中心」）")
    return "\n".join(lines)


def content_group(db: Session, group_id: str) -> str:
    """集团日报：所有子酒店营业信息（每店一行，全部列出）"""
    g = db.query(Group).filter(Group.id == group_id).first()
    gname = g.name if g else group_id
    hotels = sorted(
        (h for h in db.query(Hotel).filter(Hotel.group_id == group_id).all() if h.status != "已停用"),
        key=lambda x: x.code or "",
    )
    today = _today_bj()
    lines = [f"🌅 **{gname} 集团日报**（{today.isoformat()} {now_bj().strftime('%H:%M')}）"]
    if not hotels:
        lines.append("暂无在营子酒店")
        return "\n".join(lines)
    by_status: dict = {}
    for h in hotels:
        by_status[h.status] = by_status.get(h.status, 0) + 1
    lines.append(
        f"子酒店共 {len(hotels)} 家："
        + "、".join(f"{k} {v} 家" for k, v in by_status.items())
    )
    for h in hotels:
        lines.append(_hotel_line(h, _unhandled_alert_count(db, h.name)))
    lines.append("（明细请到集团总端查看）")
    return "\n".join(lines)


def content_hotel(db: Session, hotel_id: str) -> str:
    """子酒店日报：本店营业信息"""
    h = db.query(Hotel).filter(Hotel.id == hotel_id).first()
    if h is None:
        return ""
    today = _today_bj()
    try:
        left = (date.fromisoformat(h.expire_at) - today).days
        left_txt = f"（剩余 {left} 天）" if left >= 0 else f"（已过期 {-left} 天）"
    except Exception:
        left_txt = ""
    n = _unhandled_alert_count(db, h.name)
    lines = [
        f"🌅 **{h.name}（{h.code}）每日经营简报**（{today.isoformat()} {now_bj().strftime('%H:%M')}）",
        f"- 授权状态：{h.status}｜套餐：{h.plan}",
        f"- 到期：{h.expire_at}{left_txt}",
        f"- 设备占用：{h.devices or 0}/{h.max_devices or 0}",
        f"- 最近活跃：{h.last_active or '暂无记录'}",
        f"- 未处置告警：{n} 条",
    ]
    if n > 0:
        rows = (
            db.query(Alert)
            .filter(Alert.hotel_name == h.name, Alert.status != "已处置")
            .order_by(Alert.time.desc())
            .limit(3)
            .all()
        )
        for a in rows:
            lines.append(f"  · [{a.level}] {a.rule}（{a.status}）")
    return "\n".join(lines)


# ---------- 调度 ----------

def _push_one(db: Session, scope: str, entity_id: str, content: str) -> dict:
    if scope == "server":
        return push_message(db, content)
    if scope == "group":
        return push_message(db, content, group_id=entity_id)
    return push_message(db, content, hotel_id=entity_id)


def run_due_pushes(db: Session, now: datetime = None, force: bool = False) -> list:
    """扫描全部推送配置：到点且今天未推 → 推送并记录。返回已推送清单（审计用）。
    now 参数供测试注入指定时刻（默认当前北京时间）；
    force=True 忽略「今天已推过」（补推/测试用），推送后仍会记录今天已推。"""
    t = now or now_bj()
    hhmm = t.strftime("%H:%M")
    day = t.strftime("%Y-%m-%d")
    pushed = []

    scopes = [("server", "push_server", ""), ("risk_weekly", "push_risk_weekly", "")]
    for g in db.query(Group).all():
        scopes.append(("group", f"push_group_{g.id}", g.id))
    for h in db.query(Hotel).filter(Hotel.status != "已停用").all():
        scopes.append(("hotel", f"push_hotel_{h.id}", h.id))

    for scope, prefix, eid in scopes:
        if not _enabled(db, prefix, default="on" if scope == "risk_weekly" else "off"):
            continue
        # 周度流失周报仅周一到点（force 可任意日补推）
        if scope == "risk_weekly" and not force and t.weekday() != 0:
            continue
        if _time(db, prefix) != hhmm or (not force and _last(db, prefix) == day):
            continue
        if scope == "server":
            content = content_server(db)
        elif scope == "risk_weekly":
            content = content_risk_weekly(db)
        elif scope == "group":
            content = content_group(db, eid)
        else:
            content = content_hotel(db, eid)
        if not content:
            continue
        r = _push_one(db, scope, eid, content)
        _mark_pushed(db, prefix, day)
        pushed.append({
            "scope": scope,
            "entityId": eid,
            "time": hhmm,
            "sent": r.get("sent", []),
            "dryRun": r.get("dry_run", False),
            "chars": len(content),
        })
    db.commit()
    return pushed


def push_scheduler_loop(interval: int = 30) -> None:
    """后台调度线程主体：每 interval 秒 tick 一次（main.py lifespan 启动）"""
    import time as _time
    from .database import SessionLocal

    while True:
        try:
            db = SessionLocal()
            try:
                run_due_pushes(db)
            finally:
                db.close()
        except Exception:
            pass
        _time.sleep(interval)


def content_risk_weekly(db: Session) -> str:
    """P2-2 周度流失预警（每周一推服务商）：高/紧急风险门店清单（数据直出）"""
    from .renewal_risk import ranked_risks

    rows = [r for r in ranked_risks(db) if r["risk"] in ("高", "紧急")]
    if not rows:
        return ""
    lines = [f"**📋 续期流失周报**（{_today_bj()}）", f"高/紧急风险门店 **{len(rows)}** 家：", ""]
    for r in rows[:15]:
        marks = "；".join(r["factors"]) or "-"
        lines.append(f"- **{r['hotel_name']}**（{r['code']}）[{r['risk']}] 到期 {r['expire_at']}｜{marks}")
    if len(rows) > 15:
        lines.append(f"- …另 {len(rows) - 15} 家见后台「到期续期工作台」")
    lines.append("")
    lines.append("建议本周内优先跟进高风险门店续期。")
    return "\n".join(lines)
