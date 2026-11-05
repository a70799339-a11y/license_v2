//! 酒店AI管理客户端 —— Tauri 主进程
//! 薄壳：UI 交互在 WebView，敏感逻辑（指纹/验签/加密存储）在 license-core
//! 支持 `hotel-client.exe --selftest [授权码]`：用与界面按钮完全相同的代码路径跑激活/心跳自检
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::Mutex;

use license_core::{
    anti_debug::is_debugger_present, device_fingerprint, is_time_rollback, offline_lock_state,
    verify_license, ClientState,
};
use serde_json::{json, Value};
use tauri::State;

const CLIENT_VERSION: &str = "0.2.0";

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
    let pub_key = signed
        .get("public_key")
        .and_then(|v| v.as_str())
        .ok_or("公钥缺失")?;
    if !verify_license(payload, sig, pub_key) {
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
    st.plan = hotel
        .get("plan")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
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

    Ok(json!({ "ok": true, "verified": true, "hotel": hotel, "license": lic }))
}

/// 心跳核心流程（界面按钮与 --selftest 共用）
/// 网络失败不报错：返回 offline=true 并按宽限期给出锁定状态（超时禁用，联网恢复）
fn heartbeat_core(fp: &str, st: &mut ClientState) -> Result<Value, String> {
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
            // 离线宽限：以最后一次成功同步为基准
            let (locked, remaining) = lock_state(st);
            diag_log(&format!(
                "heartbeat offline: locked={locked} remaining={remaining:.1}h ({e})"
            ));
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
    let time_anomaly = is_time_rollback(&st.last_server_time, &server_time);
    if !time_anomaly {
        st.last_server_time = server_time.clone();
    }
    st.last_sync = server_time.clone();
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

    let mut out = resp.clone();
    out["offline"] = json!(false);
    out["locked"] = json!(false);
    out["time_anomaly"] = json!(time_anomaly);
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

#[tauri::command]
fn group_renew(state: State<AppState>, hotel_ids: Vec<String>) -> Result<Value, String> {
    let fp = state.fingerprint.clone();
    let (token, server) = {
        let mut s = state.inner.lock().unwrap();
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

#[tauri::command]
fn set_server(state: State<AppState>, server_url: String) -> Result<(), String> {
    let fp = state.fingerprint.clone();
    let mut s = state.inner.lock().unwrap();
    s.server_url = server_url.trim_end_matches('/').to_string();
    s.save(&fp, &state_dir()).map_err(|e| e.to_string())
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
