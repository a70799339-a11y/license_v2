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
    # 实时告警企微推送（紧急/高危即时；失败不影响落库）
    try:
        from .wecom import notify_alert

        notify_alert(db, alert)
    except Exception:
        pass
    return True


def scan_stale_heartbeats(db: Session, timeout_hours: int = 72) -> int:
    """扫描长时间无心跳的在册酒店（读取告警时惰性触发，补上「还在停摆」的告警）。
    T6 性能：24h 去重批量一次查回（替代逐酒店 N+1，1w 告警量下该 N+1 是 1.4s 主因）；
    跳过已停用酒店（停用店不再产生心跳告警，也省掉测试清扫店的无效扫描）。"""
    from .models import Hotel

    cutoff = (datetime.utcnow() - timedelta(hours=timeout_hours)).strftime("%Y-%m-%d %H:%M:%S")
    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    dedup = {
        name
        for (name,) in db.query(Alert.hotel_name)
        .filter(Alert.rule == "心跳中断", Alert.time >= since)
        .distinct()
    }
    count = 0
    for h in db.query(Hotel).filter(Hotel.last_active != "", Hotel.status != "已停用").all():
        if h.last_active < cutoff and h.name not in dedup:
            # 去重已批量完成（dedupe_hours=0 跳过单条 exists 查询）
            if raise_alert(
                db,
                level="中",
                rule="心跳中断",
                hotel_name=h.name,
                detail=f"最近心跳 {h.last_active}，已超过 {timeout_hours} 小时无同步",
                dedupe_hours=0,
            ):
                count += 1
    if count:
        db.commit()
    return count
