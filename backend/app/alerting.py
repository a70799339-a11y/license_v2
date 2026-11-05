"""实时告警管道 —— 检测点命中时自动写入告警（去重：同规则+同酒店 24h 内不重复）

风控页看到的告警由此模块真实产生（不再依赖种子演示数据）。
"""
from datetime import datetime, timedelta
import uuid

from sqlalchemy.orm import Session

from .masking import mask_text
from .models import Alert


def raise_alert(
    db: Session,
    *,
    level: str,
    rule: str,
    hotel_name: str,
    detail: str,
    dedupe_hours: int = 24,
) -> bool:
    """写入一条告警；命中去重窗口则跳过。返回是否实际写入。"""
    if dedupe_hours > 0:
        since = (datetime.utcnow() - timedelta(hours=dedupe_hours)).strftime("%Y-%m-%d %H:%M:%S")
        exists = (
            db.query(Alert)
            .filter(
                Alert.rule == rule,
                Alert.hotel_name == hotel_name,
                Alert.time >= since,
            )
            .first()
        )
        if exists is not None:
            return False

    alert = Alert(
        id=f"al{uuid.uuid4().hex[:12]}",
        level=level,
        rule=rule,
        hotel_name=hotel_name,
        detail=mask_text(detail)[:250],
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        status="待处理",
    )
    db.add(alert)
    db.flush()
    return True


def scan_stale_heartbeats(db: Session, timeout_hours: int = 72) -> int:
    """扫描长时间无心跳的在册酒店（读取告警时惰性触发，补上「还在停摆」的告警）"""
    from .models import Hotel

    cutoff = (datetime.utcnow() - timedelta(hours=timeout_hours)).strftime("%Y-%m-%d %H:%M:%S")
    count = 0
    for h in db.query(Hotel).filter(Hotel.last_active != "").all():
        if h.last_active < cutoff:
            if raise_alert(
                db,
                level="中",
                rule="心跳中断",
                hotel_name=h.name,
                detail=f"最近心跳 {h.last_active}，已超过 {timeout_hours} 小时无同步",
                dedupe_hours=24,
            ):
                count += 1
    if count:
        db.commit()
    return count
