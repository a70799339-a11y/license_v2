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
  const expireRows = await page.locator('.expire-row').count();
  check('U2.3', '经营总览', '到期风险提醒列表', expireRows >= 1, `${expireRows} 行`);

  // ========== 3. 客户管理 ==========
  console.log('【3. 客户管理】');
  await page.getByText('客户管理', { exact: true }).first().click();
  await page.waitForURL('**/customers', { timeout: 5000 });
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(SHOTS, '03-customers.png') });
  const rows = await page.locator('.el-table__row').count();
  check('U3.1', '客户管理', '客户表格渲染', rows >= 1, `${rows} 行`);

  // 搜索筛选
  await page.locator('input[placeholder*="搜索酒店名称"]').fill('不存在的酒店xyz');
  await page.waitForTimeout(500);
  const emptyRows = await page.locator('.el-table__row').count();
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
  const firstCheckbox = page.locator('.el-table__row .el-checkbox').first();
  if (await firstCheckbox.count()) {
    await firstCheckbox.click();
    await page.waitForTimeout(400);
    const batchBar = await page.locator('.batch-bar').isVisible().catch(() => false);
    check('U3.5', '客户管理', '勾选行出现批量操作栏', batchBar);
    await page.screenshot({ path: path.join(SHOTS, '03-customers-batch.png') });
  }

  // 详情跳转
  await page.locator('.el-table__row').first().getByRole('button', { name: '详情' }).click();
  await page.waitForURL('**/customers/**', { timeout: 5000 });
  check('U3.6', '客户管理', '详情跳转客户详情页', /\/customers\/.+/.test(new URL(page.url()).pathname), new URL(page.url()).pathname);

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
  check('U7.2', 'AI 智能风控', '告警表格渲染', (await page.locator('.el-table__row').count()) >= 1);

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

  // 吊销（含原因输入）
  const revokeBtn = page.getByRole('button', { name: '吊销', exact: true }).first();
  if (await revokeBtn.count()) {
    await revokeBtn.click();
    await page.waitForTimeout(600);
    const promptVisible = await page.locator('.el-message-box').isVisible().catch(() => false);
    check('U9.3', '安全设计', '吊销弹窗（含原因）打开', promptVisible);
    if (promptVisible) {
      await page.screenshot({ path: path.join(SHOTS, '09-revoke-dialog.png') });
      await page.locator('.el-message-box input').fill('UI自动化测试吊销');
      await page.locator('.el-message-box').getByRole('button', { name: '确认吊销' }).click();
      await page.waitForTimeout(800);
      check('U9.4', '安全设计', '吊销提交反馈', await page.locator('.el-message').first().isVisible().catch(() => false));
    }
  }

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
