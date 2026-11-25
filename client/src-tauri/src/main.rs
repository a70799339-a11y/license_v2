//! 酒店AI管理客户端 —— Tauri 主进程
//! 薄壳：UI 交互在 WebView，敏感逻辑（指纹/验签/加密存储）在 license-core
//! 支持 `hotel-client.exe --selftest [授权码]`：用与界面按钮完全相同的代码路径跑激活/心跳自检
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::Mutex;

use license_core::{
    anti_debug::is_debugger_present, device_fingerprint, offline_lock_state,
    verify_license, ClientState,
};
use serde_json::{json, Value};
use tauri::State;

const CLIENT_VERSION: &str = "1.2.5";

/// S1：出厂固化 Ed25519 验签公钥——信任锚，禁止使用服务端响应携带的公钥验签
/// （响应公钥仅供比对告警；密钥轮换需随客户端版本更新本常量）
const PINNED_PUBKEY_HEX: &str =
    "bc841feba67ab9a8f751d61775c0f8b6cfe6e11b4f9ef98e497c521e05f47ea9";

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// 离线锁状态（以最后成功同步为基准，服务端可配宽限小时数）
fn lock_state(st: &ClientState) -> (bool, f64) {
    offline_lock_state(&st.last_sync, st.offline_grace_hours, now_unix())
}

struct AppState {
    fingerprint: String,
    inner: Mutex<ClientState>,
}

fn state_dir() -> PathBuf {
    let base = std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."));
    base.join("hotel-client")
}

/// 第三方校验状态文件目录：优先机器级 ProgramData（第三方服务可跨用户读取），否则回落 APPDATA
fn license_status_dir() -> PathBuf {
    if let Some(pd) = std::env::var_os("ProgramData") {
        let p = PathBuf::from(pd).join("hotel-client");
        if std::fs::create_dir_all(&p).is_ok() {
            return p;
        }
    }
    state_dir()
}

/// Unix 秒 → "YYYY-MM-DD HH:MM:SS"（UTC；Howard Hinnant 日历算法，无第三方时间库）
fn fmt_utc(unix: i64) -> String {
    let (days, secs) = (unix.div_euclid(86400), unix.rem_euclid(86400));
    let (h, m, s) = (secs / 3600, (secs % 3600) / 60, secs % 60);
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mth = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if mth <= 2 { 1 } else { 0 };
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}", y, mth, d, h, m, s)
}

/// 推导第三方可见的状态字段（授权级事实 > 客户端锁定）
/// 锁定优先级：离线锁先于时间锁——离线过久时 serverTime 锚点已陈旧，
/// 用陈旧锚点判时间偏差会误报「时间锁定」（锚点新鲜时两路互斥，顺序无影响）。
fn derive_status_state(st: &ClientState) -> &'static str {
    let (offline_locked, _) = lock_state(st);
    let time_locked = license_core::time_deviation_state(now_unix(), &st.last_server_time).0;
    if st.status == "revoked" {
        "已吊销"
    } else if st.status == "expired" {
        "已过期"
    } else if st.status == "disabled" {
        "已停用"
    } else if offline_locked {
        "离线锁定"
    } else if time_locked {
        "时间锁定"
    } else {
        "正常" // valid / expiring
    }
}

/// 写第三方校验状态文件（Ed25519 签名载荷 + 推导状态），供同机第三方软件离线验签。
/// 信任模型：payload/signature 为服务端签发（不可伪造）；state 为客户端推导，
/// 与客户端自身感知同权（吊销感知滞后 = 心跳同步周期）。
fn write_license_status_file(st: &ClientState) {
    let signed = match &st.signed_license {
        Some(v) => v,
        None => return,
    };
    let out = json!({
        "payload": signed.get("payload").cloned().unwrap_or(Value::Null),
        "signature": signed.get("signature").cloned().unwrap_or(Value::Null),
        "public_key": signed.get("public_key").cloned().unwrap_or(Value::Null),
        "state": derive_status_state(st),
        "serverTime": st.last_server_time,
        "updatedAt": fmt_utc(now_unix()),
    });
    let path = license_status_dir().join("license_status.json");
    match std::fs::write(&path, serde_json::to_vec_pretty(&out).unwrap_or_default()) {
        Ok(_) => diag_log(&format!("status file written: {}", path.display())),
        Err(e) => diag_log(&format!("status file write fail: {e}")),
    }
}

/// 注销/清空状态时同步移除第三方校验文件（无文件 = 无授权，第三方按无效处理）
fn remove_license_status_file() {
    let _ = std::fs::remove_file(license_status_dir().join("license_status.json"));
}

/// 诊断日志：panic / 关键启动节点写入 crash.log（release 版无控制台，排查静默退出）
/// 落盘前对令牌类内容做脱敏（JWT 三段式 → ***TOKEN***）
fn diag_log(msg: &str) {
    let masked: String = {
        let mut out = String::with_capacity(msg.len());
        for seg in msg.split(' ') {
            if seg.starts_with("eyJ") && seg.matches('.').count() >= 2 {
                out.push_str("***TOKEN***");
            } else {
                out.push_str(seg);
            }
            out.push(' ');
        }
        out
    };
    let dir = state_dir();
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("crash.log"))
    {
        use std::io::Write;
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {}", masked.trim_end());
    }
}

fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        diag_log(&format!("PANIC: {info}"));
    }));
}

fn api_url(server: &str, path: &str) -> String {
    format!("{}{}", server.trim_end_matches('/'), path)
}

/// M2：服务端地址校验——必须为 http(s):// 完整格式（大小写不限），
/// 拒绝空白字符与内嵌账号密码（user@host 可疑伪装），返回 (规范化地址, 是否 https)
fn validate_server_url(url: &str) -> Result<(String, bool), String> {
    let u = url.trim().trim_end_matches('/').to_string();
    if u.is_empty() || u.chars().any(|c| c.is_whitespace()) {
        return Err("服务端地址格式无效".into());
    }
    let lower = u.to_lowercase();
    let (is_https, rest) = if lower.starts_with("https://") {
        (true, &u[8..])
    } else if lower.starts_with("http://") {
        (false, &u[7..])
    } else {
        return Err("服务端地址必须以 http:// 或 https:// 开头".into());
    };
    let host = rest.split('/').next().unwrap_or("");
    if host.is_empty() || host.contains('@') {
        return Err("服务端地址格式无效（主机名缺失或含可疑凭据）".into());
    }
    Ok((u, is_https))
}

fn http_post(url: &str, fingerprint: &str, token: &str, body: &Value) -> Result<Value, String> {
    let mut req = ureq::post(url)
        .set("Content-Type", "application/json")
        .set("X-Device-Fingerprint", fingerprint)
        .timeout(std::time::Duration::from_secs(10));
    if !token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {token}"));
    }
    let resp = req.send_string(&body.to_string()).map_err(map_ureq_err)?;
    resp.into_json::<Value>()
        .map_err(|e| format!("响应解析失败: {e}"))
}

fn http_get(url: &str, fingerprint: &str, token: &str) -> Result<Value, String> {
    let mut req = ureq::get(url)
        .set("X-Device-Fingerprint", fingerprint)
        .timeout(std::time::Duration::from_secs(10));
    if !token.is_empty() {
        req = req.set("Authorization", &format!("Bearer {token}"));
    }
    let resp = req.call().map_err(map_ureq_err)?;
    resp.into_json::<Value>()
        .map_err(|e| format!("响应解析失败: {e}"))
}

/// 把 404/5xx 响应体也带出来，便于定位（如 wslrelay 404 与后端 404 的区别）
fn map_ureq_err(e: ureq::Error) -> String {
    match e {
        ureq::Error::Status(code, resp) => {
            let body = resp.into_string().unwrap_or_default();
            let body: String = body.chars().take(200).collect();
            format!("HTTP {code}（{body}）")
        }
        other => format!("网络请求失败: {other}"),
    }
}

/// 网络抖动自愈：最多重试 3 次、间隔 2 秒（本机 WSL 测试环境虚拟机唤醒需要时间）
fn with_retry<T, F: Fn() -> Result<T, String>>(label: &str, f: F) -> Result<T, String> {
    let mut last = String::new();
    for i in 0..3 {
        match f() {
            Ok(v) => return Ok(v),
            Err(e) => {
                last = format!("[{label} 第{}次] {e}", i + 1);
                diag_log(&last);
                if i < 2 {
                    std::thread::sleep(std::time::Duration::from_secs(2));
                }
            }
        }
    }
    Err(format!(
        "{last}。请确认服务端已启动（浏览器能打开 http://localhost）后重试"
    ))
}

/// 激活核心流程（界面按钮与 --selftest 共用）
fn activate_core(
    fp: &str,
    server_url: &str,
    license_code: &str,
    device_name: &str,
    st: &mut ClientState,
) -> Result<Value, String> {
    // 反调试门禁：调试/注入环境下拒绝激活与验签
    if is_debugger_present() {
        diag_log("anti-debug: 检测到调试环境，拒绝激活");
        return Err("检测到调试环境，激活被拒绝".into());
    }
    let body = json!({
        "licenseCode": license_code,
        "deviceName": device_name,
        "os_name": std::env::consts::OS,
        "clientVersion": CLIENT_VERSION,
    });
    let resp = with_retry("激活", || {
        http_post(&api_url(server_url, "/api/client/activate"), fp, "", &body)
    })?;

    // 先验签再落盘：签名不通过的 License 一律拒收
    let signed = resp
        .get("signed_license")
        .ok_or("服务端未返回 License 签名")?;
    let payload = signed.get("payload").ok_or("License 载荷缺失")?;
    let sig = signed
        .get("signature")
        .and_then(|v| v.as_str())
        .ok_or("License 签名缺失")?;
    // S1：只认内置公钥验签；响应携带的公钥仅作比对告警（防中间人整套替换）
    let resp_pub = signed.get("public_key").and_then(|v| v.as_str()).unwrap_or("");
    if !resp_pub.is_empty() && !resp_pub.eq_ignore_ascii_case(PINNED_PUBKEY_HEX) {
        diag_log("activate: 服务端公钥与内置公钥不一致，按内置公钥验签（密钥轮换需升级客户端）");
    }
    if !verify_license(payload, sig, PINNED_PUBKEY_HEX) {
        return Err("License 验签失败，拒绝激活".into());
    }

    let token = resp
        .get("token")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    let hotel = resp.get("hotel").cloned().unwrap_or(json!({}));
    let lic = resp.get("license").cloned().unwrap_or(json!({}));

    st.token = token;
    st.server_url = server_url.trim_end_matches('/').to_string();
    st.scope = resp
        .get("scope")
        .and_then(|v| v.as_str())
        .unwrap_or("hotel")
        .to_string();
    st.hotel_name = hotel
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    // F5 修复：套餐以授权级 plan 为准（酒店表 plan 与授权 plan 不一致时不再误导）
    st.plan = lic
        .get("plan")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            hotel
                .get("plan")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string()
        });
    st.expire_at = lic
        .get("expire_at")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    st.license_code = lic
        .get("code")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    st.status = lic
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("valid")
        .to_string();
    st.last_sync = resp
        .get("server_time")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    st.last_server_time = st.last_sync.clone();
    if let Some(g) = resp.get("offline_grace_hours").and_then(|v| v.as_u64()) {
        st.offline_grace_hours = g as u32;
    }
    st.signed_license = Some(signed.clone());
    let _ = st.save(fp, &state_dir());
    write_license_status_file(st);

    Ok(json!({
        "ok": true,
        "verified": true,
        "hotel": hotel,
        "license": lic,
        // P0-1 同名迁移提示透传
        "migration_hint": resp.get("migration_hint").cloned().unwrap_or(json!(false)),
        "migrate_target": resp.get("migrate_target").cloned().unwrap_or(json!(null)),
    }))
}

/// 心跳核心流程（界面按钮与 --selftest 共用）
/// 网络失败不报错：返回 offline=true 并按宽限期给出锁定状态（超时禁用，联网恢复）
fn heartbeat_core(fp: &str, st: &mut ClientState) -> Result<Value, String> {
    heartbeat_core_inner(fp, st, true)
}

fn heartbeat_core_inner(fp: &str, st: &mut ClientState, allow_reauth: bool) -> Result<Value, String> {
    if st.token.is_empty() {
        return Err("尚未激活".into());
    }
    // 客户端本机时钟回滚自检：本地时间早于上次服务端时间 → 上报异常（触发服务端「时间回滚」告警）
    let clock_anomaly = match license_core::parse_utc(&st.last_server_time) {
        Some(last_server) => now_unix() + 300 < last_server, // 允许 5 分钟时钟容差
        None => false,
    };
    let body = json!({ "clientVersion": CLIENT_VERSION, "clockAnomaly": clock_anomaly });
    let resp = http_post(
        &api_url(&st.server_url, "/api/client/heartbeat"),
        fp,
        &st.token,
        &body,
    );

    let resp = match resp {
        Ok(v) => v,
        Err(e) => {
            if license_core::classify_api_error(&e) == license_core::ApiErrorKind::Auth {
                // 凭证失效（签名/过期）：点「重新连接」无济于事——用存储的授权码静默重激活后重试一次
                diag_log(&format!("heartbeat auth invalid: {e}"));
                if allow_reauth && !st.license_code.is_empty() {
                    let (url, code, name) = (st.server_url.clone(), st.license_code.clone(), st.hotel_name.clone());
                    if activate_core(fp, &url, &code, &name, st).is_ok() {
                        diag_log("heartbeat: silent reauth ok, retrying heartbeat");
                        return heartbeat_core_inner(fp, st, false);
                    }
                    diag_log("heartbeat: silent reauth failed");
                }
                // 鉴权失效 ≠ 离线：不要套离线锁误导用户
                return Ok(json!({
                    "offline": false,
                    "auth_invalid": true,
                    "locked": false,
                    "grace_hours": st.offline_grace_hours,
                    "error": e,
                }));
            }
            // 真离线/网络故障：以最后一次成功同步为基准
            let (locked, remaining) = lock_state(st);
            diag_log(&format!(
                "heartbeat offline: locked={locked} remaining={remaining:.1}h ({e})"
            ));
            write_license_status_file(st); // 离线锁状态变化也要让第三方看得见
            return Ok(json!({
                "offline": true,
                "locked": locked,
                "remaining_hours": remaining,
                "grace_hours": st.offline_grace_hours,
                "last_sync": st.last_sync,
                "error": e,
            }));
        }
    };

    let server_time = resp
        .get("server_time")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    // F2 修复：锚点始终前移（不因时间异常卡死，校正后自然恢复）
    st.last_server_time = server_time.clone();
    // F3 修复：以服务端时间为基准判定本机时钟偏差——改快/回拨 >24h 只读锁定，≤24h 仅提示
    let (time_locked, time_anomaly) =
        license_core::time_deviation_state(now_unix(), &server_time);
    st.last_sync = server_time.clone();
    // F5 修复：随心跳刷新套餐（续期升级套餐后自动更新显示）
    if let Some(p) = resp.get("plan").and_then(|v| v.as_str()) {
        if !p.is_empty() {
            st.plan = p.to_string();
        }
    }
    if let Some(g) = resp.get("offline_grace_hours").and_then(|v| v.as_u64()) {
        st.offline_grace_hours = g as u32;
    }
    st.status = resp
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("valid")
        .to_string();
    if let Some(exp) = resp.get("expire_at").and_then(|v| v.as_str()) {
        st.expire_at = exp.to_string();
    }
    let _ = st.save(fp, &state_dir());
    write_license_status_file(st);

    let mut out = resp.clone();
    out["offline"] = json!(false);
    out["locked"] = json!(false);
    out["time_anomaly"] = json!(time_anomaly);
    out["time_locked"] = json!(time_locked);
    out["server_time"] = json!(server_time);
    out["grace_hours"] = json!(st.offline_grace_hours);
    Ok(out)
}

// ---------- Tauri 命令 ----------
#[tauri::command]
fn get_fingerprint(state: State<AppState>) -> String {
    state.fingerprint.clone()
}

#[tauri::command]
fn get_state(state: State<AppState>) -> Value {
    let s = state.inner.lock().unwrap();
    let (locked, remaining) = if s.token.is_empty() {
        (false, 0.0)
    } else {
        lock_state(&s)
    };
    json!({
        "activated": !s.token.is_empty(),
        "server_url": s.server_url,
        "scope": s.scope,
        "hotel_name": s.hotel_name,
        "plan": s.plan,
        "expire_at": s.expire_at,
        "license_code": s.license_code,
        "status": s.status,
        "last_sync": s.last_sync,
        "client_version": CLIENT_VERSION,
        "offline_grace_hours": s.offline_grace_hours,
        "offline_locked": locked,
        "offline_remaining_hours": remaining,
    })
}

#[tauri::command]
fn activate(
    state: State<AppState>,
    server_url: String,
    license_code: String,
    device_name: String,
) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let mut st = state.inner.lock().unwrap();
    activate_core(&fp, &server_url, &license_code, &device_name, &mut st)
}

#[tauri::command]
fn heartbeat(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let mut st = state.inner.lock().unwrap();
    heartbeat_core(&fp, &mut st)
}

#[tauri::command]
fn check_update(server_url: String) -> Result<Value, String> {
    http_get(&api_url(&server_url, "/api/client/version"), "", "")
}

#[tauri::command]
fn fetch_profile(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_get(&api_url(&server, "/api/client/profile"), &fp, &token)
}

#[tauri::command]
fn fetch_group_profile(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_get(&api_url(&server, "/api/client/group/profile"), &fp, &token)
}

/// 消息通知自助配置（门店/集团配自己的企微群 webhook）
#[tauri::command]
fn fetch_notify_config(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_get(&api_url(&server, "/api/client/notify/config"), &fp, &token)
}

#[tauri::command]
fn save_notify_config(
    state: State<AppState>,
    webhook_url: String,
    push_enabled: Option<bool>,
    push_time: Option<String>,
) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    let body = json!({
        "webhookUrl": webhook_url,
        "pushEnabled": push_enabled,
        "pushTime": push_time.unwrap_or_default(),
    });
    http_post(&api_url(&server, "/api/client/notify/config"), &fp, &token, &body)
}

#[tauri::command]
fn test_notify(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_post(
        &api_url(&server, "/api/client/notify/test"),
        &fp,
        &token,
        &json!({}),
    )
}

/// 立即推送今日简报（每日定时推送的手动触发）
#[tauri::command]
fn test_push_daily(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_post(
        &api_url(&server, "/api/client/notify/push-test"),
        &fp,
        &token,
        &json!({}),
    )
}

#[tauri::command]
fn group_renew(state: State<AppState>, hotel_ids: Vec<String>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        // 离线锁定时禁用业务操作（超时禁用）
        let (locked, remaining) = lock_state(&s);
        if locked {
            return Err(format!(
                "离线超过 {} 小时，功能已禁用。请连接网络后自动恢复（当前剩余 {remaining:.0} 小时不可用）",
                s.offline_grace_hours
            ));
        }
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    let body = json!({ "hotelIds": hotel_ids });
    with_retry("集团续期", || {
        http_post(
            &api_url(&server, "/api/client/group/renew"),
            &fp,
            &token,
            &body,
        )
    })
}

// ---------- P0-1 自助换机/解绑 ｜ P0-2 续期订单 ----------
#[tauri::command]
fn fetch_devices(state: State<AppState>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_get(&api_url(&server, "/api/client/devices"), &fp, &token)
}

#[tauri::command]
fn unbind_device(state: State<AppState>, device_id: String) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_post(
        &api_url(&server, "/api/client/devices/unbind"),
        &fp,
        &token,
        &json!({ "device_id": device_id }),
    )
}

#[tauri::command]
fn migrate_device(state: State<AppState>, old_device_id: String) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_post(
        &api_url(&server, "/api/client/devices/migrate"),
        &fp,
        &token,
        &json!({ "old_device_id": old_device_id }),
    )
}

#[tauri::command]
fn create_renew_order(
    state: State<AppState>,
    months: i64,
    plan: String,
    channel: String,
    hotel_ids: Vec<String>,
) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    let body = json!({ "months": months, "plan": plan, "channel": channel, "hotel_ids": hotel_ids });
    with_retry("续期下单", || {
        http_post(&api_url(&server, "/api/client/renew/order"), &fp, &token, &body)
    })
}

#[tauri::command]
fn get_renew_order(state: State<AppState>, order_no: String) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    http_get(
        &api_url(&server, &format!("/api/client/renew/order/{order_no}")),
        &fp,
        &token,
    )
}

#[tauri::command]
fn submit_renew_proof(
    state: State<AppState>,
    order_no: String,
    proof_no: String,
    note: String,
    image_base64: String,
) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let s = state.inner.lock().unwrap();
        (s.token.clone(), s.server_url.clone())
    };
    if token.is_empty() {
        return Err("尚未激活".into());
    }
    let body = json!({ "proof_no": proof_no, "note": note, "image_base64": image_base64 });
    http_post(
        &api_url(&server, &format!("/api/client/renew/order/{order_no}/proof")),
        &fp,
        &token,
        &body,
    )
}

#[tauri::command]
fn set_server(
    state: State<AppState>,
    server_url: String,
    confirm: Option<bool>,
) -> Result<Value, String> {
    // M2 三层收敛：① http(s) 格式校验 ② 非 HTTPS 须显式二次确认
    // ③ 换服清凭证（token/license_code 不得流向新地址，须重新激活）
    let (url, is_https) = validate_server_url(&server_url)?;
    if !is_https && !confirm.unwrap_or(false) {
        return Err("NEEDS_CONFIRM: 非 HTTPS 明文传输存在凭证窃听风险，需二次确认".into());
    }
    let fp = state.fingerprint.clone();
    let mut s = state.inner.lock().unwrap();
    let mut cleared = false;
    if s.server_url != url && (!s.token.is_empty() || !s.license_code.is_empty()) {
        *s = ClientState {
            server_url: url,
            ..Default::default()
        };
        remove_license_status_file(); // 第三方校验文件同步消失
        cleared = true;
    } else {
        s.server_url = url;
    }
    s.save(&fp, &state_dir()).map_err(|e| e.to_string())?;
    Ok(json!({ "cleared": cleared }))
}

#[tauri::command]
fn logout(state: State<AppState>) {
    let fp = state.fingerprint.clone();
    let mut s = state.inner.lock().unwrap();
    *s = ClientState {
        server_url: s.server_url.clone(),
        ..Default::default()
    };
    let _ = s.save(&fp, &state_dir());
    remove_license_status_file(); // 注销后第三方校验文件同步消失
}

/// 自检模式：与界面按钮同一代码路径，跑完整激活 + 心跳 + 自助数据流程
fn run_selftest(server_url: &str, license_code: &str) -> i32 {
    let fp = device_fingerprint();
    let mut st = ClientState::default();
    println!("== 客户端自检 ==");
    println!("指纹: {}…", &fp[..16.min(fp.len())]);
    println!("服务端: {server_url}");
    println!("授权码: {license_code}");

    println!("\n[1/4] 激活（含 Ed25519 验签）…");
    match activate_core(&fp, server_url, license_code, "模拟激活测试", &mut st) {
        Ok(v) => {
            let hotel = v
                .get("hotel")
                .and_then(|x| x.get("name"))
                .and_then(|x| x.as_str())
                .unwrap_or("-");
            let lic = v
                .get("license")
                .and_then(|x| x.get("code"))
                .and_then(|x| x.as_str())
                .unwrap_or("-");
            println!("      激活成功：{hotel} · {lic} · 验签通过");
        }
        Err(e) => {
            println!("      激活失败：{e}");
            diag_log(&format!("selftest activate fail: {e}"));
            return 1;
        }
    }

    println!("[2/4] 心跳同步…");
    match heartbeat_core(&fp, &mut st) {
        Ok(v) => {
            println!(
                "      状态={} 到期={} 自动续期={} 更新提示={}",
                v.get("status").unwrap_or(&json!("-")),
                v.get("expire_at").unwrap_or(&json!("-")),
                v.get("auto_renew").unwrap_or(&json!("-")),
                v.get("update_available").unwrap_or(&json!("-")),
            );
        }
        Err(e) => {
            println!("      心跳失败：{e}");
            diag_log(&format!("selftest heartbeat fail: {e}"));
            return 1;
        }
    }

    println!("[3/4] 自助数据…");
    let profile_path = if st.scope == "group" {
        "/api/client/group/profile"
    } else {
        "/api/client/profile"
    };
    match http_get(&api_url(&st.server_url, profile_path), &fp, &st.token) {
        Ok(v) => {
            if st.scope == "group" {
                let s = v.get("summary").cloned().unwrap_or(json!({}));
                println!(
                    "      本集团门店 {} 家、设备 {} 台、告警 {} 条",
                    s.get("memberCount").unwrap_or(&json!(0)),
                    s.get("deviceCount").unwrap_or(&json!(0)),
                    s.get("alertCount").unwrap_or(&json!(0)),
                );
            } else {
                let n = v.get("devices").and_then(|d| d.as_array()).map(|a| a.len()).unwrap_or(0);
                let a = v.get("alerts").and_then(|d| d.as_array()).map(|a| a.len()).unwrap_or(0);
                println!("      本酒店设备 {n} 台、告警 {a} 条");
            }
        }
        Err(e) => {
            println!("      自助数据失败：{e}");
            return 1;
        }
    }

    println!("[4/4] 持久化状态…");
    match ClientState::load(&fp, &state_dir()) {
        Some(s) if !s.token.is_empty() => println!("      本地加密状态已落盘（授权码 {}）", s.license_code),
        _ => {
            println!("      状态落盘校验失败");
            return 1;
        }
    }

    println!("\nSELFTEST-OK：客户端激活流程全部通过");
    0
}

/// 离线宽限自检：真实激活 → 切断网络 → 验证宽限/锁定/恢复
fn run_selftest_offline(server_url: &str, license_code: &str) -> i32 {
    let fp = device_fingerprint();
    let mut st = ClientState::default();
    println!("== 离线宽限自检 ==");

    print!("[1/4] 在线激活… ");
    if let Err(e) = activate_core(&fp, server_url, license_code, "离线自检", &mut st) {
        println!("失败：{e}");
        return 1;
    }
    println!("成功（宽限 {}h）", st.offline_grace_hours);

    // 切断网络：指向不可达端口
    st.server_url = "http://127.0.0.1:9".into();
    print!("[2/4] 离线心跳（应进入宽限期）… ");
    match heartbeat_core(&fp, &mut st) {
        Ok(v) => {
            let offline = v.get("offline").and_then(|x| x.as_bool()).unwrap_or(false);
            let locked = v.get("locked").and_then(|x| x.as_bool()).unwrap_or(true);
            let rem = v.get("remaining_hours").and_then(|x| x.as_f64()).unwrap_or(0.0);
            if offline && !locked && rem > 0.0 {
                println!("OK（剩余 {rem:.1} 小时可用）");
            } else {
                println!("异常：offline={offline} locked={locked} rem={rem}");
                return 1;
            }
        }
        Err(e) => {
            println!("失败：{e}");
            return 1;
        }
    }

    print!("[3/4] 模拟超时（回拨同步时间 100 小时）… ");
    st.last_sync = "2020-01-01 00:00:00".into(); // 远古时间 → 必然超时
    match heartbeat_core(&fp, &mut st) {
        Ok(v) => {
            let locked = v.get("locked").and_then(|x| x.as_bool()).unwrap_or(false);
            if locked {
                println!("OK（已锁定，功能禁用）");
            } else {
                println!("异常：应锁定但未锁定");
                return 1;
            }
        }
        Err(e) => {
            println!("失败：{e}");
            return 1;
        }
    }

    print!("[4/4] 恢复联网（自动解锁）… ");
    st.server_url = server_url.to_string();
    match heartbeat_core(&fp, &mut st) {
        Ok(v) => {
            let offline = v.get("offline").and_then(|x| x.as_bool()).unwrap_or(true);
            if !offline {
                println!("OK（同步恢复，锁定解除）");
            } else {
                println!("异常：仍离线");
                return 1;
            }
        }
        Err(e) => {
            println!("失败：{e}");
            return 1;
        }
    }

    println!("\nSELFTEST-OFFLINE-OK：离线宽限 → 超时锁定 → 联网恢复 全部通过");
    0
}

fn main() {
    install_panic_hook();

    // 反调试门禁（启动层）：被附加调试器直接退出
    if is_debugger_present() {
        diag_log("anti-debug: 启动时检测到调试环境，退出");
        std::process::exit(3);
    }

    // 自检模式：不进 GUI，直接跑与界面相同的核心流程
    let args: Vec<String> = std::env::args().collect();
    if let Some(pos) = args.iter().position(|a| a == "--selftest") {
        let code = args.get(pos + 1).cloned().unwrap_or_else(|| "HT-0001".into());
        let server = std::env::var("SELFTEST_SERVER").unwrap_or_else(|_| "http://localhost".into());
        let code = run_selftest(&server, &code);
        std::process::exit(code);
    }

    // 离线宽限自检：激活后切断服务端，验证宽限倒计时/超时锁定/重连恢复
    if let Some(pos) = args.iter().position(|a| a == "--selftest-offline") {
        let code = args.get(pos + 1).cloned().unwrap_or_else(|| "HT-0001".into());
        let code = run_selftest_offline("http://localhost", &code);
        std::process::exit(code);
    }

    diag_log("main: start");
    let fingerprint = device_fingerprint();
    diag_log(&format!(
        "main: fingerprint={}",
        &fingerprint[..16.min(fingerprint.len())]
    ));
    let loaded = ClientState::load(&fingerprint, &state_dir()).unwrap_or_default();
    diag_log("main: state loaded");
    if !loaded.token.is_empty() {
        write_license_status_file(&loaded); // 开机即提供第三方校验文件（不等首次心跳）
    }

    let run_result = tauri::Builder::default()
        .manage(AppState {
            fingerprint,
            inner: Mutex::new(loaded),
        })
        .invoke_handler(tauri::generate_handler![
            get_fingerprint,
            get_state,
            activate,
            heartbeat,
            check_update,
            fetch_profile,
            fetch_group_profile,
            group_renew,
            fetch_devices,
            unbind_device,
            migrate_device,
            create_renew_order,
            get_renew_order,
            submit_renew_proof,
            fetch_notify_config,
            save_notify_config,
            test_notify,
            test_push_daily,
            set_server,
            logout
        ])
        .build(tauri::generate_context!());
    match run_result {
        Ok(app) => {
            diag_log("main: app built, running event loop");
            app.run(|_app, event| {
                if let tauri::RunEvent::ExitRequested { .. } = event {
                    diag_log("event: ExitRequested");
                }
            });
            diag_log("main: event loop ended");
        }
        Err(e) => {
            diag_log(&format!("main: build error: {e}"));
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_state() -> ClientState {
        let now = fmt_utc(now_unix());
        ClientState {
            token: "t".into(),
            status: "valid".into(),
            last_sync: now.clone(),
            last_server_time: now,
            offline_grace_hours: 72,
            ..Default::default()
        }
    }

    #[test]
    fn fmt_utc_known_values() {
        assert_eq!(fmt_utc(0), "1970-01-01 00:00:00");
        assert_eq!(fmt_utc(1700000000), "2023-11-14 22:13:20");
    }

    #[test]
    fn status_state_license_facts_override_locks() {
        // 授权级事实（吊销/过期/停用）优先于客户端锁定
        let mut st = base_state();
        st.status = "revoked".into();
        assert_eq!(derive_status_state(&st), "已吊销");
        st.status = "expired".into();
        assert_eq!(derive_status_state(&st), "已过期");
        st.status = "disabled".into();
        assert_eq!(derive_status_state(&st), "已停用");
    }

    #[test]
    fn status_state_time_lock() {
        let mut st = base_state();
        st.last_server_time = "2099-01-01 00:00:00".into(); // 服务端时间被改快超 24h
        assert_eq!(derive_status_state(&st), "时间锁定");
    }

    #[test]
    fn status_state_offline_lock_and_normal() {
        let mut st = base_state();
        st.last_sync = fmt_utc(now_unix() - 86400 * 30); // 30 天前同步，远超宽限期
        st.last_server_time = st.last_sync.clone();
        assert_eq!(derive_status_state(&st), "离线锁定");
        let st2 = base_state(); // 刚同步 → 正常
        assert_eq!(derive_status_state(&st2), "正常");
    }

    // M2：服务端地址校验
    #[test]
    fn validate_server_url_ok() {
        assert_eq!(
            validate_server_url("https://lic.example.com/").unwrap(),
            ("https://lic.example.com".to_string(), true)
        );
        assert_eq!(
            validate_server_url("http://10.0.0.5:8000").unwrap(),
            ("http://10.0.0.5:8000".to_string(), false)
        );
        assert_eq!(
            validate_server_url("HTTPS://LIC.EXAMPLE.COM").unwrap(),
            ("HTTPS://LIC.EXAMPLE.COM".to_string(), true)
        );
    }

    #[test]
    fn validate_server_url_rejects_bad() {
        assert!(validate_server_url("lic.example.com").is_err()); // 无 scheme
        assert!(validate_server_url("ftp://x.com").is_err()); // 非 http(s)
        assert!(validate_server_url("https://user:pw@evil.com").is_err()); // 内嵌凭据伪装
        assert!(validate_server_url("https://a b.com").is_err()); // 空白
        assert!(validate_server_url("").is_err());
        assert!(validate_server_url("http://").is_err()); // 主机缺失
    }
}
