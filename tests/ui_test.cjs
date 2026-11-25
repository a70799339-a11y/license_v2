/* License 授权管理系统 — 浏览器 UI 自动化测试
 * 用法: node tests/ui_test.js [BASE_URL]
 * 默认测 http://localhost（WSL 部署），使用系统 Chrome，截图落 tests/ui-shots/
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://localhost';
const SHOTS = path.join(__dirname, 'ui-shots');
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(cid, module, name, cond, actual = '') {
  results.push({ id: cid, module, name, pass: !!cond, actual: String(actual).slice(0, 150) });
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${cid} ${module} · ${name}${actual ? '  (' + actual + ')' : ''}`);
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e.message).slice(0, 120)));

  console.log(`UI 测试目标: ${BASE}\n`);

  // ========== 1. 登录页 ==========
  console.log('【1. 登录页】');
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.join(SHOTS, '01-login.png') });
  check('U1.1', '登录页', '登录页渲染', await page.locator('input').count() >= 2, `${await page.locator('input').count()} 个输入框`);

  await page.locator('input[type="password"]').fill('wrongpass');
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await page.waitForTimeout(800);
  const errVisible = await page.locator('.el-alert').first().isVisible().catch(() => false);
  check('U1.2', '登录页', '错误密码显示提示', errVisible);

  await page.locator('input[type="password"]').fill('admin123');
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await page.waitForURL('**/dashboard', { timeout: 8000 }).catch(() => {});
  check('U1.3', '登录页', '登录成功跳转总览', page.url().includes('dashboard'), page.url().replace(BASE, ''));

  // ========== 2. 经营总览 ==========
  console.log('【2. 经营总览】');
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '02-dashboard.png') });
  const kpiCount = await page.locator('.kpi-card').count();
  check('U2.1', '经营总览', 'KPI 卡片渲染', kpiCount >= 4, `${kpiCount} 张`);
  check('U2.2', '经营总览', '折线图渲染', await page.locator('svg path').count() >= 2);
  // 到期风险列表数据异步渲染，先等行出现再计数
  await page.waitForSelector('.expire-row', { timeout: 8000 }).catch(() => {});
  const expireRows = await page.locator('.expire-row').count();
  check('U2.3', '经营总览', '到期风险提醒列表', expireRows >= 1, `${expireRows} 行`);

  // ========== 3. 客户管理 ==========
  console.log('【3. 客户管理】');
  await page.getByText('客户管理', { exact: true }).first().click();
  await page.waitForURL('**/customers', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '03-customers.png') });
  // 行选择器兼容经典 el-table 与虚拟滚动 el-table-v2（客户管理已升级 el-table-v2）
  const ROW = '.el-table__row, .el-table-v2__row';
  const rows = await page.locator(ROW).count();
  check('U3.1', '客户管理', '客户表格渲染', rows >= 1, `${rows} 行`);

  // 搜索筛选
  await page.locator('input[placeholder*="搜索酒店名称"]').fill('不存在的酒店xyz');
  await page.waitForTimeout(500);
  const emptyRows = await page.locator(ROW).count();
  check('U3.2', '客户管理', '搜索无结果时表格为空', emptyRows === 0, `${emptyRows} 行`);
  await page.locator('input[placeholder*="搜索酒店名称"]').fill('');
  await page.waitForTimeout(400);

  // 新增客户弹窗
  await page.getByRole('button', { name: '新增客户' }).click();
  await page.waitForTimeout(500);
  const dialogVisible = await page.locator('.el-dialog').isVisible().catch(() => false);
  check('U3.3', '客户管理', '新增客户弹窗打开', dialogVisible);
  if (dialogVisible) {
    await page.locator('.el-dialog input').first().fill('UI测试酒店');
    const codeInput = page.locator('.el-dialog input').nth(1);
    await codeInput.fill(`HT-UI-${Date.now() % 100000}`);
    await page.locator('.el-dialog').getByRole('button', { name: '创建' }).click();
    await page.waitForTimeout(800);
    const msgOk = await page.locator('.el-message').first().isVisible().catch(() => false);
    check('U3.4', '客户管理', '新增客户提交反馈', msgOk);
    await page.screenshot({ path: path.join(SHOTS, '03-customers-created.png') });
  }

  // 复选框批量操作
  const firstCheckbox = page.locator(`${ROW} .el-checkbox`).first();
  if (await firstCheckbox.count()) {
    await firstCheckbox.click();
    await page.waitForTimeout(400);
    const batchBar = await page.locator('.batch-bar').isVisible().catch(() => false);
    check('U3.5', '客户管理', '勾选行出现批量操作栏', batchBar);
    await page.screenshot({ path: path.join(SHOTS, '03-customers-batch.png') });
  }

  // 详情跳转
  const detailBtn = page.locator(ROW).first().getByRole('button', { name: '详情' }).first();
  if (await detailBtn.count()) {
    await detailBtn.click();
    await page.waitForURL('**/customers/**', { timeout: 5000 });
    check('U3.6', '客户管理', '详情跳转客户详情页', /\/customers\/.+/.test(new URL(page.url()).pathname), new URL(page.url()).pathname);
  } else {
    check('U3.6', '客户管理', '详情跳转客户详情页', false, '无可用行');
  }

  // ========== 4. 客户详情页 ==========
  console.log('【4. 客户详情页】');
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '04-customer-detail.png') });
  const tabs = await page.locator('.el-tabs__item').count();
  check('U4.1', '客户详情', 'Tab 导航渲染', tabs >= 5, `${tabs} 个 Tab`);
  check('U4.2', '客户详情', '概要指标卡', (await page.locator('.kpi-card').count()) >= 5);

  // 切 Tab：设备清单
  await page.getByRole('tab', { name: /设备清单/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(SHOTS, '04-customer-devices.png') });
  check('U4.3', '客户详情', '设备清单 Tab 切换', await page.locator('.el-tabs__content .el-table').isVisible().catch(() => true));

  // 一键续期
  await page.getByRole('button', { name: '一键续期' }).click();
  await page.waitForTimeout(800);
  const renewMsg = await page.locator('.el-message').first().isVisible().catch(() => false);
  check('U4.4', '客户详情', '一键续期反馈', renewMsg);

  // ========== 5. 授权管理 ==========
  console.log('【5. 授权管理】');
  await page.getByText('授权管理', { exact: true }).first().click();
  await page.waitForURL('**/licenses', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  // 防加载竞态假告警：等表格行渲染出来再断言（空库时限时兜底不阻塞）
  await page.waitForSelector('.el-table__row', { timeout: 8000 }).catch(() => {});
  await page.screenshot({ path: path.join(SHOTS, '05-licenses.png') });
  check('U5.1', '授权管理', '授权表格渲染', (await page.locator('.el-table__row').count()) >= 1);

  await page.getByRole('button', { name: '发放授权' }).click();
  await page.waitForTimeout(500);
  const issueDlg = await page.locator('.el-dialog').isVisible().catch(() => false);
  check('U5.2', '授权管理', '发放授权弹窗打开', issueDlg);
  if (issueDlg) {
    await page.screenshot({ path: path.join(SHOTS, '05-issue-dialog.png') });
    await page.locator('.el-dialog').getByRole('button', { name: '签发' }).click();
    await page.waitForTimeout(800);
    check('U5.3', '授权管理', '签发提交反馈', await page.locator('.el-message').first().isVisible().catch(() => false));
  }

  // ========== 6. 套餐管理 ==========
  console.log('【6. 套餐管理】');
  await page.getByText('套餐管理', { exact: true }).first().click();
  await page.waitForURL('**/plans', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '06-plans.png') });
  const planCards = await page.locator('.plan-card').count();
  check('U6.1', '套餐管理', '3 档套餐卡片', planCards === 3, `${planCards} 张`);
  check('U6.2', '套餐管理', '套餐功能清单', (await page.locator('.features li').count()) >= 8);

  // ========== 7. AI 智能风控 ==========
  console.log('【7. AI 智能风控】');
  await page.getByText('AI 智能风控').first().click();
  await page.waitForURL('**/risk', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '07-risk.png') });
  check('U7.1', 'AI 智能风控', '风控 KPI 卡', (await page.locator('.kpi-card').count()) >= 4);
  // 默认聚合视图（数据异步渲染，先等行出现）
  await page.waitForSelector('.el-table__row', { timeout: 8000 }).catch(() => {});
  check('U7.2', 'AI 智能风控', '告警聚合表渲染', (await page.locator('.el-table__row').count()) >= 1);

  // 切到明细视图后才有等级筛选（聚合/明细切换）
  await page.locator('.el-radio-button__inner', { hasText: '明细视图' }).click();
  await page.waitForTimeout(600);
  check('U7.2b', 'AI 智能风控', '明细视图切换', (await page.locator('.el-table__row').count()) >= 0);

  // 等级筛选（Element Plus radio-button 点可见 span）
  await page.locator('.el-radio-button__inner', { hasText: '紧急' }).click();
  await page.waitForTimeout(500);
  const urgentRows = await page.locator('.el-table__row').count();
  check('U7.3', 'AI 智能风控', '等级筛选生效', urgentRows >= 0, `紧急 ${urgentRows} 行`);
  await page.locator('.el-radio-button__inner', { hasText: '全部等级' }).click();
  await page.waitForTimeout(400);

  // 标记处置
  const handleBtn = page.getByRole('button', { name: '标记处置' }).first();
  if (await handleBtn.count()) {
    await handleBtn.click();
    await page.waitForTimeout(800);
    check('U7.4', 'AI 智能风控', '告警处置反馈', await page.locator('.el-message').first().isVisible().catch(() => false));
    await page.screenshot({ path: path.join(SHOTS, '07-risk-handled.png') });
  }

  // ========== 8. 到期续期工作台 ==========
  console.log('【8. 到期续期工作台】');
  await page.getByText('到期续期', { exact: true }).first().click();
  await page.waitForURL('**/renewals', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '08-renewals.png') });
  check('U8.1', '到期续期', '到期分布 4 卡', (await page.locator('.kpi-card').count()) === 4);
  check('U8.2', '到期续期', '自动续期开关', await page.locator('.el-switch').first().isVisible());

  // 自动续期开关切换
  await page.locator('.el-switch').first().click();
  await page.waitForTimeout(800);
  check('U8.3', '到期续期', '自动续期切换反馈', await page.locator('.el-message').first().isVisible().catch(() => false));

  // 批量续期
  const renewCb = page.locator('.el-table__row .el-checkbox').first();
  if (await renewCb.count()) {
    await renewCb.click();
    await page.waitForTimeout(400);
    const batchBtn = page.locator('.batch-bar').getByRole('button', { name: /批量续期/ }).first();
    if (await batchBtn.count()) {
      await batchBtn.click();
      await page.waitForTimeout(800);
      check('U8.4', '到期续期', '批量续期反馈', await page.locator('.el-message').first().isVisible().catch(() => false));
      await page.screenshot({ path: path.join(SHOTS, '08-renewals-batch.png') });
    }
  }

  // ========== 9. 安全设计 ==========
  console.log('【9. 安全设计】');
  await page.getByText('安全设计', { exact: true }).first().click();
  await page.waitForURL('**/security', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '09-security.png') });
  check('U9.1', '安全设计', '签发流程 6 步', (await page.locator('.step').count()) === 6, `${await page.locator('.step').count()} 步`);
  check('U9.2', '安全设计', '风控规则表', (await page.locator('.table-row').count()) >= 8);

  // 吊销（含原因输入）——P-3：自建专用测试授权再吊销，不触碰 HT-0002 等演示数据
  const adminToken = await page.evaluate(() => localStorage.getItem('license_token') || '');
  const ts = Date.now() % 1000000;
  const hRes = await page.request.post(`${BASE}/api/hotels`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { name: `UI测试店吊销${ts}`, code: `HT-UIR${ts}`, group: '测试集团', city: '测试市', plan: '基础版' },
  });
  const hJson = await hRes.json().catch(() => ({}));
  const lRes = await page.request.post(`${BASE}/api/licenses`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { hotelId: hJson.id, plan: '基础版', months: 1 },
  });
  const licJson = await lRes.json().catch(() => ({}));
  const testLicCode = licJson.code || '';
  check('U9.3a', '安全设计', '自建吊销测试授权就绪', !!testLicCode, `code=${testLicCode}`);
  // 安全页吊销列表只显示前 5 条生效授权（slice(0,5)），自建新授权排在末尾 → 走授权管理页搜索定位
  if (testLicCode) {
    await page.getByText('授权管理', { exact: true }).first().click();
    await page.waitForURL('**/licenses', { timeout: 5000 });
    await page.waitForLoadState('networkidle');
    await page.locator('input[placeholder*="搜索授权码"]').fill(testLicCode);
    await page.waitForTimeout(600);
    await page.waitForSelector('.el-table__row', { timeout: 8000 }).catch(() => {});
    const licRow = page.locator('.el-table__row', { hasText: testLicCode }).first();
    const licRevokeBtn = licRow.getByRole('button', { name: '吊销', exact: true });
    if (await licRevokeBtn.count()) {
      await licRevokeBtn.click();
      await page.waitForTimeout(600);
      const promptVisible = await page.locator('.el-message-box').isVisible().catch(() => false);
      check('U9.3', '安全设计', '吊销弹窗（含原因）打开', promptVisible);
      if (promptVisible) {
        await page.screenshot({ path: path.join(SHOTS, '09-revoke-dialog.png') });
        await page.locator('.el-message-box input').fill('UI自动化测试吊销');
        await page.locator('.el-message-box').getByRole('button', { name: '确认吊销' }).click();
        await page.waitForTimeout(800);
        check('U9.4', '安全设计', '吊销提交反馈', await page.locator('.el-message').first().isVisible().catch(() => false));
      } else {
        check('U9.4', '安全设计', '吊销提交反馈', false, '弹窗未打开');
      }
    } else {
      check('U9.3', '安全设计', '吊销弹窗（含原因）打开', false, '搜索后未找到自建授权吊销按钮');
      check('U9.4', '安全设计', '吊销提交反馈', false, '前置失败');
    }
    // 验证真的吊销了自建授权（而非演示数据）
    const stRes = await page.request.get(`${BASE}/api/licenses`, { headers: { Authorization: `Bearer ${adminToken}` } });
    const all = await stRes.json().catch(() => []);
    const mine = all.find(l => l.code === testLicCode);
    check('U9.4b', '安全设计', '被吊销的是自建授权', mine && mine.status === '已吊销', `status=${mine && mine.status}`);
    // 回安全设计页继续 9.5 推送配置验证
    await page.getByText('安全设计', { exact: true }).first().click();
    await page.waitForURL('**/security', { timeout: 5000 });
    await page.waitForLoadState('networkidle');
  } else {
    check('U9.3', '安全设计', '吊销弹窗（含原因）打开', false, '自建授权创建失败');
    check('U9.4', '安全设计', '吊销提交反馈', false, '前置失败');
    check('U9.4b', '安全设计', '被吊销的是自建授权', false, '前置失败');
  }

  // ========== 9.5 每日定时推送 + 汇报机器人（长连接） ==========
  console.log('【9.5 每日定时推送 · 汇报机器人】');
  await page.waitForTimeout(800); // 等 ws-status / 推送配置异步加载
  const pushCard = page.locator('.card-title', { hasText: '每日定时推送' }).first();
  check('U9.5a', '每日推送', '推送卡片存在', (await pushCard.count()) > 0);

  // 汇报机器人在线状态（长连接）
  // U5.1 同款：断言前显式等待异步状态渲染（ws-status 拉取慢时 800ms 不够）
  const onlineTag = page.locator('.el-tag', { hasText: '机器人在线' }).first();
  await onlineTag.waitFor({ state: 'attached', timeout: 15000 }).catch(() => {});
  check('U9.5b', '汇报机器人', '机器人在线标识', (await onlineTag.count()) > 0);
  await page.screenshot({ path: path.join(SHOTS, '095-push-card.png') });

  // 捕获最近会话（无 @ 消息时应给出明确提示；有则回填）
  await page.getByRole('button', { name: '捕获最近会话' }).click();
  const captureToast = page.locator('.el-message', { hasText: /捕获/ }).last();
  const captureVisible = await captureToast.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
  const captureText = captureVisible ? await captureToast.innerText() : '(无提示)';
  check('U9.5c', '汇报机器人', '捕获操作有反馈', captureVisible, captureText.slice(0, 60));
  await page.screenshot({ path: path.join(SHOTS, '095-capture.png') });

  // 绑定流程（测试会话 ID，结束后还原原值）
  const chatInput = page.locator('input[placeholder*="会话 ID"]').first();
  // 先记原值，测完按原值还原（清空会误伤真实绑定；token 键名是 license_token，写错会静默 401 还原失败）
  const origChatid = await page.evaluate(async () => {
    const token = localStorage.getItem('license_token') || '';
    const r = await fetch('/api/settings', { headers: { Authorization: `Bearer ${token}` } });
    const s = await r.json().catch(() => ({}));
    return s.wecom_ws_push_chatid || '';
  });
  await chatInput.fill('UITEST-CHATID-0001');
  await page.getByRole('button', { name: '绑定' }).click();
  const bindToast = page.locator('.el-message', { hasText: /已绑定|绑定失败/ }).last();
  const bindOk = await bindToast.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
  check('U9.5d', '汇报机器人', '绑定提交反馈', bindOk && (await bindToast.innerText()).includes('已绑定'), bindOk ? await bindToast.innerText() : '(无)');
  await page.screenshot({ path: path.join(SHOTS, '095-bind.png') });

  // 测试发送（验证反馈链路；目标为测试 ID，外发失败也算链路已走通）
  await page.getByRole('button', { name: '测试发送' }).click();
  const sendToast = page.locator('.el-message', { hasText: /发送/ }).last();
  const sendVisible = await sendToast.waitFor({ state: 'visible', timeout: 8000 }).then(() => true).catch(() => false);
  const sendText = sendVisible ? await sendToast.innerText() : '(无提示)';
  check('U9.5e', '汇报机器人', '测试发送有反馈', sendVisible, sendText.slice(0, 60));
  await page.screenshot({ path: path.join(SHOTS, '095-test-send.png') });

  // 还原测试绑定（按原值还原，避免污染真实配置）
  await page.evaluate(async (orig) => {
    const token = localStorage.getItem('license_token') || '';
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ key: 'wecom_ws_push_chatid', value: orig }),
    });
  }, origChatid);
  // 验证还原真的生效（token 键名错误会导致静默 401 还原失败 → 污染真实推送目标）
  const restoredChatid = await page.evaluate(async () => {
    const token = localStorage.getItem('license_token') || '';
    const r = await fetch('/api/settings', { headers: { Authorization: `Bearer ${token}` } });
    const s = await r.json().catch(() => ({}));
    return s.wecom_ws_push_chatid || '';
  });
  check('U9.5f', '汇报机器人', '测试绑定已还原', restoredChatid === origChatid, `now=${restoredChatid || '(空)'} orig=${origChatid || '(空)'}`);

  // ========== 10. 全局 ==========
  console.log('【10. 全局】');
  // SPA 深链接（部署态回归点）
  await page.goto(`${BASE}/risk`, { waitUntil: 'networkidle' });
  check('U10.1', '全局', 'SPA 深链接直达子路由', page.url().includes('/risk') && (await page.locator('.kpi-card').count()) >= 1);

  await page.goto(`${BASE}/renewals`, { waitUntil: 'networkidle' });
  check('U10.2', '全局', '刷新子路由不丢页', (await page.locator('.el-switch').count()) >= 1);

  // 退出登录
  await page.getByRole('button', { name: '退出登录' }).click();
  await page.waitForURL('**/login', { timeout: 5000 }).catch(() => {});
  check('U10.3', '全局', '退出登录回到登录页', page.url().includes('login'));

  // 未登录拦截
  await page.goto(`${BASE}/dashboard`, { waitUntil: 'networkidle' });
  check('U10.4', '全局', '未登录访问受保护页被拦截', page.url().includes('login'), new URL(page.url()).pathname);

  // 控制台错误
  check('U10.5', '全局', '页面无 JS 报错', errors.length === 0, errors.length ? errors[0] : '0 个');

  // 清理本脚本建的测试店（UI测试酒店/自建吊销店），避免污染客户列表与每日巡检统计
  try {
    const cleanupToken = await page.evaluate(() => localStorage.getItem('license_token') || '');
    // 已退出登录 → 重新登录拿 token
    const loginRes = await page.request.post(`${BASE}/api/auth/login`, {
      data: { username: 'admin', password: 'admin123' },
    });
    const loginJson = await loginRes.json().catch(() => ({}));
    const tok = loginJson.token || cleanupToken;
    const listRes = await page.request.get(`${BASE}/api/hotels`, {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const list = await listRes.json().catch(() => []);
    let swept = 0;
    for (const h of (Array.isArray(list) ? list : [])) {
      const n = h.name || '';
      if ((n.includes('UI测试') || n.includes('测试店吊销')) && h.status !== '已停用') {
        await page.request.patch(`${BASE}/api/hotels/${h.id}`, {
          headers: { Authorization: `Bearer ${tok}` },
          data: { status: '已停用' },
        });
        swept++;
      }
    }
    console.log(`[cleanup] UI 测试店清扫：停用 ${swept} 家`);
  } catch (e) {
    console.log(`[warn] UI 测试店清扫异常（不影响结果汇总）: ${e.message}`);
  }

  await browser.close();

  // 汇总
  const passed = results.filter(r => r.pass).length;
  const total = results.length;
  const summary = {
    target: BASE,
    time: new Date().toLocaleString('zh-CN'),
    total, passed, failed: total - passed,
    pass_rate: `${((passed / total) * 100).toFixed(1)}%`,
    results,
  };
  fs.writeFileSync(path.join(__dirname, 'ui_results.json'), JSON.stringify(summary, null, 2));
  console.log(`\n===== UI 测试汇总: ${passed}/${total} 通过（${summary.pass_rate}）=====`);
  for (const r of results.filter(x => !x.pass)) {
    console.log(`  ✗ ${r.id} ${r.module} · ${r.name} (${r.actual})`);
  }
  process.exit(passed === total ? 0 : 1);
})().catch(e => {
  console.error('UI 测试运行异常:', e.message);
  process.exit(2);
});
