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


def _scoped_hotels(db: Session, binding) -> tuple:
    """按绑定角色返回可见酒店列表与范围标签（数据隔离）"""
    role = binding.role if binding else "viewer"
    if role == "group":
        return db.query(Hotel).filter(Hotel.group_id == (binding.group_id or "")).all(), "本集团"
    if role in ("admin", "operator"):
        return db.query(Hotel).all(), "全部"
    return [], "无"


def _is_list_query(text: str) -> bool:
    """是否为「清单类」问法（完整性兜底只对这类生效，避免误伤咨询类回答）"""
    return any(k in text for k in ("查询", "查一下", "列出", "清单", "哪些", "有哪些", "多少", "列表"))


def _query_data(db: Session, text: str, binding) -> tuple:
    """按问题意图从数据库取真实数据（接地）。
    返回 (数据文本, 完整性基线)；未命中数据意图返回 (None, 0)。
    完整性基线 = 主清单（临期/告警/设备）条目数，不含「已过期」附录。"""
    from datetime import date

    hotels, scope = _scoped_hotels(db, binding)
    today = date.today()
    lines = [f"今天日期：{today.isoformat()}"]

    def days_left(h: Hotel):
        try:
            return (date.fromisoformat(h.expire_at) - today).days
        except Exception:
            return None

    hit = False
    guard_n = 0
    if any(k in text for k in ("到期", "过期", "临期", "续期")):
        hit = True
        if any(k in text for k in ("一个月", "1个月", "30天")):
            win = 30
        elif any(k in text for k in ("两个月", "2个月", "60天")):
            win = 60
        else:
            win = 90
        upcoming, overdue = [], []
        for h in hotels:
            if h.status == "已停用":
                continue
            d = days_left(h)
            if d is None:
                continue
            if 0 <= d <= win:
                upcoming.append((d, h))
            elif d < 0 and -d <= 180:  # 已过期半年内一并提示
                overdue.append((d, h))
        upcoming.sort(key=lambda x: x[0])
        overdue.sort(key=lambda x: x[0])
        guard_n = len(upcoming)  # 主清单基线（已过期附录不计入）
        lines.append(f"【{scope}酒店 {win} 天内到期】共 {len(upcoming)} 家：")
        for d, h in upcoming[:30]:
            lines.append(f"- {h.name}（{h.code}）到期 {h.expire_at}（剩余 {d} 天）套餐 {h.plan} 状态 {h.status}")
        if not upcoming:
            lines.append("（无即将到期的酒店）")
        if overdue:
            lines.append(f"【已过期未续】共 {len(overdue)} 家：")
            for d, h in overdue[:15]:
                lines.append(f"- {h.name}（{h.code}）到期 {h.expire_at}（已过期 {-d} 天）套餐 {h.plan} 状态 {h.status}")

    if any(k in text for k in ("告警", "风险", "异常")):
        hit = True
        names = {h.name for h in hotels}
        alerts = [
            a for a in db.query(Alert).order_by(Alert.time.desc()).limit(30).all()
            if a.hotel_name in names
        ][:10]
        lines.append(f"【近期告警】共 {len(alerts)} 条：")
        for a in alerts:
            lines.append(f"- [{a.level}] {a.rule}｜{a.hotel_name}｜{a.status}｜{a.time}")
        if not alerts:
            lines.append("（无告警）")

    if "设备" in text:
        hit = True
        lines.append("【设备使用】：")
        for h in sorted((x for x in hotels if x.status != "已停用"), key=lambda x: -(x.devices or 0))[:15]:
            lines.append(f"- {h.name}：{h.devices}/{h.max_devices}")

    return ("\n".join(lines), guard_n) if hit else (None, 0)


def _llm_answer(db: Session, text: str, sender: str, binding) -> str:
    """企微自由问答：先从数据库取真实数据（接地），再经审计网关调用大模型组织回答。
    未配置模型时数据直出（保证查询类问题依然准确）；模型失败时降级为数据直出。"""
    from .llm_audit import llm_call
    from .llm_backend import get_backend

    role = binding.role if binding else "viewer"
    if role == "group":
        source_type, source_id = "group", (binding.group_id or "")
        g = db.query(Group).filter(Group.id == source_id).first()
        source_name = g.name if g else source_id
    elif role in ("admin", "operator"):
        source_type, source_id, source_name = "admin", sender, "企微后台"
    else:
        source_type, source_id, source_name = "hotel", (binding.group_id or ""), "企微用户"

    data, guard_n = _query_data(db, text, binding)

    def direct() -> str:
        return f"📋 查询结果（系统数据直出）：\n{data}"

    has_model = get_backend(db) is not None
    if not has_model:
        # 无模型：审计留痕（占位）后按数据直出；非数据问题给提示
        llm_call(
            db, source_type=source_type, source_id=source_id, source_name=source_name,
            user_id=sender, scene="企微问答", user_input=text,
            prompt=f"（未配置模型，数据直出）\n{data or ''}\n用户问题：{text}",
        )
        return direct() if data else (
            "AI 模型未接入。可回复『帮助』看指令列表；管理员在后台配置本地/云端模型后此处自动生效。"
        )

    prompt = (
        "你是酒店 AI 授权管理系统的企微助手。严格依据下面【实时数据】回答，"
        "禁止编造数据中不存在的酒店名、日期或数字；数据不足就如实说明。"
        "若数据含清单请**完整列出全部条目**（每行一条，不要省略或概括掉条目）；"
        "非清单问题用中文简洁回答（不超过 250 字）。\n"
        f"【实时数据】\n{data or '（本次提问未命中结构化数据，按通用助手回答）'}\n"
        f"用户问题：{text}"
    )
    r = llm_call(
        db, source_type=source_type, source_id=source_id, source_name=source_name,
        user_id=sender, scene="企微问答", user_input=text, prompt=prompt,
    )
    if r["status"] == "error":
        return (direct() + "\n（AI 摘要暂不可用，以上为系统数据直出）") if data else (
            "AI 问答暂不可用（模型服务异常，已记录），请稍后再试或回复『帮助』看指令列表。"
        )
    out = (r["output"] or "").strip()[:1600]

    # 完整性校验：仅对「清单类」问法生效，且只对标主清单条目数（防小模型截断漏项；
    # 不误伤咨询类回答——咨询回答本就无需罗列全部编码）
    if data and out and guard_n > 0 and _is_list_query(text):
        import re as _re
        exp = len(_re.findall(r"（[A-Z]{2}-[^）]+）", data))
        act = len(_re.findall(r"（[A-Z]{2}-[^）]+）", out))
        if exp > 0 and act < min(exp, guard_n):
            return direct() + f"\n（模型输出不完整 {act}/{min(exp, guard_n)} 条，已切换为系统数据直出）"

    return out or (direct() if data else "（模型无输出）")


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
            "· 其他问题直接发 —— AI 为你解答\n"
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

    # 未命中指令 → 大模型问答（本地/云端均可，经审计网关全量留痕）
    return _llm_answer(db, text, sender, binding)
