#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三方 License 校验示例（独立脚本，不依赖本项目代码）

用途：读取酒店AI管理客户端写出的 license_status.json，
     用我方分发的 Ed25519 公钥离线校验 License 真伪与有效性。

依赖：pip install cryptography
用法：python verify_license_status.py [状态文件路径]
      python verify_license_status.py --pubkey <64位hex公钥> <状态文件路径>

退出码：0 = License 有效；1 = 无效/验签失败；2 = 参数或文件错误
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# 我方签发公钥（出厂固化，勿用文件内携带的公钥替代——防公钥与载荷被一并替换）
PINNED_PUBLIC_KEY_HEX = (
    "bc841feba67ab9a8f751d61775c0f8b6cfe6e11b4f9ef98e497c521e05f47ea9"
)

# 状态文件默认路径（客户端写入位置）
DEFAULT_PATH = Path(r"C:\ProgramData\hotel-client\license_status.json")

# 允许本机时钟与 serverTime 锚点的偏差（秒）；超出则提示校时但不直接判无效
CLOCK_TOLERANCE_SEC = 3600


def canonical_json(payload: dict) -> bytes:
    """与服务端/客户端签发算法一致的规范化 JSON（键排序+紧凑分隔+UTF-8）。
    注意：状态文件本身是美化排版的，验签前必须重新规范化，不能直接用文件原始字节。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify(status_file: dict, pinned_pub_hex: str = PINNED_PUBLIC_KEY_HEX) -> tuple[bool, str]:
    payload = status_file.get("payload")
    sig_hex = status_file.get("signature")
    file_pub_hex = (status_file.get("public_key") or "").strip()
    state = status_file.get("state") or ""
    server_time = status_file.get("serverTime") or ""

    if not payload or not sig_hex:
        return False, "文件缺少 payload/signature"
    if file_pub_hex.lower() != pinned_pub_hex.lower():
        return False, "文件内公钥与出厂固化公钥不一致（疑似文件被替换）"

    # 第 1 步：Ed25519 验签（授权真伪）
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned_pub_hex))
        pub.verify(bytes.fromhex(sig_hex), canonical_json(payload))
    except (InvalidSignature, ValueError):
        return False, "验签失败：载荷被篡改或签名无效"
    except Exception as e:
        return False, f"验签异常：{e}"

    # 第 2 步：到期时间（以服务端时间锚点为准）
    expire_at = payload.get("expire_at") or ""
    try:
        expire_dt = datetime.strptime(expire_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            expire_dt = datetime.strptime(expire_at, "%Y-%m-%d")
        except ValueError:
            return False, f"到期时间格式无法解析：{expire_at!r}"
    anchor = None
    if server_time:
        try:
            anchor = datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if anchor is not None:
        drift = abs((now - anchor).total_seconds())
        if drift > CLOCK_TOLERANCE_SEC:
            print(f"[提示] 本机时钟与服务端锚点偏差 {drift/3600:.1f} 小时，建议校时", file=sys.stderr)
    if anchor is not None and expire_dt < anchor:
        return False, f"License 已于 {expire_at} 到期（服务端锚点 {server_time}）"
    if anchor is None and expire_dt < now:
        return False, f"License 已于 {expire_at} 到期（本机时间判定，锚点缺失）"

    # 第 3 步：客户端状态（与客户端自身感知同权；吊销滞后=心跳同步周期）
    if state != "正常":
        return False, f"状态不可用：{state}（详见集成文档「信任语义」）"

    return True, f"有效：{payload.get('code', '?')} · {payload.get('plan', '?')} · 到期 {expire_at}"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--pubkey"]
    pinned = PINNED_PUBLIC_KEY_HEX
    if "--pubkey" in sys.argv:
        i = sys.argv.index("--pubkey")
        if i + 1 >= len(sys.argv):
            print("用法：--pubkey <64位hex>", file=sys.stderr)
            return 2
        pinned = sys.argv[i + 1]
        args = [a for j, a in enumerate(sys.argv[1:], 1) if j not in (i, i + 1)]
    path = Path(args[0]) if args else DEFAULT_PATH

    if not path.exists():
        print(f"状态文件不存在：{path}\n（客户端未激活/未运行，或未安装在本机 → 按无授权处理）", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"状态文件解析失败：{e}", file=sys.stderr)
        return 2

    ok, msg = verify(data, pinned)
    print(("✅ " if ok else "❌ ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
