#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2026-11-24 安全审计 9 条 issue 提交到 Gitee Issue 跟踪。"""
import json
import urllib.request

TOKEN = "69e020a24dea971727388e9a62b3edfc"
REPO = "william_song/license-v2"

def create(title, body):
    data = urllib.parse.urlencode({
        "access_token": TOKEN, "title": title, "body": body,
    }).encode()
    r = urllib.request.Request(f"https://gitee.com/api/v5/repos/{REPO}/issues", data=data, method="POST")
    with urllib.request.urlopen(r, timeout=30) as resp:
        d = json.loads(resp.read())
    return d.get("number"), d.get("html_url")

import urllib.parse

ISSUES = [
    ("[安全][严重] S1 客户端信任服务端下发的公钥，License 签名可被伪造",
     """## 描述
激活时验签公钥取自响应 `signed_license.public_key`，未内置客户端。攻击者伪造服务端（hosts/中间人，部署仅 HTTP）可自签任意到期日 License 并自带公钥，Ed25519 形同虚设。

## 位置
`client/src-tauri/src/main.rs` activate_core 验签块

## 复现
搭假服务端返回自签 payload+攻击者公钥 → 客户端激活成功

## 修复建议
编译期内置公钥（`const PINNED_PUBKEY`），忽略响应内公钥；支持多公钥轮换

## 验收断言
真机：服务端下发的公钥被替换时激活失败

## 根因
docs/11 §一#1（信任链当实现细节）｜核验：属实（代码坐实）"""),
    ("[安全][严重] S2 凭客户编号即可激活，编号连续可枚举且无频率限制",
     """## 描述
激活支持用 `Hotel.code`（HT-0001 顺序生成）当凭据，接口无限流，可逐个枚举用别家编号激活自己设备（还可经客户端 API 读该店资料/设备/告警）。

## 位置
`backend/app/client_api.py` activate() 编号兜底分支

## 复现
POST /api/client/activate {"licenseCode":"HT-xxxx"} + 新指纹 → 200（已现场复现）

## 修复建议
新指纹只允许授权码激活（编号仅对本店既有指纹开放=重装便利保留）；激活接口按指纹/IP 限流+连续失败锁定

## 验收断言
T40：新指纹+编号激活 403/429；既有指纹+编号激活 200；连续失败 N 次 429

## 根因
docs/11 §一#2（缺滥用场景设计）｜核验：属实（现场复现）"""),
    ("[安全][严重] S3 集团端可免付款直接续期全部门店（group_renew）",
     """## 描述
集团总端调 `POST /api/client/group/renew` 直接把旗下门店 expire_at 改为 2027-12-31（写死），无订单/付款/审批——绕过 P0-2 续期付款流。

## 位置
`backend/app/client_api.py` group_renew()

## 修复建议
改为只创建续期订单（走订单流→运营确认→生效），客户端不能直接改到期日

## 验收断言
T40：group_renew 不再直接改 expire_at，返回订单号

## 根因
docs/11 §一#3（加新不审旧）｜核验：属实（代码坐实）"""),
    ("[安全][高] S4 不带指纹请求头可跳过设备指纹校验（13 处）",
     """## 描述
全部 `if fingerprint and fingerprint != ctx.fingerprint` 判断在缺头时放行——token 拷到别的机器不发指纹头即可心跳/查资料/解绑，告警也不触发。

## 位置
`backend/app/client_api.py` 13 处同款判断

## 复现
带 token 不带头调 heartbeat → 200（已现场复现）；错指纹 → 401

## 修复建议
统一依赖：`not fingerprint or mismatch` 一律 401；抽公共函数禁复制粘贴

## 验收断言
T40：无指纹头调 heartbeat/profile/devices 全部 401

## 根因
docs/11 §一#4（fail-open+复制粘贴）｜核验：属实（现场复现）"""),
    ("[安全][高] S5 /api/settings 对只读用户泄露明文密钥",
     """## 描述
GET /api/settings 只滤 `_` 前缀键，llm_cloud_api_key、webhook 全地址明文返回；仅需登录（viewer 可读）。

## 位置
`backend/app/main.py` get_settings()

## 复现
viewer 登录 GET /api/settings → 42 键含明文 sk-…（已现场复现）

## 修复建议
敏感键白名单/打码；viewer 无权读全量配置（角色收紧）

## 验收断言
T40：viewer GET /api/settings 403 或不含 llm_cloud_api_key 明文

## 根因
docs/11 §一#5（黑名单+新键无审点）｜核验：属实（现场复现）"""),
    ("[安全][高] S6 默认弱口令账号自动创建且登录无限流",
     """## 描述
seed 幂等创建 admin/admin123、ops/ops123、viewer/viewer123；登录无频率限制/锁定，生产忘改口令即可被登录。

## 位置
`backend/app/seed.py`；auth 登录

## 修复建议
初始口令环境变量化；登录按账号+IP 限流、失败锁定

## 验收断言
T40：连续失败登录 N 次 429

## 根因
docs/11 §一#6（原型便利带进生产）｜核验：属实"""),
    ("[安全][中] S7 部署仅 HTTP 无 TLS",
     """## 描述
deploy/nginx.conf 仅 listen 80，token/授权码/License 明文传输；配合 S1 可中间人。

## 位置
deploy/nginx.conf

## 修复建议
443+证书、80 跳转、HSTS；客户端拒绝 http 地址

## 验收断言
nginx 监听 443 且 http 强制跳转（需域名/证书——外部依赖）

## 根因
docs/11 §一#7（生产硬化缺失）｜核验：属实"""),
    ("[安全][中] S8 CORS allow_origins=* 且 allow_credentials=True",
     """## 描述
main.py CORS 全开+带凭证，注释「生产收敛」未做配置化。注：浏览器规范下 * +credentials 不带 cookie，实际利用受限，风险低于原描述。

## 位置
`backend/app/main.py` CORSMiddleware

## 修复建议
CORS_ORIGINS 环境变量白名单，默认不反射任意来源

## 验收断言
未配置时无 Access-Control-Allow-Origin: *（或来自白名单）

## 根因
docs/11 §一#8（安全债注释化）｜核验：部分属实（浏览器利用受限）"""),
    ("[安全][低] S9 密码哈希 == 比较 + 管理端/客户端 token 共钥无显式边界",
     """## 描述
verify_password 用 == 比较（时序侧信道，理论低危）；管理端/客户端 token 共用 JWT_SECRET。client 侧已有 typ 校验，admin 侧 get_current_user 未拒 typ=client（碰巧被用户表查询挡住，非显式保证）。

## 位置
`backend/app/auth.py`

## 修复建议
hmac.compare_digest；get_current_user 显式拒 client token；（可选）拆两把密钥

## 验收断言
T40：client token 调 /api/settings → 401

## 根因
docs/11 §一#9（加新不审旧/半边落地）｜核验：部分属实（typ 单边有）"""),
]

for title, body in ISSUES:
    num, url = create(title, body)
    print(f"#{num} {title[:34]}… → {url}")
