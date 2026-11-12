# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---
## [LRN-20261111-001] correction

**Logged**: 2026-11-11T15:45:00+08:00
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
时间锁（P1-1）实现口径漂移了设计：方案写「本机时钟回拨/改快 >24h → 锁」，实现却判「服务端时间之间回拨 >24h」，真实改钟完全不触发；且高水位锚点不回退导致锁粘死。

### Details
验收真机实测发现：本机时钟改快/改慢均不锁（改慢仅服务端告警）；锁遮罩只在时间基准回落>24h 出现，且回落后 last_server_time 高水位永不更新 → 点「重新连接」无法解锁，违背「恢复即解除」。根因：①判定函数输入用错了基准（server vs server，应为 local vs server）；②`if !time_anomaly { update anchor }` 的保护逻辑造成粘连。

### Suggested Action
已修复（license-core `time_deviation_state`：|本机-服务端|>24h 双向锁、回拨>5min/改快>30min 提示、每次心跳重判无锚点）。防再犯规则：**任何「锁定」逻辑必须成对设计「解锁」路径并写恢复测试；安全判定的基准（本地 vs 服务端）必须在函数注释里钉死公式**。

### Metadata
- Source: user_feedback（真机验收）
- Related Files: client/src-tauri/license-core/src/lib.rs, client/src-tauri/src/main.rs
- Tags: time-lock, design-drift, state-machine
- Pattern-Key: harden.lock_unlock_pairing
- Recurrence-Count: 1
- First-Seen: 2026-11-11
- Last-Seen: 2026-11-11

---

## [LRN-20261111-002] correction

**Logged**: 2026-11-11T15:45:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
异步状态机的 UI 必须有终态可见路径：续期订单提交凭证后客户端只显示静态「待确认」，后台驳回/生效用户完全看不见。

### Details
A2#10 验收不通过根因。订单状态机在服务端流转（待确认→已生效/已驳回/已关闭），客户端弹层提交后不轮询、重开弹层也不查历史单 → 用户永远不知道结果。同类问题曾在 401 事故出现（错误信息写在被遮罩盖住的元素）——**异步结果必须送达用户可见处**。

### Suggested Action
已修复（app.js 轮询 get_renew_order 5s，全终态文案含驳回原因；localStorage 记最近 order_no 供重开回显）。防再犯规则：**涉及远端状态流转的 UI，验收清单必须包含「远端状态变化后客户端如何反映」；提交成功 ≠ 流程结束**。

### Metadata
- Source: user_feedback（真机验收）
- Related Files: client/dist/app.js
- Tags: async-state, ux-visibility
- Pattern-Key: harden.async_status_visibility
- Recurrence-Count: 2
- First-Seen: 2026-11-10（401 错误隐身事故）
- Last-Seen: 2026-11-11

---

## [LRN-20261111-003] insight

**Logged**: 2026-11-11T15:45:00+08:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
版本号三处双源（tauri.conf.json / main.rs CLIENT_VERSION 常量 / 服务端 settings.client_version）漂移 → 客户端常驻误报「发现新版本」。

### Details
CLIENT_VERSION 常量是 0.2.1 而 tauri.conf 是 1.2.x、settings 是 1.1.0：update_available=clientVersion != latest 恒真，用户一直看到更新提示。版本号是双源/多源时必然漂移。

### Suggested Action
已修复（三处统一 1.2.2）。防再犯规则：**打包发版 checklist 必须含「版本号一致性检查」（tauri.conf == CLIENT_VERSION == settings.client_version）**；后续可做打包脚本自动校验。

### Metadata
- Source: conversation（验收发现 F4）
- Related Files: client/src-tauri/src/main.rs, backend/app/client_api.py, client/src-tauri/tauri.conf.json
- Tags: versioning, config-drift
- Pattern-Key: harden.single_source_version
- Recurrence-Count: 1

---

## [LRN-20261111-004] best_practice

**Logged**: 2026-11-11T15:45:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
客户端 UI 自动化验收的可复用打法：ctypes 点击（前台校验+DPI 换算）、PIL 截屏、WSL date -s 等价模拟时钟操纵、剪贴板粘贴输入。

### Details
沙箱禁 Add-Type；ctypes EnumWindows 回调须返回 c_int（c_bool 截断枚举）；GlobalAlloc/GlobalLock 须声明 c_void_p restype/argtypes（否则句柄截断崩溃）；125% DPI 下显示坐标×1.4=逻辑坐标；SetForegroundWindow 需 ALT 按键解锁+前台校验防点错窗口；Set-Date 需管理员，用 WSL date -s 操纵服务端时钟可在心跳视角等价模拟本机改钟。

### Suggested Action
工具已沉淀在 tests/client-accept/（shot.py/safe_click.py/type_text.py/winshow.py）。防再犯规则：**客户端发版前按 docs/09 附录 A 真机走一遍，脚本直接复用**。

### Metadata
- Source: conversation
- Related Files: tests/client-accept/
- Tags: ui-automation, tooling
- Pattern-Key: best_practice.client_acceptance_harness
- Recurrence-Count: 1

---
