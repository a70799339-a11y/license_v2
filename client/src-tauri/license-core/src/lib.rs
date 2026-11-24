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

    /// 第三方校验状态文件契约：payload/signature/public_key 三元组离线验签回环。
    /// state/serverTime/updatedAt 为客户端推导字段，不参与签名（篡改不影响验签，
    /// 第三方判「授权真伪」只看 payload 签名，判「可用性」结合 state 与到期时间）。
    #[test]
    fn status_file_contract_roundtrip() {
        let sk = SigningKey::from_bytes(&[9u8; 32]);
        let vk = sk.verifying_key();
        let payload: Value = serde_json::json!({
            "code": "HT-0001", "plan": "旗舰版", "expire_at": "2027-12-31",
            "hotel_id": "h1", "features": ["ai_risk"], "max_devices": 150
        });
        let sig_hex = hex::encode(sk.sign(&canonical_json(&payload)).to_bytes());
        let pub_hex = hex::encode(vk.to_bytes());
        // 模拟客户端写出的状态文件结构（与 main.rs write_license_status_file 一致）
        let status_file: Value = serde_json::json!({
            "payload": payload,
            "signature": sig_hex,
            "public_key": pub_hex,
            "state": "正常",
            "serverTime": "2026-11-23 10:00:00",
            "updatedAt": "2026-11-23 10:00:05",
        });
        // 第三方按文档三步校验：取三元组 → 验签
        let p = status_file.get("payload").unwrap();
        let s = status_file.get("signature").and_then(|v| v.as_str()).unwrap();
        let k = status_file.get("public_key").and_then(|v| v.as_str()).unwrap();
        assert!(verify_license(p, s, k), "状态文件 payload 应验签通过");
        // 载荷篡改（改到期日）→ 验签失败
        let tampered: Value = serde_json::json!({
            "code": "HT-0001", "plan": "旗舰版", "expire_at": "2099-12-31",
            "hotel_id": "h1", "features": ["ai_risk"], "max_devices": 150
        });
        assert!(!verify_license(&tampered, s, k), "篡改 payload 必须验签失败");
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

/// 接口错误分类：鉴权失效（重连无用，需重激活）vs 网络/离线（重连可恢复）
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum ApiErrorKind {
    Auth,
    Network,
}

pub fn classify_api_error(err: &str) -> ApiErrorKind {
    if err.contains("HTTP 401") || err.contains("HTTP 403") {
        ApiErrorKind::Auth
    } else {
        ApiErrorKind::Network
    }
}

/// P1-1 时间偏差判定（纯函数，F3 口径修正 2026-11-11）：
/// 以服务端时间为可信基准，检测本机时钟被调整：
/// - |本机 - 服务端| > 24h（改快或回拨）→ 只读锁定（locked=true）
/// - 回拨 >5 分钟 或 改快 >30 分钟（≤24h）→ 仅提示（anomaly=true）
/// - 每次心跳用当前偏差重新判定，无历史锚点 → 校正回正常即自动解锁（F2 根治锚点粘连）
pub fn time_deviation_state(local_now_unix: i64, server_time: &str) -> (bool, bool) {
    let Some(server) = parse_utc(server_time) else {
        return (false, false);
    };
    let dev = local_now_unix - server;
    let locked = dev.abs() > 24 * 3600;
    let anomaly = dev < -300 || dev > 30 * 60;
    (locked, anomaly)
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

#[cfg(test)]
mod lock_tests {
    use super::*;

    #[test]
    fn classify_auth_vs_network() {
        // 鉴权失效：重连无用，必须走重激活
        assert_eq!(
            classify_api_error("HTTP 401（{\"detail\":\"无效客户端凭证\"}）"),
            ApiErrorKind::Auth
        );
        assert_eq!(
            classify_api_error("HTTP 401（{\"detail\":\"客户端凭证已过期，请重新激活\"}）"),
            ApiErrorKind::Auth
        );
        assert_eq!(classify_api_error("HTTP 403（禁止访问）"), ApiErrorKind::Auth);
        // 网络/离线：重连可恢复
        assert_eq!(
            classify_api_error("网络请求失败: Connection refused"),
            ApiErrorKind::Network
        );
        assert_eq!(classify_api_error("HTTP 500（内部错误）"), ApiErrorKind::Network);
        assert_eq!(classify_api_error("响应解析失败: eof"), ApiErrorKind::Network);
    }

    #[test]
    fn time_deviation_thresholds() {
        // F3 口径：本机 vs 服务端偏差判定（服务端时间为基准）
        let s = "2026-11-10 12:00:00";
        let server = parse_utc(s).unwrap();
        // 正常同步偏差（±5 分钟容差内）→ 不提示不锁
        assert_eq!(time_deviation_state(server + 60, s), (false, false));
        assert_eq!(time_deviation_state(server - 60, s), (false, false));
        // 改快 ≤24h（2h）→ 仅提示不锁（清单 #14）
        assert_eq!(time_deviation_state(server + 2 * 3600, s), (false, true));
        // 回拨 ≤24h（2h）→ 仅提示不锁
        assert_eq!(time_deviation_state(server - 2 * 3600, s), (false, true));
        // 改快刚好不足 24h（23:59:59）→ 不锁
        assert_eq!(time_deviation_state(server + 24 * 3600 - 1, s), (false, true));
        // 改快超过 24h → 锁定（清单 #12）
        assert_eq!(time_deviation_state(server + 24 * 3600 + 1, s), (true, true));
        // 回拨超过 24h → 锁定
        assert_eq!(time_deviation_state(server - 24 * 3600 - 1, s), (true, true));
        // 校正回正常 → 不锁（F2：无锚点，自动解锁）
        assert_eq!(time_deviation_state(server, s), (false, false));
        // 解析失败 → 不误伤
        assert_eq!(time_deviation_state(server, "bad"), (false, false));
    }

    #[test]
    fn parse_utc_edges() {
        assert!(parse_utc("2026-11-05").is_none()); // 缺时间部分
        assert_eq!(
            parse_utc("2026-01-01 00:00:00").unwrap() + 86400 * 31,
            parse_utc("2026-02-01 00:00:00").unwrap()
        );
        // 闰日
        assert_eq!(
            parse_utc("2028-02-28 00:00:00").unwrap() + 86400,
            parse_utc("2028-02-29 00:00:00").unwrap()
        );
    }
}
