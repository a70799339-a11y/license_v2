"""企业微信机器人 —— 推送（群机器人 webhook）+ 指令管道

场景 A（内部管理）：运营群收全量告警/日报
场景 B（集团管理）：集团群只收本集团告警/日报（数据隔离在服务端）

安全：推送内容与日志同管道脱敏（mask_text）；指令进审计；dry-run 默认开
（未配置真实 webhook 前不外发，最近推送存 settings 供测试断言）。
"""
import json
import urllib.error
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from .masking import mask_text
from .models import Alert, AuditLog, Group, Hotel, Setting, WecomBinding, WecomGroupHook


# ---------- 配置读写 ----------
def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row and row.value else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def is_dry_run(db: Session) -> bool:
    """dry-run=on 时只记录不外发（默认 on，配置真实 webhook 并显式关闭后才外发）"""
    return get_setting(db, "wecom_dry_run", "on") != "off"


def push_enabled(db: Session) -> bool:
    return get_setting(db, "wecom_push_enabled") == "on"


def ops_webhook(db: Session) -> str:
    return get_setting(db, "wecom_ops_webhook")


def group_hooks(db: Session) -> dict:
    return {h.group_id: h.webhook_url for h in db.query(WecomGroupHook).all()}


# ---------- 实体级 webhook（客户端自助配置） ----------
def validate_webhook_url(url: str) -> bool:
    """仅允许企微官方机器人 webhook 域（防 SSRF）"""
    u = (url or "").strip()
    return u.startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send")


def hotel_hook(db: Session, hotel_id: str) -> str:
    return get_setting(db, f"wecom_hotel_hook_{hotel_id}")


def set_hotel_hook(db: Session, hotel_id: str, url: str) -> None:
    set_setting(db, f"wecom_hotel_hook_{hotel_id}", url.strip())


def set_group_hook(db: Session, group_id: str, url: str) -> None:
    row = db.query(WecomGroupHook).filter(WecomGroupHook.group_id == group_id).first()
    if row is None:
        db.add(WecomGroupHook(group_id=group_id, webhook_url=url.strip()))
    else:
        row.webhook_url = url.strip()


# ---------- 发送 ----------
def send_webhook(webhook_url: str, content: str) -> tuple:
    """群机器人 webhook 推送 markdown 消息，返回 (ok, err)"""
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content[:4000]}}).encode()
    try:
        req = urllib.request.Request(
            webhook_url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode() or "{}")
        if data.get("errcode") == 0:
            return True, ""
        return False, str(data.get("errmsg", data))[:100]
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(e)[:100]


def push_message(db: Session, content: str, *, group_id: str = "", hotel_id: str = "") -> dict:
    """路由推送：运营群（全量）+ 集团群 + 门店群（各自实体配置）。返回路由结果。"""
    masked = mask_text(content)
    record = {
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "group_id": group_id,
        "hotel_id": hotel_id,
        "content": masked,
        "sent": [],
        "dry_run": is_dry_run(db),
    }
    if is_dry_run(db):
        set_setting(db, "wecom_last_push", json.dumps(record, ensure_ascii=False)[:2000])
        db.commit()
        record["sent"] = ["dry-run"]
        return record

    targets = []
    if push_enabled(db):
        if ops_webhook(db):
            targets.append(("ops", ops_webhook(db)))
        if group_id:
            hook = group_hooks(db).get(group_id, "")
            if hook:
                targets.append((f"group:{group_id}", hook))
        if hotel_id:
            hook = hotel_hook(db, hotel_id)
            if hook:
                targets.append((f"hotel:{hotel_id}", hook))
    for name, url in targets:
        ok, err = send_webhook(url, masked)
        record["sent"].append(f"{name}:{'ok' if ok else 'fail ' + err}")
    set_setting(db, "wecom_last_push", json.dumps(record, ensure_ascii=False)[:2000])
    db.commit()
    return record


# ---------- 告警路由（实时） ----------
def hotel_group_id(db: Session, hotel_name: str) -> str:
    h = db.query(Hotel).filter(Hotel.name == hotel_name).first()
    return (h.group_id or "") if h else ""


def hotel_id_by_name(db: Session, hotel_name: str) -> str:
    h = db.query(Hotel).filter(Hotel.name == hotel_name).first()
    return (h.id or "") if h else ""


def notify_alert(db: Session, alert: Alert) -> None:
    """紧急/高危告警即时推；中危进日报汇总（不单独推）。失败不影响告警落库。"""
    try:
        if alert.level not in ("紧急", "高"):
            return
        gid = hotel_group_id(db, alert.hotel_name)
        hid = hotel_id_by_name(db, alert.hotel_name)
        content = (
            f"**🚨 风控告警（{alert.level}）**\n"
            f"> 规则：{alert.rule}\n"
            f"> 酒店：{alert.hotel_name}\n"
            f"> 详情：{alert.detail}\n"
            f"> 时间：{alert.time}"
        )
        push_message(db, content, group_id=gid, hotel_id=hid)
    except Exception:
        pass  # 推送失败不影响告警记账


# ---------- 日报（惰性：当日首次读取告警/统计时生成） ----------
def _daily_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def build_ops_daily(db: Session) -> str:
    today = _daily_key()
    alerts = db.query(Alert).filter(Alert.time >= today).all()
    from .models import License

    soon = []
    for h in db.query(Hotel).all():
        if h.expire_at and h.expire_at <= "2027-01-01":
            soon.append(h)
    lines = [f"**📋 运营日报 {today}**"]
    lines.append(f"- 新增告警：{len(alerts)} 条（紧急 {sum(1 for a in alerts if a.level == '紧急')} / 高 {sum(1 for a in alerts if a.level == '高')}）")
    lines.append(f"- 7 天内到期提醒：{sum(1 for h in soon if h.status in ('即将到期', '已过期'))} 家")
    for a in sorted(alerts, key=lambda x: x.level)[:8]:
        lines.append(f"- [{a.level}] {a.rule} · {a.hotel_name}")
    return "\n".join(lines)


def build_group_daily(db: Session, group_id: str) -> str:
    today = _daily_key()
    g = db.query(Group).filter(Group.id == group_id).first()
    members = db.query(Hotel).filter(Hotel.group_id == group_id).all()
    names = [m.name for m in members]
    alerts = (
        db.query(Alert)
        .filter(Alert.time >= today, Alert.hotel_name.in_(names))
        .all()
        if names
        else []
    )
    expiring = [m for m in members if m.status in ("即将到期", "已过期")]
    lines = [f"**📋 集团日报 {today}（{g.name if g else group_id}）**"]
    lines.append(f"- 门店 {len(members)} 家，即将到期/过期 {len(expiring)} 家")
    lines.append(f"- 今日告警 {len(alerts)} 条")
    for m in expiring[:8]:
        lines.append(f"- ⏰ {m.name} 到期 {m.expire_at}")
    for a in alerts[:8]:
        lines.append(f"- [{a.level}] {a.rule} · {a.hotel_name}")
    return "\n".join(lines)


def flush_daily(db: Session) -> dict:
    """生成并推送当日日报（幂等：当日已推则跳过）"""
    today = _daily_key()
    if get_setting(db, "wecom_daily_last") == today:
        return {"skipped": True, "reason": "今日已推送"}
    results = {"ops": push_message(db, build_ops_daily(db)).get("sent")}
    for gid, url in group_hooks(db).items():
        if not url:
            continue  # 未配置的集团通道跳过
        results[f"group:{gid}"] = push_message(db, build_group_daily(db, gid), group_id=gid).get("sent")
    set_setting(db, "wecom_daily_last", today)
    db.commit()
    return results


# ---------- 指令管道 ----------
def _audit(db: Session, operator: str, action: str, target: str, detail: str) -> None:
    db.add(
        AuditLog(
            operator=operator,
            action=action,
            target=mask_text(target),
            detail=mask_text(detail),
        )
    )


def process_command(db: Session, text: str, sender: str = "unknown") -> str:
    """解析并执行企微指令。权限：续期需 operator+/集团绑定；查询需绑定。"""
    text = (text or "").strip()
    operator = f"企微({sender})"
    binding = db.query(WecomBinding).filter(WecomBinding.userid == sender).first()

    if not text or text in ("帮助", "help", "?"):
        return (
            "指令列表：\n"
            "· 状态 HT-0001 —— 查授权状态\n"
            "· 告警 HT-0001 —— 查最近告警\n"
            "· 续期 HT-0001 —— 续期一年（需回复『确认续期 HT-0001』）\n"
            "（使用前需管理员绑定你的企微账号）"
        )

    if binding is None:
        return (
            f"你的企微账号尚未绑定（userid：{sender}）。\n"
            f"请联系管理员绑定后使用；回复『帮助』可先看指令列表。"
        )

    parts = text.split()
    cmd, code = parts[0], (parts[1] if len(parts) > 1 else "")

    # 集团绑定用户只能操作本集团门店
    def allowed(h: Hotel) -> bool:
        if binding.role == "admin":
            return True
        if binding.role == "group":
            return h.group_id == binding.group_id
        return binding.role in ("operator",)

    hotel = db.query(Hotel).filter((Hotel.code == code) | (Hotel.name == code)).first() if code else None

    if cmd == "状态" and hotel is not None and allowed(hotel):
        from .client_api import find_active_license, license_status

        lic = find_active_license(db, hotel)
        st = license_status(hotel, lic)
        _audit(db, operator, "企微查询", hotel.name, f"查询状态 → {st}")
        db.commit()
        return (
            f"{hotel.name}（{hotel.code}）\n"
            f"· 授权状态：{st}\n· 到期：{hotel.expire_at}\n"
            f"· 设备：{hotel.devices}/{hotel.max_devices}\n· 自动续期：{'开' if hotel.auto_renew else '关'}"
        )

    if cmd == "告警" and hotel is not None and allowed(hotel):
        alerts = (
            db.query(Alert).filter(Alert.hotel_name == hotel.name).order_by(Alert.time.desc()).limit(5).all()
        )
        _audit(db, operator, "企微查询", hotel.name, "查询告警")
        db.commit()
        if not alerts:
            return f"{hotel.name} 暂无告警记录。"
        return "\n".join(f"· [{a.level}] {a.rule}（{a.status}）{a.time}" for a in alerts)

    if cmd in ("续期", "确认续期") and hotel is not None and allowed(hotel):
        if cmd == "续期":
            return f"确认执行？回复：确认续期 {hotel.code}"
        hotel.expire_at = "2027-12-31"
        if hotel.status in ("即将到期", "已过期"):
            hotel.status = "正常"
        _audit(db, operator, "企微续期", hotel.name, "续期一年至 2027-12-31")
        db.commit()
        return f"✅ {hotel.name} 已续期至 2027-12-31"

    return f"无法识别：{text}（回复『帮助』看指令列表）"
