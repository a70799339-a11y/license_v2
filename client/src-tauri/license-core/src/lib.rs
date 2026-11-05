//! license-core —— License 校验核心（后续加固/混淆编译的独立单元）
//!
//! 职责：
//! 1. 设备指纹采集（Windows MachineGuid + 主机名 + 用户名 → SHA-256）
//! 2. Ed25519 验签（服务端签发的 License，任何字段篡改即失败）
//! 3. 许可证本地加密存储（AES-256-GCM，密钥派生自设备指纹）
//! 4. 时钟回滚检测（对比历史服务端时间）
pub mod anti_debug;

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use obfstr::obfstr;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// 应用盐值（obfstr 字符串混淆，防止二进制字符串扫描直接读出）
pub fn app_salt() -> Vec<u8> {
    obfstr!("hotel-license-client-v1").as_bytes().to_vec()
}

/// 规范化 JSON（与 Python json.dumps(sort_keys=True, separators=(",",":")) 一致）
pub fn canonical_json(value: &Value) -> Vec<u8> {
    serde_json::to_string(value).unwrap_or_default().into_bytes()
}

/// 设备指纹：SHA-256(MachineGuid + hostname + username)
pub fn device_fingerprint() -> String {
    let machine_guid = read_machine_guid();
    let hostname = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "unknown-host".into());
    let username = std::env::var("USERNAME").unwrap_or_else(|_| "unknown-user".into());
    let mut hasher = Sha256::new();
    hasher.update(machine_guid.as_bytes());
    hasher.update(b"|");
    hasher.update(hostname.as_bytes());
    hasher.update(b"|");
    hasher.update(username.as_bytes());
    hex::encode(hasher.finalize())
}

#[cfg(windows)]
fn read_machine_guid() -> String {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    hklm.open_subkey(r"SOFTWARE\Microsoft\Cryptography")
        .and_then(|k| k.get_value::<String, _>("MachineGuid"))
        .unwrap_or_else(|_| "no-machine-guid".into())
}

#[cfg(not(windows))]
fn read_machine_guid() -> String {
    "no-machine-guid".into()
}

/// Ed25519 验签 License 载荷
pub fn verify_license(payload: &Value, signature_hex: &str, public_key_hex: &str) -> bool {
    let (Ok(pub_bytes), Ok(sig_bytes)) = (
        hex::decode(public_key_hex.trim()),
        hex::decode(signature_hex.trim()),
    ) else {
        return false;
    };
    if pub_bytes.len() != 32 || sig_bytes.len() != 64 {
        return false;
    }
    let Ok(vk) = VerifyingKey::from_bytes(&pub_bytes.try_into().unwrap()) else {
        return false;
    };
    let Ok(sig) = Signature::from_slice(&sig_bytes) else {
        return false;
    };
    vk.verify(&canonical_json(payload), &sig).is_ok()
}

/// 由设备指纹派生本地存储密钥
fn derive_key(fingerprint: &str) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(app_salt());
    hasher.update(fingerprint.as_bytes());
    hasher.finalize().into()
}

/// 加密本地许可证/令牌存储
pub fn encrypt_blob(fingerprint: &str, plaintext: &[u8]) -> Result<Vec<u8>, String> {
    let key = derive_key(fingerprint);
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&key));
    // v0.x：nonce 为密钥派生常量（同机解密场景）；商业化前换随机 nonce + 文件头存储
    let mut nonce_bytes = [0u8; 12];
    nonce_bytes.copy_from_slice(&key[..12]);
    cipher
        .encrypt(Nonce::from_slice(&nonce_bytes), plaintext)
        .map_err(|e| format!("加密失败: {e}"))
}

pub fn decrypt_blob(fingerprint: &str, ciphertext: &[u8]) -> Result<Vec<u8>, String> {
    let key = derive_key(fingerprint);
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&key));
    let mut nonce_bytes = [0u8; 12];
    nonce_bytes.copy_from_slice(&key[..12]);
    cipher
        .decrypt(Nonce::from_slice(&nonce_bytes), ciphertext)
        .map_err(|e| format!("解密失败: {e}"))
}

/// 时钟回滚检测：新服务端时间不得早于历史记录
pub fn is_time_rollback(prev_server_time: &str, new_server_time: &str) -> bool {
    if prev_server_time.is_empty() {
        return false;
    }
    new_server_time < prev_server_time
}

/// 本地持久化状态
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct ClientState {
    pub token: String,
    pub server_url: String,
    #[serde(default)]
    pub scope: String, // hotel=单店 / group=集团总端
    pub hotel_name: String,
    pub plan: String,
    pub expire_at: String,
    pub license_code: String,
    pub status: String,
    pub last_sync: String,
    pub last_server_time: String,
    #[serde(default = "default_grace_hours")]
    pub offline_grace_hours: u32, // 离线宽限小时数（服务端下发，后台可配）
    pub signed_license: Option<Value>,
}

fn default_grace_hours() -> u32 {
    72
}

impl ClientState {
    pub fn save(&self, fingerprint: &str, dir: &std::path::Path) -> Result<(), String> {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        let json = serde_json::to_vec(self).map_err(|e| e.to_string())?;
        let enc = encrypt_blob(fingerprint, &json)?;
        std::fs::write(dir.join("state.bin"), enc).map_err(|e| e.to_string())
    }

    pub fn load(fingerprint: &str, dir: &std::path::Path) -> Option<ClientState> {
        let enc = std::fs::read(dir.join("state.bin")).ok()?;
        let dec = decrypt_blob(fingerprint, &enc).ok()?;
        serde_json::from_slice(&dec).ok()
    }

    pub fn clear(dir: &std::path::Path) {
        let _ = std::fs::remove_file(dir.join("state.bin"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    #[test]
    fn sign_and_verify_roundtrip() {
        let sk = SigningKey::from_bytes(&[7u8; 32]);
        let vk = sk.verifying_key();
        let payload: Value = serde_json::json!({
            "code": "LIC-1", "expire_at": "2027-01-01", "hotel_id": "h1", "v": 1
        });
        let sig = sk.sign(&canonical_json(&payload));
        assert!(verify_license(
            &payload,
            &hex::encode(sig.to_bytes()),
            &hex::encode(vk.to_bytes())
        ));
        // 篡改任一字段应当验签失败
        let tampered: Value = serde_json::json!({
            "code": "LIC-1", "expire_at": "2099-01-01", "hotel_id": "h1", "v": 1
        });
        assert!(!verify_license(
            &tampered,
            &hex::encode(sig.to_bytes()),
            &hex::encode(vk.to_bytes())
        ));
    }

    #[test]
    fn encrypt_decrypt_roundtrip() {
        let fp = "abc123";
        let data = b"hello license";
        let enc = encrypt_blob(fp, data).unwrap();
        assert_eq!(decrypt_blob(fp, &enc).unwrap(), data);
    }
}

/// 解析 "YYYY-MM-DD HH:MM:SS"（UTC）为 unix 秒
pub fn parse_utc(ts: &str) -> Option<i64> {
    let num = |s: &str| s.trim().parse::<i64>().ok();
    if ts.len() < 19 {
        return None;
    }
    let y = num(&ts[0..4])?;
    let mo = num(&ts[5..7])?;
    let d = num(&ts[8..10])?;
    let h = num(&ts[11..13])?;
    let mi = num(&ts[14..16])?;
    let se = num(&ts[17..19])?;
    // Howard Hinnant days_from_civil
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = if y2 >= 0 { y2 } else { y2 - 399 } / 400;
    let yoe = y2 - era * 400;
    let mp = (mo + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    Some(days * 86400 + h * 3600 + mi * 60 + se)
}

/// 离线宽限状态（纯函数）：以最后一次成功同步为基准，超过 grace_hours 即锁定功能。
/// 返回 (locked, 剩余可用小时数)。后台 settings 可配 grace_hours（默认 72）。
pub fn offline_lock_state(last_sync_utc: &str, grace_hours: u32, now_unix: i64) -> (bool, f64) {
    let Some(last) = parse_utc(last_sync_utc) else {
        // 无有效同步记录：视为未激活/异常，不在此处判定
        return (false, grace_hours as f64);
    };
    let elapsed_h = (now_unix - last) as f64 / 3600.0;
    let remaining = grace_hours as f64 - elapsed_h;
    (remaining <= 0.0, remaining.clamp(0.0, f64::MAX))
}

#[cfg(test)]
mod offline_tests {
    use super::*;

    #[test]
    fn parse_and_lock() {
        let t0 = parse_utc("2026-11-05 00:00:00").unwrap();
        assert_eq!(parse_utc("2026-11-05 00:01:00").unwrap(), t0 + 60);
        // 宽限期内
        let (locked, rem) = offline_lock_state("2026-11-05 00:00:00", 72, t0 + 71 * 3600);
        assert!(!locked);
        assert!((rem - 1.0).abs() < 0.01);
        // 刚好超时 -> 锁定
        let (locked, rem) = offline_lock_state("2026-11-05 00:00:00", 72, t0 + 72 * 3600);
        assert!(locked);
        assert_eq!(rem, 0.0);
        // 自定义时限（后台可配，如 24 小时）
        let (locked, _) = offline_lock_state("2026-11-05 00:00:00", 24, t0 + 25 * 3600);
        assert!(locked);
    }
}
