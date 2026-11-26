/* 酒店AI管理客户端 —— 渲染层，敏感逻辑在 Rust license-core */
const invoke = window.__TAURI__.core.invoke;

const $ = (id) => document.getElementById(id);
const STATUS_MAP = {
  valid: { text: "生效中", cls: "ok" },
  expiring: { text: "即将到期", cls: "warn" },
  revoked: { text: "已吊销", cls: "err" },
  expired: { text: "已过期", cls: "err" },
  disabled: { text: "已停用", cls: "err" },
};

let state = { activated: false, server_url: "http://localhost", scope: "hotel" };
let heartbeatTimer = null;
let gpSelected = new Set();
let offlineLocked = false;
let authInvalid = false; // 凭证失效（≠离线）：重连无用，需重新激活

/* ---------- 离线宽限 / 锁定 ---------- */
function applyOffline(locked, remaining, grace) {
  offlineLocked = !!locked;
  const banner = $("offline-banner");
  const overlay = $("lock-overlay");
  if (!state.activated) {
    banner.classList.add("hidden");
    overlay.classList.add("hidden");
    return;
  }
  if (locked) {
    banner.classList.remove("hidden");
    banner.classList.add("locked");
    $("offline-text").textContent = `离线超时，功能已禁用（宽限 ${grace} 小时）`;
    overlay.classList.remove("hidden");
    $("lock-text").textContent =
      `客户端已离线超过 ${grace} 小时（服务端配置的宽限时长），业务功能已锁定。`;
  } else if (remaining !== undefined && remaining < grace) {
    // 处于离线宽限期
    banner.classList.remove("hidden");
    banner.classList.remove("locked");
    $("offline-text").textContent = `离线运行中 · 剩余 ${Math.floor(remaining)} 小时可用（宽限 ${grace}h）`;
    overlay.classList.add("hidden");
  } else {
    banner.classList.add("hidden");
    overlay.classList.add("hidden");
  }
  $("sy-grace").textContent = `${grace ?? "--"} 小时`;
  $("sy-remain").textContent = locked ? "已禁用" : remaining === undefined ? "在线" : `${Math.floor(remaining)} 小时`;
}

function guardLocked() {
  if (offlineLocked) {
    alert("离线超时，功能已禁用。请连接网络后点「重新连接」恢复。");
    return true;
  }
  return false;
}

function showMsg(el, text, type) {
  el.textContent = text;
  el.className = `msg ${type}`;
}
function hideMsg(el) {
  el.className = "msg hidden";
}
function statusBadge(status) {
  const s = STATUS_MAP[status] || { text: status || "--", cls: "" };
  return `<span class="badge ${s.cls}">${s.text}</span>`;
}
function daysLeft(expireAt) {
  if (!expireAt) return "--";
  const diff = Math.ceil((new Date(expireAt) - new Date()) / 86400000);
  return diff > 0 ? `${diff} 天` : "已到期";
}

/* ---------- 视图切换 ---------- */
function switchView(view) {
  document.querySelectorAll(".menu-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  ["overview", "group", "devices", "sync"].forEach((v) => {
    $(`view-${v}`).classList.toggle("hidden", v !== view);
  });
  $("crumb").textContent =
    { overview: "授权概览", group: "集团概览", devices: "设备信息", sync: "同步状态" }[view] || view;
  if (view === "group") loadGroupProfile();
  if (view === "devices") loadDevices();
  if (view === "sync") {
    refreshSyncView();
    loadNotifyConfig();
  }
}

function applyMenuByScope() {
  const isGroup = state.scope === "group";
  // 集团总端：显示 集团概览/同步状态；单店：显示 授权概览/设备信息/同步状态
  document.querySelector('[data-view="overview"]').classList.toggle("hidden", isGroup);
  document.querySelector('[data-view="group"]').classList.toggle("hidden", !isGroup);
  document.querySelector('[data-view="devices"]').classList.toggle("hidden", isGroup);
}

/* ---------- 初始化 ---------- */
async function init() {
  state = await invoke("get_state");
  // 版本号单源显示：跟随 state.client_version（F4 防再犯）
  $("logo-version").textContent = `客户端 v${state.client_version || ""}`;
  const fp = await invoke("get_fingerprint");
  $("fp-short").textContent = fp.slice(0, 12) + "…";
  $("fp-full").textContent = fp;
  $("in-server").value = state.server_url || "http://localhost";
  $("in-server2").value = state.server_url || "http://localhost";

  if (!state.activated) {
    $("view-activate").classList.remove("hidden");
    ["overview", "group", "devices", "sync"].forEach((v) => $(`view-${v}`).classList.add("hidden"));
    document.querySelectorAll(".menu-item").forEach((b) => b.classList.add("hidden"));
  } else {
    $("view-activate").classList.add("hidden");
    document.querySelectorAll(".menu-item").forEach((b) => b.classList.remove("hidden"));
    applyMenuByScope();
    const home = state.scope === "group" ? "group" : "overview";
    switchView(home);
    if (home === "overview") renderOverview();
    applyOffline(state.offline_locked, state.offline_remaining_hours, state.offline_grace_hours);
    startHeartbeatLoop();
  }
}

function renderOverview() {
  $("hotel-line").textContent = state.hotel_name || "未激活";
  $("ov-plan").textContent = state.plan || "--";
  $("ov-status").innerHTML = statusBadge(state.status);
  $("ov-expire").textContent = state.expire_at || "--";
  $("ov-days").textContent = daysLeft(state.expire_at);
  $("ov-hotel").textContent = state.hotel_name || "--";
  $("ov-code").textContent = state.license_code || "--";
  $("ov-renew").textContent = state.auto_renew ? "已开启" : "未开启";
  $("ov-sync").textContent = state.last_sync || "--";
  renderRenewBanner();
}

/* ---------- 激活 ---------- */
$("btn-activate").addEventListener("click", async () => {
  if (guardLocked()) return;
  const server = $("in-server").value.trim();
  const code = $("in-code").value.trim();
  const name = $("in-name").value.trim();
  if (!server || !code) {
    showMsg($("activate-msg"), "请填写服务端地址与授权码", "err");
    return;
  }
  // M2：激活是服务端地址主入口——非 HTTPS 明文传输须二次确认（Rust 侧同门禁强制）
  const actIsHttps = server.toLowerCase().startsWith("https://");
  let actConfirm = false;
  if (!actIsHttps) {
    if (!window.confirm("⚠️ 非 HTTPS 明文传输，激活凭证与心跳数据可能被窃听。\n确定继续使用 HTTP？")) return;
    actConfirm = true;
  }
  $("btn-activate").disabled = true;
  showMsg($("activate-msg"), "正在激活并验签…", "warn");
  try {
    const res = await invoke("activate", {
      serverUrl: server,
      licenseCode: code,
      deviceName: name || "客户端",
      confirm: actConfirm,
    });
    // P0-1 同名迁移提示（重装系统场景）
    if (res.migration_hint && res.migrate_target) {
      const t = res.migrate_target;
      if (confirm(`检测到疑似重装的旧设备「${t.name}」（最近活跃 ${t.last_active || "未知"}），是否替换？\n替换后旧设备配额释放，本机接管。`)) {
        try {
          await invoke("migrate_device", { oldDeviceId: t.id });
        } catch (e) {
          alert(`迁移失败：${e}`);
        }
      }
    }
    const scopeText = res.scope === "group" ? "集团总端" : "门店";
    showMsg(
      $("activate-msg"),
      `激活成功（${scopeText}，License 验签通过）：${res.hotel.name} · ${res.license.plan}`,
      "ok"
    );
    state = await invoke("get_state");
    setTimeout(() => {
      authInvalid = false;
      offlineLocked = false;
      $("btn-lock-reconnect").textContent = "重新连接";
      $("lock-overlay").classList.add("hidden");
      $("view-activate").classList.add("hidden");
      document.querySelectorAll(".menu-item").forEach((b) => b.classList.remove("hidden"));
      applyMenuByScope();
      const home = state.scope === "group" ? "group" : "overview";
      switchView(home);
      if (home === "overview") renderOverview();
      startHeartbeatLoop();
    }, 800);
  } catch (e) {
    showMsg($("activate-msg"), String(e), "err");
  } finally {
    $("btn-activate").disabled = false;
  }
});

/* ---------- 心跳同步 ---------- */
async function doHeartbeat() {
  try {
    const res = await invoke("heartbeat");
    state = await invoke("get_state");
    if (res.offline) {
      // 离线宽限/锁定（不视为错误）
      applyOffline(res.locked, res.remaining_hours, res.grace_hours);
      return res;
    }
    if (res.auth_invalid) {
      // 凭证失效：静默重激活已失败——明确告知并引导重新激活（不是离线锁，重连无用）
      authInvalid = true;
      offlineLocked = true;
      $("offline-banner").classList.remove("hidden");
      $("offline-banner").classList.add("locked");
      $("offline-text").textContent = `凭证失效：${res.error || "鉴权失败"}`;
      $("lock-overlay").classList.remove("hidden");
      $("lock-text").textContent =
        `客户端凭证已失效（${res.error || "鉴权失败"}），自动重新激活未成功。请点下方按钮重新激活；若授权码也无法激活，请联系服务商。`;
      $("btn-lock-reconnect").textContent = "去重新激活";
      return res;
    }
    // 在线成功：复位锁态与按钮
    authInvalid = false;
    $("btn-lock-reconnect").textContent = "重新连接";
    state.last_sync = res.server_time;
    applyOffline(false, res.grace_hours, res.grace_hours); // 在线：恢复
    // P1-1 时间回拨分级：>24h 只读锁定（联网心跳恢复正常即解除）
    if (res.time_locked) {
      offlineLocked = true;
      $("lock-overlay").classList.remove("hidden");
      $("lock-text").textContent =
        "检测到系统时间与服务端时间偏差超过 24 小时（本机时间可能被调整），为授权安全业务功能已只读锁定。请校正系统时间后点「重新连接」恢复。";
    }
    if (state.scope === "group") {
      $("hotel-line").textContent = `${state.hotel_name} · 集团总端`;
      $("gp-count").textContent = res.member_count ?? "--";
    } else {
      state.auto_renew = res.auto_renew;
      renderOverview();
    }
    refreshSyncView(res);
    if (res.update_available) {
      $("update-banner").classList.remove("hidden");
      $("update-text").textContent = `发现新版本 v${res.latest_version}`;
    } else {
      $("update-banner").classList.add("hidden");
    }
    return res;
  } catch (e) {
    const el = $("ov-msg");
    if (el) {
      el.classList.remove("hidden");
      showMsg(el, `同步失败：${e}`, "err");
    }
    // 锁定遮罩可见时，错误必须显示在遮罩上（否则用户点重连「毫无反应」）
    const overlay = $("lock-overlay");
    if (overlay && !overlay.classList.contains("hidden")) {
      $("lock-text").textContent = `同步失败：${e}。请检查服务端地址与网络后重试。`;
    }
    return null;
  }
}

function startHeartbeatLoop() {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  doHeartbeat();
  heartbeatTimer = setInterval(doHeartbeat, 60_000);
}

$("btn-sync").addEventListener("click", () => {
  if (state.activated) doHeartbeat();
});
$("btn-heartbeat").addEventListener("click", () => {
  if (state.activated) doHeartbeat();
});
$("goto-sync").addEventListener("click", () => switchView("sync"));
$("btn-reconnect").addEventListener("click", () => doHeartbeat());
$("btn-lock-reconnect").addEventListener("click", () => {
  if (authInvalid) {
    // 凭证失效：重连无用——进激活页重新激活
    $("lock-overlay").classList.add("hidden");
    $("view-activate").classList.remove("hidden");
    ["view-overview", "view-group", "view-devices", "view-sync"].forEach((id) => {
      const el = $(id);
      if (el) el.classList.add("hidden");
    });
    const msg = $("activate-msg");
    if (msg) showMsg(msg, "原凭证已失效，请输入授权码重新激活（授权码可向服务商索取）", "err");
  } else {
    doHeartbeat();
  }
});

/* ---------- 集团总端 ---------- */
async function loadGroupProfile() {
  try {
    const d = await invoke("fetch_group_profile");
    $("hotel-line").textContent = `${d.group.name} · 集团总端`;
    $("gp-count").textContent = d.summary.memberCount;
    $("gp-devices").textContent = d.summary.deviceCount;
    $("gp-expiring").textContent = d.summary.expiringSoon;
    $("gp-alerts").textContent = d.summary.alertCount;

    $("gp-member-rows").innerHTML = d.members
      .map(
        (m) => `<tr>
          <td><input type="checkbox" data-id="${m.id}" ${gpSelected.has(m.id) ? "checked" : ""} /></td>
          <td>${m.name}</td>
          <td class="mono">${m.code}</td>
          <td>${m.city}</td>
          <td>${m.plan}</td>
          <td>${statusBadge(m.status)}</td>
          <td class="mono">${m.devices}/${m.max_devices}</td>
          <td class="mono">${m.expire_at}</td>
        </tr>`
      )
      .join("");
    $("gp-member-rows").querySelectorAll("input").forEach((cb) => {
      cb.addEventListener("change", () => {
        cb.checked ? gpSelected.add(cb.dataset.id) : gpSelected.delete(cb.dataset.id);
      });
    });

    $("gp-alert-rows").innerHTML = d.alerts.length
      ? d.alerts
          .slice(0, 10)
          .map(
            (a) => `<tr>
              <td>${statusBadge(a.status === "已处置" ? "valid" : "expired")}</td>
              <td>${a.rule}</td>
              <td>${a.hotel_name}</td>
              <td>${a.detail}</td>
              <td>${a.status}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="muted">暂无告警</td></tr>`;
  } catch (e) {
    $("gp-member-rows").innerHTML = `<tr><td colspan="8" class="muted">加载失败：${e}</td></tr>`;
  }
}

$("btn-group-renew-selected").addEventListener("click", async () => {
  if (guardLocked()) return;
  const ids = [...gpSelected];
  if (!ids.length) {
    alert("请先勾选门店");
    return;
  }
  try {
    const r = await invoke("group_renew", { hotelIds: ids });
    alert(`已续期 ${r.renewed} 家门店至 2027-12-31`);
    gpSelected = new Set();
    await loadGroupProfile();
    await doHeartbeat();
  } catch (e) {
    alert(`续期失败：${e}`);
  }
});

$("btn-group-renew-all").addEventListener("click", async () => {
  if (guardLocked()) return;
  if (!confirm("确定为旗下全部门店续期一年吗？")) return;
  try {
    const r = await invoke("group_renew", { hotelIds: [] });
    alert(`已续期 ${r.renewed} 家门店至 2027-12-31`);
    gpSelected = new Set();
    await loadGroupProfile();
    await doHeartbeat();
  } catch (e) {
    alert(`续期失败：${e}`);
  }
});

/* ---------- 设备信息（单店）：P0-1 自助解绑/迁移 ---------- */
function deviceStatusBadge(d) {
  if (d.status === "已解绑") return `<span class="badge">已解绑</span>`;
  if (d.status === "已迁移") return `<span class="badge">已迁移</span>`;
  return statusBadge(d.status === "在线" ? "valid" : "disabled");
}

async function loadDevices() {
  const tbody = $("device-rows");
  try {
    const d = await invoke("fetch_devices");
    $("device-quota-tip").textContent =
      `支持自助解绑（离线 ≥30 分钟的旧设备）与同名迁移；今日剩余自助操作次数：${d.quota_left_today ?? "--"}`;
    tbody.innerHTML = (d.devices || [])
      .map((x) => {
        const canUnbind = !x.is_self && (x.status === "在线" || x.status === "离线");
        const op = canUnbind
          ? `<button class="btn-mini" data-unbind="${x.id}">解绑</button>`
          : x.is_self ? '<span class="muted small">本机</span>' : '<span class="muted small">—</span>';
        return `<tr${x.status === "已解绑" || x.status === "已迁移" ? ' style="opacity:.55"' : ""}>
          <td>${x.name}${x.is_self ? "（本机）" : ""}</td>
          <td>${x.os}</td>
          <td class="mono">${(x.fingerprint || "").slice(0, 16)}…</td>
          <td>${x.last_active || "--"}</td>
          <td>${deviceStatusBadge(x)}</td>
          <td>${op}</td>
        </tr>`;
      })
      .join("");
    if (!(d.devices || []).length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">暂无绑定设备</td></tr>`;
    }
    tbody.querySelectorAll("[data-unbind]").forEach((b) => {
      b.addEventListener("click", async () => {
        if (guardLocked()) return;
        if (!confirm("确定解绑该设备吗？解绑后其配额立即释放（离线 ≥30 分钟的设备才可解绑）。")) return;
        try {
          const r = await invoke("unbind_device", { deviceId: b.dataset.unbind });
          alert(`已解绑。今日剩余自助操作次数：${r.quota_left_today}`);
          loadDevices();
        } catch (e) {
          alert(`解绑失败：${e}`);
        }
      });
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">加载失败：${e}</td></tr>`;
  }
}

/* ---------- 同步状态 / 更新检查 ---------- */
function refreshSyncView(hb) {
  $("sy-last").textContent = state.last_sync || "--";
  $("sy-status").innerHTML = statusBadge(state.status);
  $("sy-local").textContent = state.client_version || "0.2.1";
  if (hb) {
    $("sy-latest").textContent = hb.latest_version || "--";
    const warn = $("sy-warn");
    if (hb.time_anomaly) {
      showMsg(warn, "检测到本机时间与服务端时间不一致（可能被调整过），请注意授权安全。", "warn");
    } else if (hb.status && hb.status !== "valid" && hb.scope !== "group") {
      showMsg(warn, `授权状态：${STATUS_MAP[hb.status]?.text || hb.status}，请及时联系服务商续期。`, "warn");
    } else {
      hideMsg(warn);
    }
    if (hb.update_available) {
      $("update-box").classList.remove("hidden");
      $("ub-version").textContent = hb.latest_version;
      $("ub-notes").textContent = hb.notes || "请下载最新版本完成更新。";
      $("ub-url").textContent = hb.download_url || "（联系服务商获取安装包）";
    } else {
      $("update-box").classList.add("hidden");
    }
  }
}

$("btn-check-update").addEventListener("click", async () => {
  try {
    const v = await invoke("check_update", { serverUrl: state.server_url });
    $("sy-latest").textContent = v.latest || "--";
    const local = state.client_version || "0.2.1";
    if (v.latest && v.latest !== local) {
      $("update-box").classList.remove("hidden");
      $("ub-version").textContent = v.latest;
      $("ub-notes").textContent = v.notes || "请下载最新版本完成更新。";
      $("ub-url").textContent = v.download_url || "（联系服务商获取安装包）";
    } else {
      $("update-box").classList.add("hidden");
      showMsg($("sy-warn"), "当前已是最新版本。", "ok");
    }
  } catch (e) {
    showMsg($("sy-warn"), `检查更新失败：${e}`, "err");
  }
});

$("btn-save-server").addEventListener("click", async () => {
  const url = $("in-server2").value.trim();
  if (!url) return;
  // M2：非 HTTPS 明文传输须二次确认（Rust 侧同时强制 confirm 标志）
  const isHttps = url.toLowerCase().startsWith("https://");
  let confirmFlag = false;
  if (!isHttps) {
    if (!window.confirm("⚠️ 非 HTTPS 明文传输，激活凭证与心跳数据可能被窃听。\n确定继续使用 HTTP？")) return;
    confirmFlag = true;
  }
  try {
    const r = await invoke("set_server", { serverUrl: url, confirm: confirmFlag });
    const cleared = !!(r && r.cleared);
    state.server_url = url.replace(/\/+$/, "");
    if (cleared) {
      // M2：换服清凭证——旧 token/授权码不流向新地址，回激活页重新激活
      showMsg($("sy-warn"), "服务端地址已切换：为防旧凭证流向新地址，本地激活状态已清除，请重新激活。", "err");
      setTimeout(() => location.reload(), 1500);
    } else {
      showMsg($("sy-warn"), "服务端地址已保存。", "ok");
    }
  } catch (e) {
    showMsg($("sy-warn"), `保存失败：${e}`, "err");
  }
});

/* ---------- 消息通知配置（门店/集团） ---------- */
async function loadNotifyConfig() {
  $("hook-entity").textContent = state.scope === "group" ? "集团" : "门店";
  $("push-scope-desc").textContent = state.scope === "group" ? "（所有子酒店营业信息）" : "（本店营业信息）";
  try {
    const c = await invoke("fetch_notify_config");
    $("in-hook").value = c.webhookUrl || "";
    $("in-push-enabled").checked = !!c.pushEnabled;
    $("in-push-time").value = c.pushTime || "09:00";
    $("push-last-txt").textContent = c.lastPushDate ? `上次推送：${c.lastPushDate}` : "尚未推送过";
  } catch { /* 未激活时忽略 */ }
}

$("btn-save-hook").addEventListener("click", async () => {
  if (guardLocked()) return;
  const url = $("in-hook").value.trim();
  const pushEnabled = $("in-push-enabled").checked;
  const pushTime = $("in-push-time").value || "09:00";
  try {
    const r = await invoke("save_notify_config", { webhookUrl: url, pushEnabled, pushTime });
    showMsg($("hook-msg"), r.saved ? "配置已保存：webhook 与每日定时推送已生效。" : "配置已保存（webhook 已清空）。", "ok");
    loadNotifyConfig();
  } catch (e) {
    showMsg($("hook-msg"), String(e), "err");
  }
});

$("btn-test-hook").addEventListener("click", async () => {
  if (guardLocked()) return;
  try {
    const r = await invoke("test_notify");
    showMsg($("hook-msg"), r.dryRun ? "测试消息已生成（dry-run 模式未外发）。" : `测试消息已发送（${(r.sent || []).join(",")}）`, "ok");
  } catch (e) {
    showMsg($("hook-msg"), String(e), "err");
  }
});

$("btn-push-now").addEventListener("click", async () => {
  if (guardLocked()) return;
  try {
    const r = await invoke("test_push_daily");
    showMsg(
      $("hook-msg"),
      r.dryRun ? "今日简报已生成（dry-run 模式未外发）。" : `今日简报已推送（${(r.sent || []).join(",")}）`,
      "ok"
    );
  } catch (e) {
    showMsg($("hook-msg"), String(e), "err");
  }
});

$("btn-logout").addEventListener("click", async () => {
  if (!confirm("确定退出激活吗？退出后需重新输入授权码激活。")) return;
  await invoke("logout");
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  await init();
});

/* ---------- P0-2 自助续期 ---------- */
let renewOrder = null;

function renderRenewBanner() {
  const banner = $("renew-banner");
  if (!state.activated || state.scope === "group" || !state.expire_at) {
    banner.classList.add("hidden");
    return;
  }
  const days = Math.ceil((new Date(state.expire_at) - new Date()) / 86400000);
  if (days > 30) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  if (days < 0) {
    banner.className = "msg err";
    $("renew-banner-text").textContent = `授权已于 ${state.expire_at} 过期，请立即续期恢复正常使用。`;
  } else if (days <= 7) {
    banner.className = "msg err";
    $("renew-banner-text").textContent = `授权将在 ${days} 天后（${state.expire_at}）到期，请尽快续期。`;
  } else if (days <= 15) {
    banner.className = "msg warn";
    $("renew-banner-text").textContent = `授权将在 ${days} 天后（${state.expire_at}）到期。`;
  } else {
    banner.className = "msg warn";
    $("renew-banner-text").textContent = `授权将于 ${state.expire_at} 到期（剩余 ${days} 天），可提前续期。`;
  }
}

/* ---------- 续期订单状态轮询（F1 修复：驳回/生效状态客户端可见） ---------- */
let renewPollTimer = null;

function stopRenewPoll() {
  if (renewPollTimer) {
    clearInterval(renewPollTimer);
    renewPollTimer = null;
  }
}

async function refreshRenewStatus() {
  if (!renewOrder) return;
  try {
    const o = await invoke("get_renew_order", { orderNo: renewOrder });
    const s = o.status || "";
    $("ro-no").textContent = o.order_no || renewOrder;
    $("ro-amount").textContent = `¥${o.amount}`;
    if (s === "已生效") {
      $("ro-status").textContent = `✅ 订单已生效，到期日已顺延（${o.months} 个月）。`;
      $("btn-renew-proof").classList.add("hidden");
      $("btn-renew-create").classList.remove("hidden");
      stopRenewPoll();
    } else if (s === "已驳回") {
      $("ro-status").textContent = `❌ 订单已驳回：${o.reject_reason || "（无原因）"}。可重新下单或联系服务商。`;
      $("btn-renew-proof").classList.add("hidden");
      $("btn-renew-create").classList.remove("hidden");
      stopRenewPoll();
    } else if (s === "已关闭") {
      $("ro-status").textContent = `订单已超时关闭，请重新下单。`;
      $("btn-renew-proof").classList.add("hidden");
      $("btn-renew-create").classList.remove("hidden");
      stopRenewPoll();
    } else if (s === "待确认") {
      $("ro-status").textContent = `凭证已提交（待确认），服务商确认到账后自动生效。`;
      $("btn-renew-proof").classList.add("hidden");
    } else if (s === "待支付") {
      $("ro-status").textContent = `订单待支付：请对公转账 ¥${o.amount} 后填写回单号提交。`;
      $("btn-renew-proof").classList.remove("hidden");
    } else {
      $("ro-status").textContent = `订单状态：${s || "查询中…"}`;
    }
  } catch (e) {
    /* 轮询失败静默，下一轮重试 */
  }
}

function startRenewPoll() {
  stopRenewPoll();
  refreshRenewStatus();
  renewPollTimer = setInterval(refreshRenewStatus, 5000);
}

function openRenewModal() {
  renewOrder = localStorage.getItem("renew_last_order") || null;
  $("renew-order-box").classList.add("hidden");
  $("btn-renew-proof").classList.add("hidden");
  $("btn-renew-create").classList.remove("hidden");
  hideMsg($("renew-msg"));
  $("renew-overlay").classList.remove("hidden");
  if (renewOrder) {
    // 重开弹层：回显最近订单实时状态（已生效/已驳回/待确认一目了然）
    $("renew-order-box").classList.remove("hidden");
    startRenewPoll();
  }
}

$("btn-renew").addEventListener("click", () => {
  if (guardLocked()) return;
  openRenewModal();
});
$("btn-renew-close").addEventListener("click", () => {
  $("renew-overlay").classList.add("hidden");
  stopRenewPoll();
});

$("btn-renew-create").addEventListener("click", async () => {
  if (guardLocked()) return;
  const months = parseInt($("renew-months").value, 10);
  const plan = $("renew-plan").value;
  try {
    const r = await invoke("create_renew_order", {
      months,
      plan,
      channel: "transfer",
      hotelIds: [],
    });
    renewOrder = r.order_no;
    localStorage.setItem("renew_last_order", renewOrder);
    $("ro-no").textContent = r.order_no;
    $("ro-amount").textContent = `¥${r.amount}`;
    $("ro-status").textContent = "订单已创建，待支付。";
    $("renew-order-box").classList.remove("hidden");
    $("btn-renew-create").classList.add("hidden");
    $("btn-renew-proof").classList.remove("hidden");
    showMsg($("renew-msg"), "订单已创建，请按订单金额对公转账后填写回单号提交。", "ok");
  } catch (e) {
    showMsg($("renew-msg"), `创建订单失败：${e}`, "err");
  }
});

$("btn-renew-proof").addEventListener("click", async () => {
  if (!renewOrder) return;
  const proofNo = $("ro-proof-no").value.trim();
  if (!proofNo) {
    showMsg($("renew-msg"), "请填写汇款回单号/流水号", "err");
    return;
  }
  try {
    const r = await invoke("submit_renew_proof", {
      orderNo: renewOrder,
      proofNo,
      note: $("ro-proof-note").value.trim(),
      imageBase64: "",
    });
    $("ro-status").textContent = `凭证已提交（${r.status}），服务商确认到账后自动生效。`;
    showMsg($("renew-msg"), "凭证已提交，服务商确认到账后自动续期生效。", "ok");
    $("btn-renew-proof").classList.add("hidden");
    startRenewPoll(); // 轮询跟进：生效/驳回状态实时可见
  } catch (e) {
    showMsg($("renew-msg"), `提交失败：${e}`, "err");
  }
});

/* ---------- 菜单事件 ---------- */
document.querySelectorAll(".menu-item").forEach((b) => {
  b.addEventListener("click", () => switchView(b.dataset.view));
});

init();
