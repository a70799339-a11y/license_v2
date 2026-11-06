# License 授权管理系统（license-v2）

面向 AI 酒店管理软件的 **License 授权与客户运营平台**：服务商在后台管理酒店客户/授权/风控，酒店通过桌面客户端激活授权并与服务端保持同步，集团总部可通过「集团总端」统一查看和管理旗下门店。

> 版本：ver1.0.0 ｜ 技术栈：FastAPI + PostgreSQL + Vue3 + Element Plus + Tauri (Rust)

---

## 一、产品架构

```
┌──────────────────────────────────────────────────────┐
│  服务端（部署在服务商自己的服务器上）                    │
│  ├─ FastAPI 后端 + PostgreSQL（授权签发/心跳同步/数据）  │
│  └─ 管理后台 Web（运营团队：客户/授权/风控/集团/设置）    │
└────────────┬───────────────────────┬─────────────────┘
             │  HTTPS 心跳/激活/同步   │
   ┌─────────┴─────────┐   ┌─────────┴──────────┐
   │  门店客户端        │   │  集团总端客户端     │
   │  （酒店电脑）      │   │  （集团总部电脑）   │
   │  只管理自己        │   │  统管旗下全部门店   │
   └───────────────────┘   └────────────────────┘
```

| 端 | 使用者 | 说明 |
|---|---|---|
| **管理后台** | 服务商运营团队 | 客户管理、授权签发/吊销、套餐、AI 风控、到期续期、集团管理、用户权限、安全设置 |
| **门店客户端** | 各酒店 | 激活授权、查看本店授权/设备/到期、离线宽限运行、自动心跳同步 |
| **集团总端客户端** | 集团总部 | 集团 KPI、旗下门店一览、批量续期、跨店告警（数据隔离：只见本集团） |

**同一客户端 exe 双形态**：激活码 `GP-xxxx` → 集团总端；门店授权码（`LIC-xxxx`/`HT-xxxx`）→ 门店端。

## 二、核心功能

### 授权与安全
- **Ed25519 签名 License**：服务端私钥签发、客户端验签通过才落盘，字段篡改即失效
- **设备指纹绑定**：一机一店，凭证拷贝到其他机器心跳直接拒绝（401）
- **AES-256-GCM 加密存储** + **反调试门禁**（检测调试器/注入即拒绝激活）+ 字符串混淆 + 符号剥离
- **私钥不入库**：KeyProvider 抽象（文件密钥库/环境变量/云 KMS 预留接口）
- **离线宽限期**（默认 72 小时，后台可配）：离线宽限内正常用 → 超时功能禁用 → 联网自动恢复
- **心跳同步**：吊销/到期/停用/自动续期/新版本提示即时下发客户端

### 总/子管理（集团）
- 集团码（GP-xxxx）激活总端，查看/续期旗下门店，数据严格隔离
- 后台集团管理：创建集团、划入门店、集团级批量续期

### 规模化（支撑 1000+ 家）
- 服务端分页 + 客户表格虚拟滚动（el-table-v2）
- 告警按规则聚合视图
- RBAC 三角色：admin（全部）/ operator（运营）/ viewer（只读，按钮隐藏+后端 403）
- **企业微信机器人**：高危告警/日报推运营群与集团群（数据隔离）、企微指令（状态/告警/续期二次确认）——见 docs/05

## 三、快速体验（本机演示环境）

前提：Windows + Docker Desktop（或 WSL2 Ubuntu 24.04）。

```bash
# 1. 启动 PostgreSQL + 后端 + Nginx（WSL 内 systemd）
wsl -d Ubuntu-24.04 -u root -e bash -c "systemctl start postgresql license-backend nginx"
# 防 WSL 空闲休眠（重要）
wsl -d Ubuntu-24.04 -u root -e sleep infinity

# 2. 打开管理后台
#    http://localhost   账号 admin / admin123
#    其他角色：ops / ops123（运营）、viewer / viewer123（只读）

# 3. 启动客户端（或用 release/ 下的 NSIS 安装包安装）
client/src-tauri/target/release/hotel-client.exe
#    门店激活码：HT-0001 或 LIC-2026-xxxx ｜ 集团总端：GP-0001
```

## 四、部署到正式服务器

配置部署文档按**三类读者分册**：

| 你是谁 | 看哪本 |
|---|---|
| 我们（服务商）运维/运营 | **[docs/03-部署运维手册.md](docs/03-部署运维手册.md)**（服务端/企微/大模型/备份升级） |
| 集团总部管理员 | **[docs/06-集团总端配置指南.md](docs/06-集团总端配置指南.md)**（集团码激活/门店管理/企微通知） |
| 子酒店（门店）管理员 | **[docs/07-子酒店配置指南.md](docs/07-子酒店配置指南.md)**（授权码激活/日常使用/企微通知） |

服务端部署摘要如下（详见 docs/03）：

```bash
# 1) 后端
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
export DATABASE_URL=postgresql+psycopg2://user:pass@127.0.0.1:5432/license
export JWT_SECRET=<强随机串>            # 必改
export LICENSE_KEY_PROVIDER=file        # 或 env / kms（见 docs/02）
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2) 前端产物
npm run build && cp -r dist/* backend/static/

# 3) systemd + Nginx 参考 deploy/ 目录
```

**生产必做清单**：改 JWT_SECRET 与数据库密码 → Nginx 配 HTTPS → 收敛 CORS → 数据库每日备份 → 私钥文件权限 600。

## 五、客户端构建与分发

```bat
client\build.bat test       :: license-core 单元测试
client\build.bat release    :: 构建 hotel-client.exe
client\bundle.bat           :: 打 NSIS 安装包 → client/src-tauri/target/release/bundle/nsis/
```

- **NSIS 安装包**（酒店正式安装）：`release\酒店AI管理客户端_0.1.0_x64-setup.exe`
- **绿色版**：直接拷贝 `hotel-client.exe`
- **自检命令**：`hotel-client.exe --selftest HT-0001`（激活链路）/ `--selftest-offline`（离线宽限链路）
- 依赖：Rust stable (MSVC)、VS Build Tools、WebView2 运行时；crates.io 走 rsproxy 镜像
- 版本更新：后台 `settings.client_version` 发布新版号 → 客户端心跳自动提示更新

## 六、测试

```bash
BASE_URL=http://localhost python tests/run_tests.py   # 接口自动化 65 条
node tests/ui_test.cjs                                 # 浏览器 UI 自动化 38 条
```

当前状态：**接口 65/65（100%）**、UI 38/38、客户端自检与跨语言验签全部通过。
测试报告见 `output/.../License授权管理系统测试报告.docx`。

## 七、目录结构

```
license/
├─ backend/          # FastAPI 后端（API/签发/客户端同步/种子数据）
├─ src/              # 管理后台 Vue3 前端源码
├─ dist/             # 前端构建产物（拷入 backend/static 发布）
├─ client/           # 酒店桌面客户端（Tauri + Rust license-core）
│  ├─ src-tauri/     #   Rust 主进程 + license-core 校验核心
│  └─ dist/          #   客户端 UI
├─ deploy/           # systemd / Nginx 部署文件
├─ docs/             # 产品文档（功能/安全/运维/规模化）
├─ tests/            # 自动化测试（接口 / UI / 自检）
└─ release/          # 发布产物（NSIS 安装包）
```

## 八、文档索引

**部署配置（按读者分册）**

| 文档 | 读者 | 内容 |
|---|---|---|
| [docs/03-部署运维手册.md](docs/03-部署运维手册.md) | 我们（服务商） | 服务端部署、企微机器人、大模型接入、密钥/备份/升级、故障排查 |
| [docs/06-集团总端配置指南.md](docs/06-集团总端配置指南.md) | 集团总部 | 集团码激活、门店一览/批量续期、企微通知与机器人、离线宽限 |
| [docs/07-子酒店配置指南.md](docs/07-子酒店配置指南.md) | 子酒店 | 授权码激活、三个页签使用、企微通知、离线宽限、FAQ |

**产品与技术**

| 文档 | 内容 |
|---|---|
| [docs/01-系统功能说明.md](docs/01-系统功能说明.md) | 全功能清单、双形态客户端、同步机制、离线宽限、大模型/告警管道 |
| [docs/02-安全加固指南.md](docs/02-安全加固指南.md) | 签名体系、KeyProvider/KMS、脱敏、大模型审计、商业化加固清单 |
| [docs/04-P2规模化与权限说明.md](docs/04-P2规模化与权限说明.md) | 虚拟滚动、告警聚合、RBAC 权限矩阵、总/子管理实现 |
| [docs/05-企业微信机器人接入方案.md](docs/05-企业微信机器人接入方案.md) | 企微双场景、指令/日报/AI 问答、自动化测试策略 |

## 九、安全须知

- 签名私钥存 `backend/keys/`（已 gitignore，**绝不入库**）；生产建议接云 KMS（docs/02 有接入步骤）
- 防破解是提高破解成本而非绝对免疫；商业化上线前请完成 docs/02 的 P0 清单（代码签名、KMS、HTTPS）
- 默认账号仅限演示环境，生产必须修改全部密码
- 部署密钥（JWT_SECRET/数据库口令）经 `/opt/license/env.file` 注入（600 权限），缺失时服务拒绝启动；企微机器人 Secret 如曾离开过保密渠道请在企业微信后台轮换

## 十、版本历史

### v1.0.0（2026-11-06）—— 首个客户交付版本
- **企微 AI 问答准确性保障**：数据接地（到期/告警/设备/酒店/集团子酒店五类意图）、完整性兜底（漏项自动切数据直出）、单店/单集团范围收敛、北京时间展示、告警口径统一
- **大模型**：本地 qwen2.5:7b 发布主力（3B 备选），云端可切换；全量调用经审计网关留痕脱敏
- **生产加固**：部署密钥强随机注入（缺配置拒绝启动）、数据库口令轮换、每日自动备份（保留 30 天+恢复演练）、服务自监控（异常/恢复推企微）、nginx 超时 300s
- **管理后台**：告警/日志/设备时间统一北京时间显示
- **测试**：自动化回归 112 条（T1~T25），测试数据收尾自动清扫

### v0.2.x（2026-11-01 ~ 11-06）
- 安全加固/RBAC/虚拟滚动/NSIS 打包、集团总端、离线宽限期、实时告警管道、日志脱敏
- 企微机器人（webhook+长连接+AI 问答）、大模型审计网关、本地/云端双模型
- 数据接地+完整性兜底+统计基座、设备 ID 防撞、检索中提示
