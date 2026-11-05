"""签名私钥管理（KeyProvider 抽象）—— 私钥不再入库，商业化对接 KMS 只换 Provider

三种实现：
1. FileKeyProvider（默认）：私钥存 backend/keys/ 目录，权限收紧，数据库只存公钥指纹
2. EnvKeyProvider：从环境变量 LICENSE_SIGNING_KEY 读取（由外部 KMS/密钥管理系统注入）
3. KmsKeyProvider：对接云 KMS / HSM 的预留接口（腾讯云/阿里云 KMS），见 docs/02-安全加固指南.md

配置方法（环境变量 LICENSE_KEY_PROVIDER）：
- file（默认）| env | kms
"""
import os
from abc import ABC, abstractmethod
from pathlib import Path

KEY_DIR = Path(os.environ.get("LICENSE_KEY_DIR", Path(__file__).resolve().parent.parent / "keys"))
KEY_FILE = KEY_DIR / "ed25519.key"


class KeyProvider(ABC):
    """私钥提供者：签名时取私钥，验签只用公钥（公钥可公开分发）"""

    @abstractmethod
    def get_private_key_hex(self) -> str:
        """返回 32 字节 Ed25519 私钥的 hex（64 字符）"""

    @abstractmethod
    def name(self) -> str: ...


class FileKeyProvider(KeyProvider):
    """文件密钥库：私钥落盘于 keys/ed25519.key（首次自动生成），文件权限仅属主可读"""

    def name(self) -> str:
        return "file"

    def get_private_key_hex(self) -> str:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        if KEY_FILE.exists():
            return KEY_FILE.read_text(encoding="utf-8").strip()
        # 首次生成（调用方随后会写入公钥到 settings）
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization

        priv_hex = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()
        KEY_FILE.write_text(priv_hex, encoding="utf-8")
        self._restrict()
        return priv_hex

    @staticmethod
    def _restrict() -> None:
        try:
            os.chmod(KEY_FILE, 0o600)  # Windows 上为尽力而为；Linux 生产必生效
        except OSError:
            pass


class EnvKeyProvider(KeyProvider):
    """环境变量注入密钥（由外部密钥系统/KMS Agent 注入后使用）"""

    def name(self) -> str:
        return "env"

    def get_private_key_hex(self) -> str:
        key = os.environ.get("LICENSE_SIGNING_KEY", "").strip()
        if len(key) != 64:
            raise RuntimeError(
                "环境变量 LICENSE_SIGNING_KEY 缺失或长度错误（应为 64 字符 hex 的 Ed25519 私钥）"
            )
        return key


class KmsKeyProvider(KeyProvider):
    """云 KMS / HSM 预留实现 —— 私钥永不出 KMS，签名由 KMS 完成

    接入步骤（以腾讯云 KMS 为例）：
    1. 在 KMS 创建密钥对（Ed25519 或用软件密钥托管），记录 key_id
    2. pip install tencentcloud-sdk-python-kms（或厂商对应 SDK）
    3. 在本类 sign() 中调用 KMS 的签名 API，返回签名结果
    4. 环境变量 LICENSE_KEY_PROVIDER=kms 并配置 KMS_KEY_ID / 云凭证
    本地开发请用 file / env，勿把云凭证写进代码库。
    """

    def name(self) -> str:
        return "kms"

    def get_private_key_hex(self) -> str:
        raise RuntimeError(
            "KmsKeyProvider 为预留接口：请在 docs/02-安全加固指南.md 按厂商接入，"
            "或改用 LICENSE_KEY_PROVIDER=file / env"
        )


def get_key_provider() -> KeyProvider:
    kind = os.environ.get("LICENSE_KEY_PROVIDER", "file").strip().lower()
    if kind == "env":
        return EnvKeyProvider()
    if kind == "kms":
        return KmsKeyProvider()
    return FileKeyProvider()
