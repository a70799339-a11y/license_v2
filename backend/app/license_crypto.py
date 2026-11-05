"""Ed25519 License 签发 —— 私钥经 KeyProvider 管理（默认文件密钥库，可切 KMS）

签发的 License 载荷为规范化 JSON（sort_keys + 紧凑分隔符 + 原始 UTF-8），
客户端用内置公钥验签，任何字段篡改都会导致验签失败。
公钥存 settings（公开信息）；私钥不入库（旧库存量自动迁移到文件密钥库）。
"""
import json
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .database import get_db
from .key_provider import get_key_provider
from .models import Setting

PUB_KEY = "_ed25519_public"
LEGACY_PRIV_KEY = "_ed25519_private"  # 历史版本存库的私钥，启动时迁移后删除


def _get_setting(db, key: str) -> Optional[str]:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def _migrate_legacy_key(db) -> Optional[str]:
    """老版本把私钥放 settings，升级后迁到 KeyProvider 并从库里删除"""
    legacy = _get_setting(db, LEGACY_PRIV_KEY)
    if not legacy:
        return None
    provider = get_key_provider()
    if provider.name() == "file":
        from .key_provider import KEY_DIR, KEY_FILE

        KEY_DIR.mkdir(parents=True, exist_ok=True)
        if not KEY_FILE.exists():
            KEY_FILE.write_text(legacy, encoding="utf-8")
            provider._restrict()
    row = db.query(Setting).filter(Setting.key == LEGACY_PRIV_KEY).first()
    db.delete(row)
    db.commit()
    return legacy


def ensure_keypair(db) -> tuple[str, str]:
    """返回 (private_hex, public_hex)；私钥来源为 KeyProvider，公钥存 settings"""
    _migrate_legacy_key(db)
    provider = get_key_provider()
    priv_hex = provider.get_private_key_hex()
    pub_hex = _get_setting(db, PUB_KEY)
    if not pub_hex or len(pub_hex) != 64:
        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
        pub_hex = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        _set_setting(db, PUB_KEY, pub_hex)
        db.commit()
    return priv_hex, pub_hex


def get_public_key_hex(db) -> str:
    _, pub_hex = ensure_keypair(db)
    return pub_hex


def canonical_payload(payload: dict) -> bytes:
    """规范化 JSON：sort_keys + 紧凑分隔符 + 原始 UTF-8（与 Rust serde_json 输出一致，
    ensure_ascii 必须为 False，否则中文被转义为 \\uXXXX 会导致跨语言验签失败）"""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_license(db, payload: dict) -> dict:
    """签发 License：返回 {payload, signature, algorithm, public_key}"""
    priv_hex, pub_hex = ensure_keypair(db)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    signature = private_key.sign(canonical_payload(payload))
    return {
        "payload": payload,
        "signature": signature.hex(),
        "algorithm": "ed25519",
        "public_key": pub_hex,
    }


def verify_license(payload: dict, signature_hex: str, public_key_hex: str) -> bool:
    """验签（自测/管理端校验用，客户端侧由 Rust 实现同样逻辑）"""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), canonical_payload(payload))
        return True
    except Exception:
        return False
