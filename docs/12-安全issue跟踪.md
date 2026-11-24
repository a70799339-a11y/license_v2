# 安全 Issue 跟踪（S1~S9，2026-11-24 安全审计）

> 来源：外部安全审计 license_v2_security_issues.md（核实 8 实 1 部分实）。
> 根因复盘见 docs/11。修复必须伴随 T40+ 攻击者回归断言（防回潮）。
> 状态：🔴 未修 ｜ 🟡 修复中 ｜ ✅ 已修（附提交号）｜ ⏸ 外部依赖

| ID | 严重度 | 问题 | 状态 | 修复要点 | 验收断言 |
|---|---|---|---|---|---|
| S1 | 严重 | 客户端信任服务端下发的公钥，签名可伪造 | ✅ | 客户端内置 PINNED_PUBKEY（v1.2.4），验签忽略响应公钥 | 公钥被换时激活失败 |
| S2 | 严重 | 客户编号可激活/枚举/无限流 | ✅ | 激活限流（IP+指纹 15min/10 次）+编号仅既有指纹可用+新授权码随机 8hex | T40.3/40.4/40.5 |
| S3 | 严重 | group_renew 免付款直接续期 | ✅ | 改创建续期订单（RO-单号/待支付），运营确认后生效 | T12.4/12.5 新语义 |
| S4 | 高 | 不带指纹头跳过指纹校验（14 处） | ✅ | not fingerprint or mismatch 一律 401 | T40.1/40.2 |
| S5 | 高 | /api/settings 对 viewer 泄露明文密钥 | ✅ | admin/operator 角色收紧 + secret/api_key/token 键打码 | T40.6/40.7 |
| S6 | 高 | 默认弱口令 + 登录无限流 | ✅ | 登录限流（账号+IP 15min/10 次 429）+初始口令环境变量（ADMIN_PASSWORD 等） | T40.9 |
| S7 | 中 | 无 TLS | ⏸ | 443+证书+强制跳转（需域名/证书） | nginx 监听 443 |
| S8 | 中 | CORS * + credentials | ✅ | CORS_ORIGINS 白名单化；* 时不带凭证 | 代码审查 |
| S9 | 低 | == 比较 + token 边界半边落地 | ✅ | compare_digest + get_current_user 拒 typ=client | T40.8 |

## 备注
- Gitee Issues API 提交失败（token 缺 Issues 写权限，POST 404 "project or enterprise"）——本表为正式跟踪位；如需 Gitee 同步，给令牌加 issues 权限后可补建。
- S2 残留风险：存量顺序号授权码（LIC-2026-XXXX）仍可被限速枚举，建议运营逐步换发高熵码。
- S7 需要域名/证书到位后实施。
