"""P2-2 续期流失预测 —— 规则打分（数据全部来自现有心跳/设备/到期字段，模型可后置）

风险档：低 / 中 / 高 / 紧急；factors 列出触发因子（供 UI 展示与 AI 话术引用）。
约定：数字一律数据直出，模型只管表述。
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import Device, Hotel, RenewOrder

LEVELS = ["低", "中", "高", "紧急"]
_LEVEL_RANK = {v: i for i, v in enumerate(LEVELS)}


def _parse_ts_any(s) -> datetime | None:
    """兼容秒级/分级/日期三种历史时间格式"""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _days_left(hotel: Hotel) -> int:
    try:
        return (datetime.strptime(hotel.expire_at, "%Y-%m-%d") - datetime.utcnow()).days
    except (ValueError, TypeError):
        return 3650  # 无到期日视为长期有效


def _active_ratio(db: Session, hotel: Hotel) -> float:
    """近 30 天活跃设备占比（活跃=last_active 在 30 天内且未解绑/迁移）"""
    total = (
        db.query(Device)
        .filter(Device.hotel_id == hotel.id, Device.status.in_(["在线", "离线"]))
        .count()
    )
    if total == 0:
        return 0.0
    since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    active = (
        db.query(Device)
        .filter(
            Device.hotel_id == hotel.id,
            Device.status.in_(["在线", "离线"]),
            Device.last_active >= since,
        )
        .count()
    )
    return active / total


def _idle_days(db: Session, hotel: Hotel) -> int:
    """距最近一次心跳的天数（无设备记录视为 -1 表示未知）"""
    d = (
        db.query(Device)
        .filter(Device.hotel_id == hotel.id, Device.status.in_(["在线", "离线"]))
        .order_by(Device.last_active.desc())
        .first()
    )
    if d is None:
        return -1
    last = _parse_ts_any(d.last_active)
    if last is None:
        return -1
    return max(0, (datetime.utcnow() - last).days)


def risk_score(db: Session, hotel: Hotel) -> dict:
    """确定性规则打分（无模型依赖，可回归）"""
    days = _days_left(hotel)
    ratio = _active_ratio(db, hotel)
    idle = _idle_days(db, hotel)
    factors: list[str] = []
    level = 0  # 低

    # 基础档：按剩余天数
    if days <= 7:
        level = max(level, 2)
        factors.append(f"剩余 {days} 天到期")
    elif days <= 30:
        level = max(level, 1)
        factors.append(f"剩余 {days} 天到期")
    elif days < 0:
        level = 2
        factors.append(f"已过期 {-days} 天")

    # 使用率低 → 升一档
    if ratio < 0.3:
        level = min(3, level + 1)
        factors.append(f"设备使用率仅 {ratio:.0%}")

    # 紧急：到期 <7 天 + 有未支付订单 + >7 天无心跳
    unpaid = (
        db.query(RenewOrder)
        .filter(
            RenewOrder.target_id == hotel.id,
            RenewOrder.status.in_(["待支付", "待确认", "已支付"]),
        )
        .count()
    )
    if days <= 7 and unpaid and idle > 7:
        level = 3
        factors.append(f"到期 ≤7 天且 {idle} 天无心跳、有未完成续期订单")

    # 自动续期已开 → 降一档（收入有保障）
    if hotel.auto_renew and level > 0:
        level -= 1
        factors.append("已开自动续期（降档）")

    return {
        "hotel_id": hotel.id,
        "hotel_name": hotel.name,
        "code": hotel.code,
        "group": hotel.group,
        "plan": hotel.plan,
        "expire_at": hotel.expire_at,
        "days_left": days,
        "active_ratio": round(ratio, 2),
        "idle_days": idle,
        "unpaid_orders": unpaid,
        "auto_renew": bool(hotel.auto_renew),
        "risk": LEVELS[level],
        "factors": factors,
    }


def ranked_risks(db: Session) -> list[dict]:
    rows = [risk_score(db, h) for h in db.query(Hotel).filter(Hotel.status != "已停用").all()]
    rows.sort(key=lambda r: (-_LEVEL_RANK[r["risk"]], r["days_left"]))
    return rows
