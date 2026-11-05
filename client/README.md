# 酒店AI管理客户端（路线 B：桌面客户端 + Rust 校验核心）

## 目录结构

```
client/
  dist/                    # 客户端 UI（HTML/CSS/JS，与管理后台同一套设计 token）
  src-tauri/
    src/main.rs            # Tauri 壳：激活 / 心跳 / 更新检查 / 状态管理
    license-core/          # Rust 校验核心（独立 crate，便于后续加固混淆编译）
      src/lib.rs           #   设备指纹 / Ed25519 验签 / AES-GCM 加密存储 / 时钟回滚检测
      examples/cross_verify.rs  # 跨语言验签工具（校验 Python 签名）
    tauri.conf.json
  build.bat                # 构建脚本（自动加载 MSVC 环境）
```

## 构建

```bat
client\build.bat test      # license-core 单元测试
client\build.bat debug     # 调试构建
client\build.bat release   # 发布构建 → src-tauri\target\release\hotel-client.exe
```

依赖：Rust stable（MSVC）、VS Build Tools、WebView2 运行时。
crates.io 走 rsproxy 镜像（~/.cargo/config.toml 已配置）。

## 与后端同步更新

| 机制 | 说明 |
|---|---|
| 激活 | POST /api/client/activate —— 授权码 + 设备指纹绑定，返回 Ed25519 签名 License，客户端验签通过才落盘 |
| 心跳（60s 自动 + 手动） | POST /api/client/heartbeat —— 同步授权状态（吊销/到期/停用即时生效）、自动续期开关、服务端时间（防回滚） |
| 版本同步 | 心跳/检查更新返回 update_available + 下载地址；管理端改 settings.client_version 即可发布新版本提示 |
| 自助数据 | GET /api/client/profile —— 仅本酒店的设备清单/授权/告警 |

## 演示账号

- 服务端地址：http://localhost（WSL Ubuntu 24.04 部署）
- 授权码：LIC-2026-0001 或客户编号 HT-0001 ~ HT-0040
- 管理后台：admin / admin123
