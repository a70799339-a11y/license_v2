#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_sample 自证：用临时密钥三类判定（正常/过期/篡改）验证示例脚本逻辑。
不依赖客户端落盘文件，纯逻辑验证。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent.parent / "tools" / "verify_sample" / "verify_license_status.py"

sk = Ed25519PrivateKey.generate()


def pub_hex_of(key):
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()


def sign(payload: dict, key) -> str:
    import json as _j
    canon = _j.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return key.sign(canon).hex()


pub_hex = pub_hex_of(sk)

base_payload = {
    "v": 1, "code": "HT-DEMO", "plan": "旗舰版",
    "expire_at": "2027-12-31 00:00:00", "hotel_id": "h1",
    "issued_at": "2026-01-01 00:00:00", "max_devices": 10,
    "features": [], "quotas": {"max_devices": 10, "max_rooms": 0},
}

cases = []
# 1) 正常
cases.append(("正常文件应通过", 0, {
    "payload": base_payload, "signature": sign(base_payload, sk),
    "public_key": pub_hex, "state": "正常",
    "serverTime": "2026-11-24 08:00:00", "updatedAt": "2026-11-24 08:00:01",
}))
# 2) 过期（签名有效但到期日早于锚点）
expired = dict(base_payload, expire_at="2025-01-01 00:00:00")
cases.append(("过期文件应拒绝", 1, {
    "payload": expired, "signature": sign(expired, sk),
    "public_key": pub_hex, "state": "正常",
    "serverTime": "2026-11-24 08:00:00", "updatedAt": "2026-11-24 08:00:01",
}))
# 3) 篡改（改到期日不改签名）
cases.append(("篡改载荷应验签失败", 1, {
    "payload": dict(base_payload, expire_at="2099-12-31 00:00:00"),
    "signature": sign(base_payload, sk),  # 签名还是旧载荷的
    "public_key": pub_hex, "state": "正常",
    "serverTime": "2026-11-24 08:00:00", "updatedAt": "2026-11-24 08:00:01",
}))
# 4) 状态非正常（签名有效）
cases.append(("状态非正常应拒绝", 1, {
    "payload": base_payload, "signature": sign(base_payload, sk),
    "public_key": pub_hex, "state": "已吊销",
    "serverTime": "2026-11-24 08:00:00", "updatedAt": "2026-11-24 08:00:01",
}))

fails = 0
with tempfile.TemporaryDirectory() as td:
    for i, (name, expect_code, data) in enumerate(cases):
        p = Path(td) / f"case{i}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SAMPLE), "--pubkey", pub_hex, str(p)],
            capture_output=True, text=True, timeout=30,
        )
        ok = r.returncode == expect_code
        print(f"[{'PASS' if ok else 'FAIL'}] {name} (exit={r.returncode}, expect={expect_code})")
        print("       " + (r.stdout + r.stderr).strip().replace("\n", " | ")[:120])
        if not ok:
            fails += 1

print(f"\n===== verify_sample 自证 {len(cases)-fails}/{len(cases)} =====")
sys.exit(1 if fails else 0)
