"""种子数据 —— 首次启动时写入；40 家酒店 + 设备 + 设置 + 操作日志"""
import json
import os
import random
import re
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .models import User, Hotel, License, Alert, Plan, Device, Setting, AuditLog, Group
from .auth import hash_password

CITIES = ["杭州", "上海", "成都", "深圳", "北京", "三亚", "西安", "南京", "广州", "重庆",
          "苏州", "武汉", "厦门", "青岛", "长沙", "昆明", "天津", "郑州", "合肥", "福州"]
GROUPS = ["华住集团", "凯悦集团", "锦江集团", "万豪集团", "亚朵集团", "首旅如家", "金陵集团", "丽呈集团"]
NAMES = ["西湖大酒店", "外滩茂悦", "天府丽呈", "湾万怡", "国贸亚朵", "海棠湾君悦", "钟楼全季", "金陵饭店",
         "滨江希尔顿", "古城假日", "湖畔开元", "空港铂尔曼", "金融城万豪", "老城墙亚朵", "星海豪生",
         "麓山悦榕", "江畔丽晶", "高铁站美居", "会展中心凯悦", "科技园智选", "翠湖宾馆", "滨海悦华",
         "岳麓书院酒店", "天府广场希尔顿", "西湖国宾馆", "秦淮河颐居", "珠江新城四季", "渝中洲际",
         "姑苏锦华", "东湖碧波", "鹭岛悦海", "崂山云顶", "橘子洲华天", "滇池温泉", "海河瑞吉",
         "嵩山少林客栈", "黄鹤楼锦江", "长城脚下公社", "黄山云海", "桂林漓江"]
PLANS = ["基础版", "专业版", "旗舰版"]
PLAN_MAXDEV = {"基础版": 30, "专业版": 60, "旗舰版": 150}

OS_LIST = ["Windows 11", "Windows 10", "Ubuntu 22.04", "macOS 15"]


def ensure_users(db: Session) -> None:
    """幂等补齐账号（升级旧库用）：admin / ops / viewer 三角色。
    S6：初始口令支持环境变量覆盖（ADMIN_PASSWORD/OPS_PASSWORD/VIEWER_PASSWORD），
    生产部署务必注入强口令——默认值仅用于演示/开发环境。"""
    import os

    wanted = [
        ("admin", os.environ.get("ADMIN_PASSWORD") or "admin123", "运营管理员", "admin"),
        ("ops", os.environ.get("OPS_PASSWORD") or "ops123", "运营专员", "operator"),
        ("viewer", os.environ.get("VIEWER_PASSWORD") or "viewer123", "只读访客", "viewer"),
    ]
    for username, pwd, name, role in wanted:
        if db.query(User).filter(User.username == username).first() is None:
            db.add(User(username=username, password_hash=hash_password(pwd), name=name, role=role))
    db.commit()
    # 原 6：仍在用默认口令时启动告警（生产必须设 ADMIN_PASSWORD/OPS_PASSWORD/VIEWER_PASSWORD 环境变量）
    if not os.environ.get("ADMIN_PASSWORD"):
        print("[security] ⚠️ 正在使用默认口令（admin123 等）！生产环境请设置 ADMIN_PASSWORD/OPS_PASSWORD/VIEWER_PASSWORD 环境变量后重启。")


def ensure_groups(db: Session) -> None:
    """幂等构建集团（总/子关系）：按酒店 group 名生成集团并回填 group_id"""
    existing = {g.name: g for g in db.query(Group).all()}
    # T4：按存量最大编号取基数（len() 在有删除时会撞已存在的 g 序号）
    seq = 0
    for gid in existing.values():
        m = re.search(r"(\d+)$", gid.id or "")
        if m:
            seq = max(seq, int(m.group(1)))
    for (gname,) in db.query(Hotel.group).distinct().all():
        if not gname:
            continue
        g = existing.get(gname)
        if g is None:
            seq += 1
            g = Group(
                id=f"g{seq:03d}",
                name=gname,
                code=f"GP-{seq:04d}",
                activation_key=f"GPK-{secrets.token_hex(8)}",  # R3：创建即生成，不留空窗
                contact="集团总部",
                phone="0571-88880000",
                plan="旗舰版",
                expire_at="2027-12-31",
                auto_renew=True,
                created_at="2026-01-01",
            )
            db.add(g)
            existing[gname] = g
    db.flush()
    for gname, g in existing.items():
        db.query(Hotel).filter(Hotel.group == gname, (Hotel.group_id == None) | (Hotel.group_id == "")).update(
            {Hotel.group_id: g.id}
        )
    db.commit()


def ensure_plans(db: Session) -> None:
    """幂等补齐套餐（F1：必需基础数据，与 SEED_DEMO_DATA 无关）：
    全新生产环境没有套餐 → 续期订单 _plan_price 取 0 全部被拒。
    按 id 只补缺失，不覆盖已有行（保护运营手调过的价格）。"""
    defs = [
        ("p1", "基础版", 1999, 19999, 30, ["基础经营看板", "设备管理", "到期提醒", "标准客服支持"], "#3B82F6"),
        ("p2", "专业版", 4999, 49999, 60, ["全部基础版功能", "AI 智能风控", "多门店集团管理", "批量操作与导入导出", "优先客服支持"], "#8B5CF6"),
        ("p3", "旗舰版", 9999, 99999, 150, ["全部专业版功能", "定制风控规则", "专属客户成功经理", "API 开放接口", "私有化部署可选"], "#F59E0B"),
    ]
    for pid, name, price, yprice, maxd, feats, color in defs:
        if db.query(Plan).filter(Plan.id == pid).first() is None:
            cnt = db.query(Hotel).filter(Hotel.plan == name).count()
            db.add(Plan(
                id=pid, name=name, price=price, yearly_price=yprice, max_devices=maxd,
                features=json.dumps(feats, ensure_ascii=False), hotels=cnt, color=color,
            ))
    db.commit()


def ensure_settings(db: Session) -> None:
    """幂等补齐系统设置（F1：必需基础数据，与 SEED_DEMO_DATA 无关）：
    按 key 只补缺失，不覆盖运营已改的值。"""
    for key, value in (
        ("auto_renew", "on"),
        ("renew_days_before", "7"),
        ("heartbeat_timeout_hours", "72"),
    ):
        if db.query(Setting).filter(Setting.key == key).first() is None:
            db.add(Setting(key=key, value=value))
    db.commit()


def ensure_code_seqs(db: Session) -> None:
    """T4：编号计数控位初始化（幂等，只升不降）——
    缺失则按存量最大编号建位；已存在则抬到 max(现值, 存量最大)，
    防手工插入的更大序号导致新编号撞主键。分配见 main.alloc_code_seq（行锁递增）。"""
    def _max_suffix(rows) -> int:
        mx = 0
        for (v,) in rows:
            m = re.search(r"(\d+)$", v or "")
            if m:
                mx = max(mx, int(m.group(1)))
        return mx

    specs = {
        "seq_hotel": _max_suffix(db.query(Hotel.id).all()),
        "seq_license": _max_suffix(db.query(License.id).all()),
        # 集团 id(gNNN) 与展示码 GP-NNNN 同号，取两者最大防历史错位
        "seq_group": max(
            _max_suffix(db.query(Group.id).all()),
            _max_suffix(db.query(Group.code).all()),
        ),
    }
    for key, mx in specs.items():
        row = db.query(Setting).filter(Setting.key == key).first()
        if row is None:
            db.add(Setting(key=key, value=str(mx)))
        else:
            try:
                cur = int(row.value or "0")
            except ValueError:
                cur = 0
            if mx > cur:
                row.value = str(mx)
    db.commit()


def seed(db: Session) -> None:
    if db.query(User).first() is not None:
        ensure_users(db)
        ensure_groups(db)
        ensure_plans(db)  # F1：已部署环境重启后补上缺失的基础数据
        ensure_settings(db)
        ensure_code_seqs(db)  # T4：计数控位幂等补齐/抬升
        return  # 已初始化

    ensure_users(db)
    # F1：套餐/设置是必需基础数据，在演示数据门禁之前创建——
    # 否则全新生产环境（SEED_DEMO_DATA 关）续期付款流程整体不可用
    ensure_plans(db)
    ensure_settings(db)

    # R2：演示数据只在显式开启时生成（SEED_DEMO_DATA=1）——
    # 生产默认关闭，防演示门店的顺序授权码/可预测指纹进生产
    if os.environ.get("SEED_DEMO_DATA", "").strip().lower() not in ("1", "true", "yes"):
        ensure_code_seqs(db)
        return

    rnd = random.Random(42)
    base_day = datetime(2026, 11, 1)

    hotel_ids = []
    for i in range(40):
        hid = f"h{i+1:03d}"
        hotel_ids.append(hid)
        plan = rnd.choice(PLANS)
        max_dev = PLAN_MAXDEV[plan]
        devices = rnd.randint(5, max_dev)
        expire_offset = rnd.choice([-30, -3, 5, 12, 25, 45, 80, 120, 200, 320])
        expire = base_day + timedelta(days=expire_offset)
        if expire < base_day:
            status = "已过期"
        elif expire <= base_day + timedelta(days=30):
            status = "即将到期"
        else:
            status = "正常"
        city = CITIES[i % len(CITIES)]
        name = f"{city}{NAMES[i % len(NAMES)]}"
        code = f"{city[:2].encode('unicode_escape').hex()[:4].upper()}-{i+1:03d}"
        db.add(Hotel(
            id=hid, name=name, code=f"HT-{i+1:04d}", group=GROUPS[i % len(GROUPS)], city=city,
            contact=f"联系人{i+1}", phone=f"13{rnd.randint(0,9)}****{rnd.randint(1000,9999)}",
            plan=plan, status=status, devices=devices, max_devices=max_dev,
            expire_at=expire.strftime("%Y-%m-%d"), auto_renew=rnd.random() > 0.4,
            created_at=(base_day - timedelta(days=rnd.randint(100, 900))).strftime("%Y-%m-%d"),
            last_active=(base_day - timedelta(hours=rnd.randint(1, 72))).strftime("%Y-%m-%d %H:%M"),
        ))

        # 设备 2-5 台
        for j in range(rnd.randint(2, 5)):
            db.add(Device(
                id=f"d{i+1:03d}-{j+1}", hotel_id=hid,
                name=f"{rnd.choice(['前台', '客房', '工程', '安保', '餐饮'])}-PC-{j+1}",
                os=rnd.choice(OS_LIST),
                fingerprint=f"f{secrets.token_hex(12)}",  # R2：随机指纹，不可预测
                last_active=(base_day - timedelta(minutes=rnd.randint(5, 1440))).strftime("%Y-%m-%d %H:%M"),
                status=rnd.choice(["在线", "在线", "在线", "离线"]),
            ))

        # 授权 1-2 条
        for k in range(rnd.randint(1, 2)):
            issued = expire - timedelta(days=365)
            db.add(License(
                id=f"l{i+1:03d}-{k+1}", code=f"LIC-2026-{secrets.token_hex(4).upper()}",
                hotel_id=hid, hotel_name=name,
                plan=plan, type=rnd.choice(["新签", "续期", "升级"]),
                issued_at=issued.strftime("%Y-%m-%d"), expire_at=expire.strftime("%Y-%m-%d"),
                status="已过期" if expire < base_day else "生效中",
                devices=devices, max_devices=max_dev,
            ))

    # 告警
    alert_defs = [
        ("紧急", "破解特征检测", "客户端完整性校验失败，疑似被篡改", "待处理"),
        ("高", "多设备并发激活", "并发设备超出授权上限", "处理中"),
        ("高", "时间回滚", "客户端时间戳倒流 48 小时", "待处理"),
        ("中", "设备指纹变更", "设备指纹哈希变化超过阈值", "已处置"),
        ("中", "心跳中断（72h）", "连续 76 小时无心跳回连", "处理中"),
        ("高", "License 共享检测", "同一授权码在多个城市 IP 激活", "已处置"),
        ("高", "多设备并发激活", "并发设备超出授权上限 20%", "待处理"),
        ("紧急", "客户端完整性校验", "校验失败，拒绝启动并告警", "待处理"),
        ("中", "异地 IP 聚集", "多个门店授权回连同一 IP", "已处置"),
    ]
    for idx, (level, rule, detail, status) in enumerate(alert_defs):
        hname = f"{CITIES[idx % len(CITIES)]}{NAMES[idx % len(NAMES)]}"
        db.add(Alert(
            id=f"a{idx+1:03d}", level=level, rule=rule, hotel_name=hname, detail=detail,
            time=(base_day - timedelta(hours=idx * 7 + 2)).strftime("%Y-%m-%d %H:%M"),
            status=status,
        ))

    # 套餐/设置已由 ensure_plans/ensure_settings 在演示数据门禁前创建（F1），此处不再重复写

    # 操作日志
    log_defs = [
        ("运营管理员", "发放授权", "杭州西湖大酒店", "签发旗舰版授权 LIC-2026-0001，有效期 12 个月"),
        ("运营管理员", "批量续期", "3 家酒店", "续期至 2027-11-01"),
        ("运营专员", "处置告警", "设备指纹变更", "确认为正常换机，标记已处置"),
        ("运营管理员", "吊销授权", "南京金陵饭店", "原因：申请退订"),
        ("运营管理员", "新增客户", "苏州姑苏锦华", "套餐：专业版"),
    ]
    for idx, (op, action, target, detail) in enumerate(log_defs):
        from .masking import mask_text

        db.add(AuditLog(
            operator=op, action=action, target=mask_text(target), detail=mask_text(detail),
            created_at=base_day - timedelta(hours=idx * 11),
        ))

    db.commit()
    ensure_groups(db)  # 构建集团（总/子）关系
    ensure_code_seqs(db)  # T4：演示数据建完后初始化计数控位（在其后，防与 h001..h040 等种子编号相撞）
