"""ORM 数据模型 —— groups / hotels / licenses / alerts / plans / users"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    name = Column(String(64), nullable=False)
    role = Column(String(32), default="operator")


class Group(Base):
    """集团（总端）—— 旗下 N 家门店，总端客户端用集团码激活后统一查看/管理"""

    __tablename__ = "groups"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)  # 集团码 GP-xxxx，总端激活用
    contact = Column(String(64), default="")
    phone = Column(String(32), default="")
    status = Column(String(16), default="正常")
    plan = Column(String(16), default="旗舰版")  # 集团套餐（决定功能上限）
    expire_at = Column(String(10), default="")
    auto_renew = Column(Boolean, default=False)
    created_at = Column(String(10), default="")


class WecomGroupHook(Base):
    """集团企微群机器人 webhook（场景 B：集团告警/日报推本集团群）"""

    __tablename__ = "wecom_group_hooks"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String(16), unique=True, index=True, nullable=False)
    webhook_url = Column(String(512), default="")


class WecomBinding(Base):
    """企微用户 ↔ 系统角色绑定（指令管道权限依据）"""

    __tablename__ = "wecom_bindings"
    id = Column(Integer, primary_key=True, index=True)
    userid = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(64), default="")
    role = Column(String(16), default="viewer")  # admin / operator / viewer / group
    group_id = Column(String(16), default="")  # role=group 时生效


class Hotel(Base):
    __tablename__ = "hotels"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    code = Column(String(32), unique=True, nullable=False, index=True)
    group = Column(String(64), default="")
    group_id = Column(String(16), index=True, default="")  # 归属集团（总/子关系）
    city = Column(String(32), default="")
    contact = Column(String(64), default="")
    phone = Column(String(32), default="")
    plan = Column(String(16), default="基础版")
    status = Column(String(16), default="正常")
    devices = Column(Integer, default=0)
    max_devices = Column(Integer, default=30)
    expire_at = Column(String(10), default="")
    auto_renew = Column(Boolean, default=False)
    created_at = Column(String(10), default="")
    last_active = Column(String(32), default="")


class License(Base):
    __tablename__ = "licenses"
    id = Column(String(16), primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    hotel_name = Column(String(128), nullable=False)
    plan = Column(String(16), default="基础版")
    type = Column(String(16), default="新签")
    issued_at = Column(String(10), default="")
    expire_at = Column(String(10), default="")
    status = Column(String(16), default="生效中")
    devices = Column(Integer, default=0)
    max_devices = Column(Integer, default=30)
    # P1-3 模块/配额位：功能模块 JSON（空=随 Plan.features）+ 按房量计价（0=不限）
    features = Column(Text, default="")
    max_rooms = Column(Integer, default=0)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(String(16), primary_key=True, index=True)
    level = Column(String(8), default="中")
    rule = Column(String(64), default="")
    hotel_name = Column(String(128), default="")
    detail = Column(String(256), default="")
    time = Column(String(32), default="")
    status = Column(String(16), default="待处理")
    # 处置闭环（v1.1）：谁在什么时候处理、处置备注
    handled_by = Column(String(64), default="")
    handle_note = Column(String(256), default="")
    handled_at = Column(String(32), default="")


class Plan(Base):
    __tablename__ = "plans"
    id = Column(String(16), primary_key=True, index=True)
    name = Column(String(16), unique=True, nullable=False)
    price = Column(Float, default=0)
    yearly_price = Column(Float, default=0)
    max_devices = Column(Integer, default=30)
    features = Column(String(512), default="")  # JSON 数组字符串
    hotels = Column(Integer, default=0)
    color = Column(String(16), default="#3B82F6")


class Device(Base):
    __tablename__ = "devices"
    id = Column(String(32), primary_key=True, index=True)
    hotel_id = Column(String(16), index=True, nullable=False)
    name = Column(String(64), default="")
    os = Column(String(128), default="")
    fingerprint = Column(String(128), default="")
    last_active = Column(String(32), default="")
    status = Column(String(16), default="在线")  # 在线/离线/已禁用/已解绑/已迁移（软删保留痕迹）
    created_at = Column(String(32), default="")  # 首次激活时间（P1-2 激活速度检测用）
    # P0-1 自助换机/解绑：解绑痕迹（软删，可后台恢复）
    unbound_at = Column(String(32), default="")
    unbound_by = Column(String(16), default="")  # self=客户自助 / admin=服务商 / migration=同名迁移


class RenewOrder(Base):
    """P0-2 续期自助化订单（状态机：待支付→已支付→已生效；待支付→已关闭/待确认→已生效/已驳回）"""

    __tablename__ = "renew_orders"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(32), unique=True, nullable=False, index=True)  # RO-YYYYMMDD-xxxxxx
    target_type = Column(String(8), default="hotel")  # hotel / group
    target_id = Column(String(16), index=True, nullable=False)
    target_ids = Column(Text, default="")  # 集团批量单：门店 ID 逗号分隔；单店单同 target_id
    target_name = Column(String(128), default="")
    plan = Column(String(16), default="")  # 续期套餐（升级时为目标套餐）
    months = Column(Integer, default=12)
    amount = Column(Float, default=0)  # 服务端计算，客户端只展示
    channel = Column(String(16), default="transfer")  # transfer=对公凭证 / wxpay=微信支付（预留）
    status = Column(String(16), default="待支付")  # 待支付/已支付/已生效/待确认/已驳回/已关闭
    proof_no = Column(String(64), default="")  # 对公汇款回单号
    proof_note = Column(String(256), default="")
    proof_file = Column(String(256), default="")  # 回单图片落盘路径（uploads/renew_proof/）
    paid_at = Column(String(32), default="")
    confirmed_by = Column(String(64), default="")  # 后台确认人（对公）
    reject_reason = Column(String(256), default="")
    created_at = Column(String(32), default="")
    closed_at = Column(String(32), default="")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")  # 可存企微推送记录等长内容


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    operator = Column(String(64), default="")
    action = Column(String(64), default="")
    target = Column(String(128), default="")
    detail = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class LLMCallLog(Base):
    """大模型调用审计 —— 集团/子酒店/管理端经服务端网关的每次 LLM 调用全量留痕"""

    __tablename__ = "llm_call_logs"
    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(16), default="")   # group=集团总端 / hotel=门店客户端 / admin=管理后台
    source_id = Column(String(32), default="")     # 集团码或酒店ID
    source_name = Column(String(128), default="")
    user_id = Column(String(64), default="")       # 指令发起人（企微 userid / 后台账号）
    scene = Column(String(64), default="")         # 场景：告警解读/对话查询/处置建议等
    user_input = Column(Text, default="")          # 用户原始输入（脱敏后）
    model_input = Column(Text, default="")         # 送入模型的提示词（脱敏后）
    model_output = Column(Text, default="")        # 模型输出（脱敏后）
    model_name = Column(String(64), default="")
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(16), default="success")  # success / error
    error = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
