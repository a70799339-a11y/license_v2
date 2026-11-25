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

## [LRN-20261124-001] correction

**Logged**: 2026-11-24T10:05:00+08:00
**Priority**: high
**Status**: resolved
**Area**: git

### Summary
.gitignore **不支持行尾注释**——`!tools/verify_sample/  # 注释` 会把注释算进模式，否定规则静默失效；且 git add 对被忽略路径只给 warning 继续跑，暂存清单里没有目标文件却没被发现，多绕了一轮。

### Details
gitignore 每一整行就是一个 pattern，`#` 只有在行首才是注释。改正模式应写成：
```
# 注释独立成行
tools/*
!tools/verify_sample/
```
（用 `tools/*` 而非 `tools/`，否则父目录被排除后无法再纳入子项。）另外 git add 目标被忽略时返回 warning 而非失败——**add 之后必须用 `git diff --cached --name-only` 核实目标真的进暂存了**，不能只看命令没报错。

### Suggested Action
改 .gitignore 后立即 `git check-ignore -v <目标文件>` 验证规则生效；git add 后查暂存清单核对目标文件在列。

### Metadata
- Source: conversation
- Related Files: .gitignore
- Tags: git, gitignore, silent-failure
- Pattern-Key: correction.gitignore_no_inline_comments
- Recurrence-Count: 1

---

## [LRN-20261124-002] correction

**Logged**: 2026-11-24T10:05:00+08:00
**Priority**: high
**Status**: resolved
**Area**: testing

### Summary
Git Bash→wsl.exe 链路里 `echo $?` 捕获的退出码失真（`python -c "sys.exit(1)"` 都显示 0）——连续 4 轮误信 shell 输出去「排查」一个不存在的产品 bug，正确做法是断言退出码必须在 Python/进程层捕获。

### Details
MSYS→Windows exe 的参数转义会吃掉/提前展开 `$?`，凡是「命令退出码」类断言穿过多层 shell（Git Bash→wsl→bash -c）都不可信。同理，管道 `| tail` 后的 `$?` 是 tail 的退出码。现象与根因之间隔了两层转义时，先怀疑捕获链再怀疑被测对象——用 subprocess.run().returncode 或直接把断言写进 Python 脚本一次性收口。

### Suggested Action
测试脚本里的退出码/输出断言一律 Python 进程层捕获（subprocess returncode）；shell 里的 `echo $?` 仅作人工参考，不作断言依据。

### Metadata
- Source: conversation
- Related Files: tools/verify_sample/verify_license_status.py, tests/client-accept/verify_sample_selftest.py
- Tags: testing, wsl, shell-escaping, verification
- Pattern-Key: correction.cross_shell_exit_code
- Recurrence-Count: 1

---

## [LRN-20261124-003] insight

**Logged**: 2026-11-24T16:10:00+08:00
**Priority**: high
**Status**: resolved
**Area**: security

### Summary
外部安全审计 9 条 issue 核实 8 实 1 部分实。根因复盘结论：功能测试全绿挡不住安全漏洞——所有问题长在「攻击者视角缺席」的盲区：验签回环测了原语没测信任链、指纹校验宽容写法复制 13 处、settings 黑名单让新密钥键静默暴露、新功能（续期订单）不回头审旧路径（group_renew 免付款）、安全债写在注释不进任务。

### Details
五个系统性病根：①验收标准没有攻击者（只测「能跑通」不测「滥用被挡」）②加新不审旧 ③fail-open 宽容默认带进生产 ④安全债无任务位 ⑤信任链（公钥分发/token 边界）当实现细节。典型案例：Ed25519 用得对但公钥随签名一起下发=签名白签；llm_config 自己脱敏而通用 get_settings 明文泄同一把 key——安全意识要制度化不能靠自觉。

### Suggested Action
防再发五机制：威胁三问（设计期）、攻击者回归段（测试期，本次 9 条固化 T40+）、发版安全五问闸门、新功能回头审旧路径、敏感输出白名单制。详见 docs/11-安全复盘与根因分析.md。

### Metadata
- Source: external-audit
- Related Files: docs/11-安全复盘与根因分析.md
- Tags: security, root-cause, threat-modeling
- Pattern-Key: insight.attacker_perspective_absent
- Recurrence-Count: 1

---

## [LRN-20261124-004] best_practice

**Logged**: 2026-11-24T16:10:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: development-process

### Summary
「加新不审旧」是本项目反复踩坑的单一最大来源（group_renew 免付款、恢复按钮误绑、指纹校验半边落地、settings 新密钥暴露均为同型）。任何新增「写敏感字段/校验敏感头」的能力，上线前必须列出所有同类旧路径并纳入测试。

### Details
三步固化：①新功能设计时问「这个字段/动作还有哪些入口」；②把旧入口列进该模块开发记录的「旧路径复核」栏；③回归加断言（旧入口要么同等校验要么明确封禁）。类似地，安全校验抽公共依赖（Depends）单点实现，禁止复制粘贴校验代码——13 处同款 `if fingerprint and ...` 即教训。

### Suggested Action
模块开发记录模板加「旧路径复核」必填栏；安全校验一律走公共依赖。

### Metadata
- Source: conversation
- Related Files: docs/09-开发记录.md
- Tags: process, regression, refactoring
- Pattern-Key: best_practice.review_old_paths
- Recurrence-Count: 4

---

## [LRN-20261125-001] insight

**Logged**: 2026-11-25T07:55:00+08:00
**Priority**: high
**Status**: resolved
**Area**: security

### Summary
二轮复审 N1~N13 全属实。核心病根是「修复只修报告点，不修攻击面」：S2 修了酒店编号枚举却漏了同类集团码 GP-0001（N1），S2 的限流键拼了客户端可控指纹头可换头绕过（N2），S3 堵了 API 却漏了企微同型入口（N5），S4 修了校验却让被校验凭据（指纹）在列表明文回显（N4）。

### Details
修复面完整性三步：①修一个凭据/字段漏洞时列出它的全部暴露面（谁能看到/谁能在哪用）②限流/审计等防护键禁止含客户端可控输入 ③状态枚举的每个值要有写入口与复活测试。注释里的「商业化前再改」（N8 nonce 固定）必须立任务，与 CORS 教训同型。

### Suggested Action
安全修复收尾 checklist 增加一项「攻击面盘点」：对被修凭据/字段穷举读写入口并逐一测试。

### Metadata
- Source: external-review
- Related Files: docs/11-安全复盘与根因分析.md
- Tags: security, attack-surface, rate-limit
- Pattern-Key: insight.fix_surface_not_point
- Recurrence-Count: 1

---

- [2026-11-25] SQLAlchemy 2.x 的 str(URL) 会把密码渲染成 ***：测试/脚本里拼接数据库 URL 后重解析会拿假密码连库（报 password authentication failed）。必须用 render_as_string(hide_password=False) 取全量 URL。—— t43_seed.py F1 白盒踩坑
- [2026-11-25] 安全修复的改动边界必须区分「被保护的」与「被误伤的」：SEED_DEMO_DATA 门禁把必需基础数据（套餐/设置）也关掉导致全新生产环境续期全瘫（F1）；登录 IP 聚合上限把共用出口 IP 的正常员工一起锁死（L1）。修复落地前走三受害面自检：全新环境、共用 IP 正常用户、防御自身性能（L2 每请求全字典遍历）。
