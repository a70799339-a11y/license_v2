# 第三方 License 校验接口（本地状态文件 · 集成文档）

> 面向第三方软件开发者。你的软件与酒店AI管理客户端部署在同一台机器（或可访问其共享目录）时，可通过读取**签名状态文件**离线校验 License 有效性——无需网络请求、无需 API Key。
>
> 版本：v1.0（2026-11-24）｜ 适用客户端：≥ 1.2.3

---

## 1. 机制总览

客户端在**激活成功、每次心跳同步、状态变化、开机加载**时，会写出/刷新一个 JSON 状态文件：

| 项 | 值 |
|---|---|
| **首选路径** | `C:\ProgramData\hotel-client\license_status.json`（机器级，第三方服务可跨用户读取） |
| **回落路径** | `%APPDATA%\hotel-client\license_status.json`（ProgramData 无写权限时） |
| **更新时机** | 激活成功 / 心跳同步成功（默认随客户端心跳周期） / 离线锁状态变化 / 客户端启动 / 注销时**删除** |
| **签名算法** | Ed25519（签名对象为 license payload 的规范化 JSON） |

文件长这样：

```json
{
  "payload": {
    "v": 1,
    "license_id": "ln1234",
    "code": "HT-0001",
    "hotel_id": "h001",
    "hotel_name": "杭州西湖大酒店",
    "plan": "旗舰版",
    "max_devices": 150,
    "issued_at": "2026-01-01 00:00:00",
    "expire_at": "2027-12-31 00:00:00",
    "features": ["ai_risk", "group_portal"],
    "quotas": { "max_devices": 150, "max_rooms": 0 }
  },
  "signature": "…ed25519 签名 hex…",
  "public_key": "bc841feba67ab9a8f751d61775c0f8b6cfe6e11b4f9ef98e497c521e05f47ea9",
  "state": "正常",
  "serverTime": "2026-11-24 08:30:00",
  "updatedAt": "2026-11-24 08:30:02"
}
```

## 2. 校验三步（照做即可）

### 第 1 步：验签（判断「授权是真的」）

对 `payload` 做**规范化 JSON** 后验 Ed25519 签名。规范化规则（必须严格一致）：

- 键按字典序排序
- 紧凑分隔符（`","` `":"`，无空格）
- UTF-8 编码、非 ASCII 不转义

> ⚠️ 状态文件本身是美化排版的，**不能**直接拿文件原始字节验签，必须先解析出 payload 重新序列化。
>
> ⚠️ `public_key` 字段仅供比对参考，**请把你收到本文档时固化的公钥硬编码进你的程序**（见 §4）。只信文件里携带的公钥等于让替换文件的人自己给自己签发。

### 第 2 步：到期（判断「授权没过期」）

比较 `payload.expire_at` 与 `serverTime`（服务端时间锚点）。不要用本机时钟直接比——本机时钟可能被改；若本机时间与 `serverTime` 偏差超过 1 小时，建议提示用户校时。

### 第 3 步：状态（判断「客户端认为可用」）

`state` 必须为 `正常`。其他取值含义：

| state | 含义 | 你的软件建议 |
|---|---|---|
| `正常` | 有效（含即将到期但未过期） | 放行 |
| `已过期` | 授权过期 | 拒绝 |
| `已吊销` | 授权被吊销 | 拒绝 |
| `已停用` | 客户被停用 | 拒绝 |
| `时间锁定` | 本机时钟异常被锁 | 拒绝并提示校时 |
| `离线锁定` | 长期离线超宽限期被锁 | 拒绝并提示联网 |

**补充判定**：文件不存在、JSON 解析失败、验签失败 → 一律按**无授权**处理。

## 3. 信任语义（务必理解）

- `payload` / `signature` 由我方服务端签发，**不可伪造**（篡改任何字段验签即失败）。
- `state` 由客户端推导，与酒店AI管理客户端**自身感知同权**。这意味着：
  - **吊销感知滞后**：授权被吊销后，state 要等到客户端下一次心跳同步才会变为`已吊销`（滞后 ≤ 心跳同步周期）。此口径已与需求方确认（心跳同步级即可）。
  - 本机恶意用户理论上可以替换整个文件——但必须换掉 payload+签名才有效，而签名他伪造不了；换成本机另一次合法激活的授权则属于转租场景，由我方服务端共享检测告警覆盖。
- 时间锚点 `serverTime` 防本机改时钟绕过期判定。

## 4. 我方签发公钥（出厂固化）

```
bc841feba67ab9a8f751d61775c0f8b6cfe6e11b4f9ef98e497c521e05f47ea9
```

- 算法：Ed25519；上列为 32 字节公钥的 hex。
- 公钥轮换会提前公告并出新版本文档；接入方收到本文档即以上列为准。

## 5. 示例代码

完整可运行示例：`tools/verify_sample/verify_license_status.py`（依赖 `pip install cryptography`）。核心逻辑：

```python
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PINNED = "bc841feba67ab9a8f751d61775c0f8b6cfe6e11b4f9ef98e497c521e05f47ea9"

data = json.loads(open(r"C:\ProgramData\hotel-client\license_status.json",
                       encoding="utf-8").read())

# 1) 验签：规范化 JSON（键排序+紧凑）后验 Ed25519
payload = data["payload"]
canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")
pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PINNED))
pub.verify(bytes.fromhex(data["signature"]), canon)   # 失败抛 InvalidSignature

# 2) 到期：用服务端锚点比，不用本机时钟
assert data["serverTime"] < payload["expire_at"], "已过期"

# 3) 状态
assert data["state"] == "正常", f"不可用：{data['state']}"
```

C# / Java / Go 等价要点：序列化必须**键排序 + 紧凑分隔 + UTF-8 不转义**，签名 64 字节 hex，公钥 32 字节 hex。

## 6. 常见问题

| 问题 | 处理 |
|---|---|
| 文件不存在 | 客户端未安装/未激活/已注销 → 按无授权处理 |
| 文件很旧（`updatedAt` 远古） | 客户端长期未运行；`state` 可能仍是`正常`，请结合 `serverTime` 与你自己的业务容忍度判断（建议超 7 天提示） |
| 验签失败 | 文件被篡改或损坏 → 按无授权处理，并可向我方反馈 |
| 公钥不一致告警 | 文件被整体替换 → 按无授权处理 |
| 想要更强的实时性 | 联系我方追加服务端直查接口（需另行接入认证） |

## 7. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-24 | 首版（客户端 1.2.3 起提供状态文件） |
