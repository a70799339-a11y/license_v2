"""企业微信机器人 —— 推送（群机器人 webhook）+ 指令管道

场景 A（内部管理）：运营群收全量告警/日报
场景 B（集团管理）：集团群只收本集团告警/日报（数据隔离在服务端）

安全：推送内容与日志同管道脱敏（mask_text）；指令进审计；dry-run 默认开
（未配置真实 webhook 前不外发，最近推送存 settings 供测试断言）。
"""
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .masking import mask_text
from .models import Alert, AuditLog, Group, Hotel, Setting, WecomBinding, WecomGroupHook


# ---------- 时间展示（库存 UTC，对客户一律换算北京时间） ----------
def _fmt_bj(ts, fmt="%Y-%m-%d %H:%M") -> str:
    """UTC 时间串 → 北京时间（UTC+8）展示；解析失败原样返回"""
    s = (ts or "").strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return (datetime.strptime(s, f) + timedelta(hours=8)).strftime(fmt)
        except ValueError:
            continue
    return s


def _today_bj():
    """北京时间「今天」——企微回复里的日期与到期天数按此计算"""
    return (datetime.utcnow() + timedelta(hours=8)).date()


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
    """紧急/高危告警即时推；中危进日报汇总（不单独推）。失败不影响告警落库。
    防刷屏（v1.1）：同规则 5 分钟窗口内首条即时推，其余进聚合缓冲，窗口结束合并补发。"""
    try:
        if alert.level not in ("紧急", "高"):
            return
        gid = hotel_group_id(db, alert.hotel_name)
        hid = hotel_id_by_name(db, alert.hotel_name)
        item = {
            "level": alert.level, "rule": alert.rule, "hotel": alert.hotel_name,
            "detail": alert.detail, "time": alert.time, "gid": gid, "hid": hid,
        }
        agg = json.loads(get_setting(db, "alert_push_agg", "") or '{"start":"","items":[]}')
        now = datetime.utcnow()
        start = agg.get("start") or ""
        # 窗口到期（>5 分钟）→ 先冲刷窗口内聚合项，再开新窗口
        try:
            window_age_ok = bool(start) and (now - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() > 300
        except Exception:
            window_age_ok = True
        buffered = agg.get("items") or []
        if window_age_ok and buffered:
            _flush_alert_agg(db, buffered)
            buffered = []
        # 按规则聚合：同规则窗口内首条即时推，其余合并补发；不同规则各自首条都即时
        first_of_rule = not any(i.get("rule") == alert.rule for i in buffered)
        buffered.append(item)
        set_setting(db, "alert_push_agg", json.dumps(
            {"start": start or now.strftime("%Y-%m-%d %H:%M:%S"), "items": buffered}, ensure_ascii=False)[:4000])
        db.commit()
        if first_of_rule:
            content = (
                f"**🚨 风控告警（{alert.level}）**\n"
                f"> 规则：{alert.rule}\n"
                f"> 酒店：{alert.hotel_name}\n"
                f"> 详情：{alert.detail}\n"
                f"> 时间：{_fmt_bj(alert.time)}\n"
                f"> 窗口内同类告警将合并推送"
            )
            push_message(db, content, group_id=gid, hotel_id=hid)
    except Exception:
        pass  # 推送失败不影响告警记账


def _flush_alert_agg(db: Session, items: list) -> None:
    """把聚合缓冲里的告警合并为一条推送（按规则分组，最多列 8 条）"""
    if not items:
        return
    by_rule: dict = {}
    for it in items:
        by_rule.setdefault(it.get("rule", ""), []).append(it)
    lines = [f"**📉 窗口内聚合告警 {len(items)} 条**"]
    for rule, lst in list(by_rule.items())[:6]:
        lines.append(f"> {rule}：{len(lst)} 条（{('、'.join(i.get('hotel', '') for i in lst[:3]))}{'…' if len(lst) > 3 else ''}）")
    # 路由到运营群（聚合项跨多个实体，统一进 ops 通道）
    push_message(db, "\n".join(lines))
    set_setting(db, "alert_push_agg", json.dumps({"start": "", "items": []}, ensure_ascii=False))
    db.commit()


def flush_alert_agg_if_due(db: Session) -> None:
    """窗口到期冲刷入口（日报/健康检查等调用；未到期静默）"""
    try:
        agg = json.loads(get_setting(db, "alert_push_agg", "") or "{}")
        items = agg.get("items") or []
        start = agg.get("start") or ""
        if not items:
            return
        try:
            due = (datetime.utcnow() - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() > 300
        except Exception:
            due = True
        if due:
            _flush_alert_agg(db, items)
    except Exception:
        pass


def notify_alert_handled(db: Session, alert: Alert, handler: str, note: str) -> None:
    """处置结果推企微（闭环）：路由到该店/集团通道。失败不影响处置记录。"""
    try:
        gid = hotel_group_id(db, alert.hotel_name)
        hid = hotel_id_by_name(db, alert.hotel_name)
        content = (
            f"**✅ 告警已处置（{alert.level}）**\n"
            f"> 规则：{alert.rule}\n"
            f"> 酒店：{alert.hotel_name}\n"
            f"> 处置人：{handler or '—'}\n"
            f"> 备注：{note or '（无）'}\n"
            f"> 处置时间：{_fmt_bj(alert.handled_at)}"
        )
        push_message(db, content, group_id=gid, hotel_id=hid)
    except Exception:
        pass


# ---------- 日报（惰性：当日首次读取告警/统计时生成） ----------
def _daily_key() -> str:
    """日报日期（北京时间）"""
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")


def _daily_cutoff() -> str:
    """北京时间今日 00:00 对应的 UTC 时间串（按天过滤告警用）"""
    bj_mid = (datetime.utcnow() + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (bj_mid - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def build_ops_daily(db: Session) -> str:
    today = _daily_key()
    alerts = db.query(Alert).filter(Alert.time >= _daily_cutoff()).all()
    from .models import License

    soon = []
    for h in db.query(Hotel).all():
        if h.expire_at and h.expire_at <= "2027-01-01":
            soon.append(h)
    lines = [f"**📋 运营日报 {today}**"]
    lines.append(f"- 新增告警：{len(alerts)} 条（紧急 {sum(1 for a in alerts if a.level == '紧急')} / 高 {sum(1 for a in alerts if a.level == '高')}）")
    lines.append(f"- 7 天内到期提醒：{sum(1 for h in soon if h.status in ('即将到期', '已过期'))} 家")
    # 超时未处置升级提醒：紧急/高危超过 24 小时仍未处理
    cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    overdue = [
        a for a in db.query(Alert).filter(Alert.status != "已处置", Alert.level.in_(["紧急", "高"])).all()
        if (a.time or "") < cutoff
    ]
    if overdue:
        lines.append(f"- ⏰ **超时未处置（紧急/高 >24h）：{len(overdue)} 条**，请尽快处理：")
        for a in overdue[:5]:
            lines.append(f"  - [{a.level}] {a.rule} · {a.hotel_name} · {_fmt_bj(a.time, '%m-%d %H:%M')}")
        if len(overdue) > 5:
            lines.append(f"  - …等共 {len(overdue)} 条，完整清单到后台风险页查看")
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
        .filter(Alert.time >= _daily_cutoff(), Alert.hotel_name.in_(names))
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
    flush_alert_agg_if_due(db)  # 窗口到期的聚合告警先冲刷
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


def _match_group(db: Session, text: str):
    """问题里出现集团全名/集团编码 → 返回该集团（最长名优先），用于数据范围收敛"""
    matches = [g for g in db.query(Group).all() if g.name and g.name in text]
    if matches:
        return max(matches, key=lambda g: len(g.name or ""))
    return next((g for g in db.query(Group).all() if g.code and g.code in text), None)


def _is_list_query(text: str) -> bool:
    """是否为「清单类」问法（完整性兜底只对这类生效，避免误伤咨询类回答）"""
    return any(k in text for k in ("查询", "查一下", "查出", "查找", "查查", "列出", "清单", "哪些", "有哪些", "多少", "列表"))


def _query_data(db: Session, text: str, binding) -> tuple:
    """按问题意图从数据库取真实数据（接地）。
    返回 (数据文本, 完整性基线, 强校验)；未命中数据意图返回 (None, 0, False)。
    完整性基线 = 主清单（临期/告警/设备）条目数，不含「已过期」附录；
    强校验=True（单店告警全量）时不再要求清单类问法，兜底必生效。"""
    from datetime import date

    hotels, scope = _scoped_hotels(db, binding)
    # 单集团提问（问题里带集团名/编码）→ 数据范围收敛到该集团（在角色隔离之上再收窄）
    gt = _match_group(db, text)
    if gt is not None:
        hotels = [h for h in hotels if h.group_id == gt.id]
        scope = gt.name
    today = _today_bj()
    lines = [f"今天日期：{today.isoformat()}"]

    def days_left(h: Hotel):
        try:
            return (date.fromisoformat(h.expire_at) - today).days
        except Exception:
            return None

    hit = False
    guard_n = 0

    # 单店档案（问题指名到具体门店）：真实字段直出，
    # 杜绝把全局统计（设备占用/告警总量等）冒充该店数据。
    code_norms = {_norm_code(t) for t in _find_code_tokens(text)}
    _targets = [
        h for h in hotels
        if (h.code and _norm_code(h.code) in code_norms) or (h.name and h.name in text)
    ]
    for th in _targets:
        hit = True
        un = db.query(Alert).filter(Alert.hotel_name == th.name, Alert.status != "已处置").count()
        lines.append(
            f"【单店档案】{th.name}（{th.code}）：状态 {th.status}｜套餐 {th.plan}｜到期 {th.expire_at}"
            f"｜设备 {th.devices}/{th.max_devices}｜自动续期 {'开' if th.auto_renew else '关'}｜未处置告警 {un} 条"
        )

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

    strict = False
    if any(k in text for k in ("告警", "风险", "异常")):
        hit = True
        code_of = {h.name: (h.code or "") for h in hotels}
        # 风险等级过滤（v1.1.1）：问『高风险/高危/紧急』只给高+紧急，问『中风险/中危』只给中
        level_filter = None
        if any(k in text for k in ("高风险", "高危", "紧急", "严重")):
            level_filter = ("高", "紧急")
        elif any(k in text for k in ("中风险", "中危")):
            level_filter = ("中",)

        def _lv_ok(a: Alert) -> bool:
            return level_filter is None or a.level in level_filter

        # 单店提问（问题里出现该店编码/全名）→ 取该店全量告警，避免老告警被全局窗口挤掉
        # （编码匹配走归一化，ht-0004 也能命中 HT-0004）
        target = _targets[0] if _targets else next(
            (h for h in hotels if h.name and h.name in text), None
        )
        if target is not None:
            rows = [
                a for a in db.query(Alert).filter(Alert.hotel_name == target.name)
                .order_by(Alert.time.desc()).limit(30).all() if _lv_ok(a)
            ]
            guard_n = min(len(rows), 30)
            strict = guard_n > 0  # 单店全量：无论问法都必须完整，漏项即切数据直出
            by_lv: dict = {}
            for a in rows:
                by_lv[a.level] = by_lv.get(a.level, 0) + 1
            lv_txt = "、".join(f"{k} {v} 条" for k, v in by_lv.items()) or "无"
            lines.append(f"【{target.name}（{target.code}）告警】共 {len(rows)} 条（{lv_txt}，含已处置历史）：")
            if rows:
                for a in rows[:30]:
                    lines.append(f"- [{a.level}] {a.rule}｜{a.status}｜{_fmt_bj(a.time, '%m-%d %H:%M')}（{target.code}）")
            else:
                lines.append("（该店暂无告警记录）")
        else:
            # 全局提问：总量 + 按酒店聚合（用户要找的是“哪些店异常”）+ 最近明细
            # 口径与统计基座一致：只算在营（非已停用）酒店的未处置告警
            names = {h.name for h in hotels if h.status != "已停用"}
            rows = [
                a for a in db.query(Alert).order_by(Alert.time.desc()).limit(400).all()
                if a.hotel_name in names and a.status != "已处置" and _lv_ok(a)
            ]
            by_hotel: dict = {}
            for a in rows:
                d = by_hotel.setdefault(a.hotel_name, {"n": 0, "rules": set()})
                d["n"] += 1
                d["rules"].add(a.rule)
            top = sorted(by_hotel.items(), key=lambda kv: -kv[1]["n"])[:25]
            guard_n = min(len(top), 25)  # 清单类问法要求把聚合清单列全
            lv_all: dict = {}
            for a in rows:
                lv_all[a.level] = lv_all.get(a.level, 0) + 1
            lv_txt = "、".join(f"{k} {v} 条" for k, v in lv_all.items()) or "无"
            lines.append(
                f"【未处置告警】共 {len(rows)} 条，涉及 {len(by_hotel)} 家酒店（{lv_txt}）；"
                f"按告警数列前 {len(top)} 家（完整清单可到后台查看）："
            )
            if top:
                for name, d in top:
                    lines.append(f"- {name}（{code_of.get(name, '')}）{d['n']} 条：{'、'.join(sorted(d['rules']))}")
            else:
                lines.append("（无未处置告警）")
            recent = rows[:8]
            if recent:
                lines.append("【最近告警明细】：")
                for a in recent:
                    lines.append(
                        f"- [{a.level}] {a.rule}｜{a.hotel_name}（{code_of.get(a.hotel_name, '')}）"
                        f"｜{a.status}｜{_fmt_bj(a.time, '%m-%d %H:%M')}"
                    )

    if "设备" in text:
        hit = True
        lines.append("【设备使用】：")
        for h in sorted((x for x in hotels if x.status != "已停用"), key=lambda x: -(x.devices or 0))[:15]:
            lines.append(f"- {h.name}：{h.devices}/{h.max_devices}")

    # 操作/审计日志（提取 N 条）：真实数据直出，杜绝模型编造日志条目
    if any(k in text for k in ("日志", "操作记录", "审计")):
        hit = True
        m = re.search(r"(\d+)\s*条", text)
        if m:
            n = int(m.group(1))
        else:
            _cn_num = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            m2 = re.search(r"前\s*([一二两三四五六七八九十])\s*条", text)
            n = _cn_num.get(m2.group(1), 10) if m2 else 10
        n = max(1, min(n, 50))
        name_code = {h.name: (h.code or "") for h in hotels}
        rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(n).all()
        if binding and binding.role == "group":
            names = set(name_code)
            rows = [a for a in rows if any(nm in (a.target or "") for nm in names)][:n]
        guard_n = len(rows)
        strict = guard_n > 0  # 日志条目必须完整列出，漏项即切数据直出
        lines.append(f"【操作日志（最近 {len(rows)} 条）】：")
        if rows:
            for a in rows:
                code_hint = ""
                for nm, cd in name_code.items():
                    if nm and nm in (a.target or ""):
                        code_hint = f"（{cd}）"
                        break
                t = _fmt_bj(a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "")
                lines.append(f"- {t}｜{a.action}｜{a.target}{code_hint}｜{a.operator}｜{a.detail}")
        else:
            lines.append("（暂无操作日志）")

    # 集团/子酒店（未命中更具体意图时）：单集团→列成员；全局→各集团子酒店数
    if not hit and scope != "无" and ("集团" in text or "子酒店" in text) and any(
        k in text for k in ("几家", "多少", "数量", "子酒店", "成员", "门店", "分别",
                            "哪些", "列出", "清单", "统计", "情况", "总览", "有几个", "排行")
    ):
        hit = True
        if gt is not None:
            members = sorted((h for h in hotels if h.status != "已停用"), key=lambda x: x.code or "")
            guard_n = min(len(members), 30)
            strict = guard_n > 0  # 单集团成员清单：漏项即切数据直出
            head = f"【{gt.name}（{gt.code}）子酒店】共 {len(members)} 家"
            if len(members) > 30:
                head += "（仅列前 30 家，完整清单请到后台查看）"
            lines.append(head + "：")
            if members:
                for h in members[:30]:
                    lines.append(f"- {h.name}（{h.code}）到期 {h.expire_at} 套餐 {h.plan} 状态 {h.status}")
            else:
                lines.append("（暂无在营子酒店）")
        else:
            member_counts: dict = {}
            for h in db.query(Hotel).filter(Hotel.status != "已停用", Hotel.group_id != "").all():
                member_counts[h.group_id] = member_counts.get(h.group_id, 0) + 1
            if binding and binding.role == "group":
                g_rows = [g for g in db.query(Group).all() if g.id == (binding.group_id or "")]
                n_empty = 0
            else:
                g_rows = [g for g in db.query(Group).all() if member_counts.get(g.id, 0) > 0]
                n_empty = sum(1 for g in db.query(Group).all() if member_counts.get(g.id, 0) == 0)
            g_rows.sort(key=lambda g: (-member_counts.get(g.id, 0), g.code or ""))
            guard_n = min(len(g_rows), 25)  # 清单类问法要求把集团清单列全
            head = f"【集团与子酒店】共 {len(g_rows)} 个集团有在营子酒店"
            if n_empty:
                head += f"（另有 {n_empty} 个集团暂无在营子酒店）"
            if len(g_rows) > 25:
                head += "；仅列前 25 家"
            lines.append(head + "：")
            for g in g_rows[:25]:
                lines.append(f"- {g.name}（{g.code}）：{member_counts.get(g.id, 0)} 家")
            if not g_rows:
                lines.append("（暂无集团）")

    # 酒店总清单（兜底问法：仅当未命中更具体意图时生效，避免与到期/告警/设备叠加）
    if not hit and any(k in text for k in ("酒店", "客户")) and any(
        k in text for k in ("列出", "全部", "清单", "列表", "有哪些", "哪些", "所有")
    ):
        hit = True
        active = sorted((x for x in hotels if x.status != "已停用"), key=lambda x: x.code or "")
        guard_n = min(len(active), 30)  # 清单直出上限 30 条，基线对标实际给出的条目
        head = f"【{scope}酒店清单】共 {len(active)} 家"
        if len(active) > 30:
            head += "（仅列前 30 家，完整清单请到后台查看）"
        lines.append(head + "：")
        if active:
            for h in active[:30]:
                lines.append(f"- {h.name}（{h.code}）到期 {h.expire_at} 套餐 {h.plan} 状态 {h.status}")
        else:
            lines.append("（暂无酒店）")

    return ("\n".join(lines), guard_n, strict) if hit else (None, 0, False)


ACK_TEXT = "🔍 正在检索，请稍候…（数据查询/AI 生成中）"

# 变更类操作请求（拉黑/停用等）：机器人无对话内变更能力，不得谎称执行
ACTION_WORDS = ("拉黑", "封禁", "黑名单", "黑掉", "停用", "吊销", "删除", "解绑", "禁用", "冻结", "取消授权")
ACTION_CLAIMS = ("已拉黑", "将拉黑", "拉黑成功", "已封禁", "已停用", "已删除", "已吊销", "已处理完毕",
                 "我们将", "将为您", "已为您", "已经拉黑", "已加入黑名单", "处置完成")
ACTION_FOOTER = "\n\n⚠️ 说明：拉黑/停用/删除等变更操作我未执行、也不会代为执行。可回复『停用 HT-XXXX』（二次确认）或到后台「客户管理」页操作。"


def _analysis_stats(db: Session, binding, gt=None) -> str:
    """系统预计算聚合指标（分析类问题的事实基座）——
    小模型不擅长自己统计，由系统算好全量指标喂给它解读，消除『分析时数据缺失』。
    gt：单集团提问时收敛到该集团（与实时数据同口径）。"""
    from datetime import date

    hotels, scope = _scoped_hotels(db, binding)
    if gt is not None:
        hotels = [h for h in hotels if h.group_id == gt.id]
        scope = gt.name
    hotels = [h for h in hotels if h.status != "已停用"]
    today = _today_bj()

    def days_left(h: Hotel):
        try:
            return (date.fromisoformat(h.expire_at) - today).days
        except Exception:
            return None

    total = len(hotels)
    by_status: dict = {}
    by_plan: dict = {}
    soon7 = soon30 = soon90 = overdue = 0
    for h in hotels:
        by_status[h.status] = by_status.get(h.status, 0) + 1
        by_plan[h.plan] = by_plan.get(h.plan, 0) + 1
        d = days_left(h)
        if d is None:
            continue
        if d < 0 and -d <= 180:
            overdue += 1
        elif 0 <= d <= 7:
            soon7 += 1
        if d is not None and 0 <= d <= 30:
            soon30 += 1
        if d is not None and 0 <= d <= 90:
            soon90 += 1
    names = {h.name for h in hotels}
    alerts = [a for a in db.query(Alert).filter(Alert.status != "已处置").all() if a.hotel_name in names]
    by_level: dict = {}
    for a in alerts:
        by_level[a.level] = by_level.get(a.level, 0) + 1
    dev_total = sum(h.devices or 0 for h in hotels)
    dev_cap = sum(h.max_devices or 0 for h in hotels)

    return (
        f"【统计基座（系统精确计算，回答时请直接引用这些数字）】\n"
        f"- 范围：{scope}｜酒店总数 {total} 家\n"
        f"- 状态分布：{'、'.join(f'{k} {v} 家' for k, v in by_status.items()) or '无'}\n"
        f"- 套餐分布：{'、'.join(f'{k} {v} 家' for k, v in by_plan.items()) or '无'}\n"
        f"- 到期风险：7 天内 {soon7} 家｜30 天内 {soon30} 家｜90 天内 {soon90} 家｜已过期未续 {overdue} 家\n"
        f"- 未处置告警：{len(alerts)} 条"
        f"（{'、'.join(f'{k} {v} 条' for k, v in by_level.items()) or '无'}）\n"
        f"- 设备占用：{dev_total}/{dev_cap}"
    )


def _llm_answer(db: Session, text: str, sender: str, binding, on_slow=None) -> str:
    """企微自由问答：先从数据库取真实数据（接地），再经审计网关调用大模型组织回答。
    未配置模型时数据直出（保证查询类问题依然准确）；模型失败时降级为数据直出。
    on_slow：进入慢路径（AI 检索/生成）前的回调——用于向用户先发『检索中』提示。"""
    from .llm_audit import llm_call
    from .llm_backend import get_backend

    if on_slow:
        try:
            on_slow()
        except Exception:
            pass

    role = binding.role if binding else "viewer"
    if role == "group":
        source_type, source_id = "group", (binding.group_id or "")
        g = db.query(Group).filter(Group.id == source_id).first()
        source_name = g.name if g else source_id
    elif role in ("admin", "operator"):
        source_type, source_id, source_name = "admin", sender, "企微后台"
    else:
        source_type, source_id, source_name = "hotel", (binding.group_id or ""), "企微用户"

    data, guard_n, strict = _query_data(db, text, binding)

    def direct() -> str:
        return f"📋 查询结果：\n{data}"

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

    # 单店提问：禁止把【统计基座】全局数字（酒店总数/套餐分布/设备占用等）冒充该店数据
    single_mode = bool(data and "【单店档案】" in data)
    stats_block = "" if single_mode else f"{_analysis_stats(db, binding, _match_group(db, text))}\n"
    single_rule = (
        "本次提问指名到具体门店：只允许用【单店档案】里的字段回答；"
        "档案里没有的字段写『暂无数据』；"
        "严禁引用全局统计数字（酒店总数/套餐分布/设备占用/告警总量等）作为该店数据。\n"
        if single_mode else ""
    )
    prompt = (
        "你是酒店 AI 授权管理系统的企微助手。严格依据下面【实时数据】回答，"
        "禁止编造数据中不存在的酒店名、日期或数字；数据不足就如实说明。"
        "若数据含清单请**完整列出全部条目**（每行一条、保留（编码），不要省略或概括掉条目，编码必须逐字引用不得改动）；"
        "数据里的范围说明（如『共 N 条/涉及 N 家/前 N 家/完整清单到后台查看』）回答须原样保留，"
        "让用户分得清这是全量还是节选；"
        "分析/建议类问题请结合【统计基座】的精确数字回答（直接引用，不要自己重新统计）；"
        "用户请求执行变更操作（拉黑/停用/删除/吊销等）时，**严禁声称已执行或将代为执行**，"
        "只给涉及对象清单并引导到后台或确定性指令；"
        "非清单问题用中文简洁回答（不超过 250 字）。\n"
        f"{single_rule}"
        f"{stats_block}"
        f"【实时数据】\n{data or '（本次提问未命中结构化明细：不要编造任何清单/日志/明细条目/酒店编码；分析类问题仅可引用统计基座的聚合数字；需要明细数据就如实说明暂无并引导到后台查询）'}\n"
        f"用户问题：{text}"
    )
    # 快通道（v1.1）：短咨询类问题（无数据意图且 ≤30 字）走本地小模型提速；数据类一律主力模型
    fast_fn = None
    if data is None and len(text) <= 30:
        try:
            from .llm_backend import get_fast_backend

            fast_fn = get_fast_backend(db)
        except Exception:
            fast_fn = None
    r = llm_call(
        db, source_type=source_type, source_id=source_id, source_name=source_name,
        user_id=sender, scene="企微问答", user_input=text, prompt=prompt,
        backend=fast_fn,
    )
    if r["status"] == "error":
        return direct() if data else (
            "AI 问答暂不可用，请稍后再试或回复『帮助』看指令列表。"
        )
    out = (r["output"] or "").strip()[:2400]

    # 完整性校验：清单类问法或单店告警全量（strict）时生效
    # 双重判据（v1.1）：①编码出现次数 ②期望编码逐一精确命中（防模型抄错编码漏检）；
    # 任一不满足即切数据直出。不误伤咨询类回答——咨询回答本就无需罗列全部编码
    if data and out and guard_n > 0 and (strict or _is_list_query(text)):
        import re as _re
        pat = r"（([A-Z]{2}-[^）]+)）"
        exp_occ = len(_re.findall(pat, data))
        act_occ = len(_re.findall(pat, out))
        exp_codes = set(_re.findall(pat, data))
        act_codes = set(_re.findall(pat, out))
        matched = len(exp_codes & act_codes)
        if exp_occ > 0 and (act_occ < min(exp_occ, guard_n) or matched < min(len(exp_codes), guard_n)):
            return direct()  # AI 漏项/抄错编码 → 静默切换完整数据（兜底路径细节见审计日志）

    reply = out or (direct() if data else "（模型无输出）")

    # 变更操作请求（v1.1.1）：模型若谎称执行/承诺代办 → 丢弃叙述只给数据；一律附「未执行」说明
    if any(k in text for k in ACTION_WORDS):
        if out and any(c in out for c in ACTION_CLAIMS):
            reply = direct() if data else (
                "该操作我无法代为执行。可回复『停用 HT-XXXX』（二次确认）或到后台「客户管理」页操作。"
            )
        reply += ACTION_FOOTER
    return reply


def _norm_code(raw: str) -> str:
    """酒店编码归一：ht9999 / ht-9999 / HT 9999 → HT-9999（大小写与连字符统一）"""
    s = re.sub(r"[\s\-]+", "", (raw or "").strip().upper())
    if s.startswith("HT") and len(s) > 2:
        return "HT-" + s[2:]
    return s


def _find_code_tokens(text: str) -> list:
    """从自由文本中提取疑似酒店编码的 token（ht9999 / HT-0001 / HT-XA123 …）。
    要求 ht 后紧跟数字或带连字符，避免误伤 html 之类普通单词。"""
    out = []
    for m in re.finditer(r"(?i)(?<![0-9a-z])ht[- ]?[0-9a-z]+(?![0-9a-z])", text):
        tok = m.group(0)
        if tok.lower().startswith("ht-") or tok[2:3].isdigit():
            out.append(tok)
    return out


def process_command(db: Session, text: str, sender: str = "unknown", on_slow=None) -> str:
    """解析并执行企微指令。权限：续期需 operator+/集团绑定；查询需绑定。
    on_slow：慢路径（AI 问答）进入前回调（如向用户发『检索中』提示）。"""
    text = (text or "").strip()
    operator = f"企微({sender})"
    binding = db.query(WecomBinding).filter(WecomBinding.userid == sender).first()

    if not text or text in ("帮助", "help", "?"):
        return (
            "指令列表：\n"
            "· 状态 HT-0001 —— 查授权状态\n"
            "· 告警 HT-0001 —— 查最近告警\n"
            "· 续期 HT-0001 —— 续期一年（需回复『确认续期 HT-0001』）\n"
            "· 停用 HT-0001 —— 停用/拉黑该客户（需回复『确认停用 HT-0001』）\n"
            "· 其他问题直接发 —— AI 为你解答\n"
            "（使用前需管理员绑定你的企微账号）"
        )

    if binding is None:
        return (
            f"你的企微账号尚未绑定（userid：{sender}）。\n"
            f"请联系管理员绑定后使用；回复『帮助』可先看指令列表。"
        )

    # 指令解析：不依赖空格。「状态HT-0004」「状态ht-888」等无空格写法
    # 必须同样进入确定性通道，否则会绕过全部防瞎答保护掉进 LLM 编造。
    _KNOWN_CMDS = (
        "确认续期", "确认停用", "确认拉黑", "状态", "告警", "续期", "停用", "拉黑",
    )
    first_tok = text.split()[0] if text.split() else ""
    cmd, code = first_tok, ""
    if len(text.split()) > 1:
        code = text.split()[1]
    else:
        # 无空格：尝试按已知动词前缀切分（最长匹配优先）
        for known in _KNOWN_CMDS:
            if first_tok.startswith(known) and len(first_tok) > len(known):
                cmd, code = known, first_tok[len(known):].strip()
                break

    # 集团绑定用户只能操作本集团门店
    def allowed(h: Hotel) -> bool:
        if binding.role == "admin":
            return True
        if binding.role == "group":
            return h.group_id == binding.group_id
        return binding.role in ("operator",)

    # 编码归一（ht0004 ≡ ht-0004 ≡ HT-0004）；同时支持按名称精确查找
    code_stripped = code.strip()
    hotel = None
    if code_stripped:
        code_norm = _norm_code(code_stripped)
        hotel = (
            db.query(Hotel)
            .filter((func.upper(Hotel.code) == code_norm) | (Hotel.name == code_stripped))
            .first()
        )

    # 确定性指令缺编码 → 用法提示（不进 AI）
    if not code and cmd in ("状态", "告警", "续期", "停用", "拉黑"):
        return f"用法：{cmd} HT-0001（请带上客户编码）。回复『帮助』看全部指令。"

    # 确定性指令带未知编码/名称 → 直接明确拒绝（不进 AI，防瞎答）
    if hotel is None and code and cmd in (
        "状态", "告警", "续期", "确认续期", "停用", "确认停用", "拉黑", "确认拉黑"
    ):
        return f"未找到编号/名称为「{code}」的客户。请核对编码（如 HT-0001），或回复『帮助』看指令列表。"

    # 确定性指令目标存在但无权限（如只读角色/非本集团）→ 明确拒绝（不进 AI）
    if hotel is not None and not allowed(hotel) and cmd in (
        "状态", "告警", "续期", "确认续期", "停用", "确认停用", "拉黑", "确认拉黑"
    ):
        return f"你没有操作「{hotel.name}（{hotel.code}）」的权限，请联系管理员。"

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
        return "\n".join(f"· [{a.level}] {a.rule}（{a.status}）{_fmt_bj(a.time)}" for a in alerts)

    if cmd in ("续期", "确认续期") and hotel is not None and allowed(hotel):
        if cmd == "续期":
            return f"确认执行？回复：确认续期 {hotel.code}"
        hotel.expire_at = "2027-12-31"
        if hotel.status in ("即将到期", "已过期"):
            hotel.status = "正常"
        _audit(db, operator, "企微续期", hotel.name, "续期一年至 2027-12-31")
        db.commit()
        return f"✅ {hotel.name} 已续期至 2027-12-31"

    # 停用（拉黑）：v1.1.1 二次确认后停用客户（等效拉黑，剔除统计与查询）
    if cmd in ("停用", "确认停用", "拉黑", "确认拉黑") and hotel is not None and allowed(hotel):
        if cmd in ("停用", "拉黑"):
            return f"确认执行？回复：确认停用 {hotel.code}（停用后该店不再计入统计，可到后台恢复）"
        hotel.status = "已停用"
        _audit(db, operator, "企微停用", hotel.name, "对话停用（拉黑）")
        db.commit()
        try:
            push_message(db, f"**⛔ 客户已停用（拉黑）**\n> {hotel.name}（{hotel.code}）\n> 操作人：{operator}\n> 时间：{_fmt_bj(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))}")
        except Exception:
            pass
        return f"✅ {hotel.name}（{hotel.code}）已停用（拉黑），不再计入统计；如需恢复请到后台启用"

    # AI 前置接地门：消息里出现的酒店编码逐一查库，任一查无此店 → 确定性拒绝。
    # 否则「ht9999的状态详情」这类编码开头的提问会掉进 LLM，
    # 把全局统计冒充单店数据或直接编造状态（谎称）。
    for raw_tok in _find_code_tokens(text):
        cn = _norm_code(raw_tok)
        if db.query(Hotel).filter(func.upper(Hotel.code) == cn).first() is None:
            return f"未找到编号/名称为「{raw_tok}」的客户。请核对编码（如 HT-0001），或回复『帮助』看指令列表。"

    # 未命中指令 → 大模型问答（本地/云端均可，经审计网关全量留痕）
    return _llm_answer(db, text, sender, binding, on_slow)
