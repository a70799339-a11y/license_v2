<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type License, type LlmCallLog } from '../api/client'
import { fmtBJ } from '../utils/time'

const licenses = ref<License[]>([])
const loading = ref(true)
const graceHours = ref('72')

onMounted(async () => {
  try {
    const [l, s] = await Promise.all([api.listLicenses(), api.getSettings()])
    licenses.value = l
    if (s.heartbeat_timeout_hours) graceHours.value = s.heartbeat_timeout_hours
    await loadWecom()
    await loadLlmLogs()
    await loadLlmCfg()
  } finally {
    loading.value = false
  }
})

// 离线宽限时长（服务端统一配置，随心跳下发全部客户端）
async function saveGrace() {
  const n = parseInt(graceHours.value, 10)
  if (!n || n < 1 || n > 8760) {
    ElMessage.warning('请输入 1–8760 之间的小时数')
    return
  }
  await api.updateSetting('heartbeat_timeout_hours', String(n))
  ElMessage.success(`已保存：客户端离线宽限 ${n} 小时（超时禁用，联网恢复）`)
}

// ---------- 企业微信通知 ----------
const wecom = ref({ opsWebhook: '', pushEnabled: false, dryRun: true })
const wecomTestResult = ref('')
const simText = ref('状态 HT-0001')
const simReply = ref('')
const simLoading = ref(false)
const simAck = ref('')
const wsStatus = ref<{ running: boolean; authenticated: boolean; last_error: string; started_at: string; msg_count: number } | null>(null)

async function loadWecom() {
  try {
    const cfg = await api.wecomConfig() as Record<string, unknown>
    wecom.value = {
      opsWebhook: String(cfg.opsWebhook || ''),
      pushEnabled: Boolean(cfg.pushEnabled),
      dryRun: Boolean(cfg.dryRun),
    }
  } catch { /* 无权限时忽略 */ }
  try {
    wsStatus.value = await fetch('/api/wecom/ws-status', {
      headers: { Authorization: `Bearer ${localStorage.getItem('license_token')}` },
    }).then(r => r.json())
  } catch { /* 忽略 */ }
}

async function saveWecom() {
  try {
    await api.wecomConfigUpdate(wecom.value)
    ElMessage.success('企微配置已保存')
    await loadWecom()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function testWecom() {
  const r = await api.wecomTest()
  wecomTestResult.value = r.dryRun
    ? `dry-run 模式：内容已生成未外发（${r.sent.join(',')}）`
    : `已发送：${r.sent.join(',')}`
  ElMessage.success(wecomTestResult.value)
}

async function pushDaily() {
  await api.wecomFlushDaily()
  ElMessage.success('日报已生成推送（dry-run 时仅记录）')
}

async function runSim() {
  simLoading.value = true
  simReply.value = ''
  simAck.value = '🔍 正在检索，请稍候…'
  try {
    const r = await api.wecomSimulate(simText.value)
    simAck.value = r.ackSent ? r.ack : ''
    simReply.value = r.reply
  } catch (e) {
    simReply.value = e instanceof Error ? e.message : '执行失败'
    simAck.value = ''
  } finally {
    simLoading.value = false
  }
}

// ---------- 大模型调用审计 ----------
const llmLogs = ref<LlmCallLog[]>([])
const llmFilter = ref('')
const llmDetail = ref<LlmCallLog | null>(null)
const llmDetailShow = ref(false)
const llmSimInput = ref('最近哪家酒店风险最高？联系人手机 13812345678')

async function loadLlmLogs() {
  llmLogs.value = await api.listLlmLogs({ sourceType: llmFilter.value || undefined, limit: 30 })
}

async function simulateLlm() {
  try {
    await api.simulateLlmCall({
      sourceType: llmFilter.value || 'admin',
      scene: '对话查询',
      userInput: llmSimInput.value,
    })
    await loadLlmLogs()
    ElMessage.success('已产生一条模拟调用记录（可展开核对脱敏与留痕）')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '模拟失败')
  }
}

function sourceTypeLabel(t: string) {
  return t === 'group' ? '集团总端' : t === 'hotel' ? '门店客户端' : '管理后台'
}

// ---------- 大模型接入（云端 + 本地双配置） ----------
const llmCfg = ref({
  active: '',
  fallback: true,
  local: { provider: '', endpoint: '', model: '' },
  cloud: { endpoint: '', model: '', apiKey: '' },
})
const llmPing = ref<{ ok: boolean; detail: string; local?: { ok: boolean; detail: string }; cloud?: { ok: boolean; detail: string } } | null>(null)

async function loadLlmCfg() {
  try {
    const s = await api.llmStatus()
    llmCfg.value = {
      active: s.active,
      fallback: s.fallback !== 'off',
      local: { ...s.local },
      cloud: { endpoint: s.cloud.endpoint, model: s.cloud.model, apiKey: '' },
    }
    llmPing.value = s.ping
  } catch { /* 无权限忽略 */ }
}

async function saveLlmCfg() {
  try {
    await api.llmConfigSave({
      active: llmCfg.value.active,
      fallback: llmCfg.value.fallback,
      local: llmCfg.value.local,
      cloud: { ...llmCfg.value.cloud },
    })
    llmCfg.value.cloud.apiKey = ''
    await loadLlmCfg()
    ElMessage.success('模型配置已保存（云端与本地并存，调用统一经审计网关留痕）')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function testLlm() {
  await saveLlmCfg()
  await loadLlmCfg()
  if (llmPing.value?.ok) ElMessage.success(`连通正常：${llmPing.value.detail}`)
  else ElMessage.warning(`连通异常：${llmPing.value?.detail || '未知'}`)
}

// 签发流程 6 步
const steps = [
  { n: 1, title: '创建授权', desc: '选择门店与套餐', server: true },
  { n: 2, title: '双人审批', desc: '高危操作复核', server: true },
  { n: 3, title: 'KMS 私钥签名', desc: 'Ed25519 算法', server: true },
  { n: 4, title: '生成 License', desc: '绑定设备指纹', server: true },
  { n: 5, title: '下发客户端', desc: '安全通道传输', server: false },
  { n: 6, title: '公钥验签', desc: '心跳 + 吊销校验', server: false },
]

// 风控告警规则（与实时告警管道一一对应：检测点命中自动写入风控告警）
const rules = [
  { rule: '多设备并发激活', level: '高', action: '实时告警 + 人工核查', on: true },
  { rule: '时间回滚', level: '高', action: '实时告警（客户端上报时钟异常）', on: true },
  { rule: '设备指纹变更', level: '高', action: '拒绝心跳 + 实时告警', on: true },
  { rule: '心跳中断', level: '中', action: '离线宽限超时锁定 + 告警', on: true },
  { rule: 'License 共享检测', level: '高', action: '拒绝激活 + 实时告警', on: true },
  { rule: '吊销后使用', level: '紧急', action: '拒绝请求 + 实时告警', on: true },
  { rule: '无效激活尝试', level: '中', action: '实时告警', on: true },
  { rule: '设备数超限', level: '中', action: '拒绝激活 + 实时告警', on: true },
  { rule: '异地 IP 聚集', level: '中', action: '规则预留（下版本）', on: false },
  { rule: '客户端完整性校验', level: '紧急', action: '反调试门禁拒绝启动（已实现）', on: true },
]

// 吊销记录 = 已吊销的授权
const revoked = computed(() => licenses.value.filter(l => l.status === '已吊销'))

async function revokeWithReason(row: License) {
  try {
    const { value } = await ElMessageBox.prompt(
      `吊销授权 ${row.code}（${row.hotelName}）后客户端将立即失效，请填写吊销原因：`,
      '吊销确认',
      { confirmButtonText: '确认吊销', cancelButtonText: '取消', inputPlaceholder: '如：欠费停用 / 违规转租 / 申请退订' }
    )
    await api.revokeLicense(row.id, value || '手动吊销')
    licenses.value = await api.listLicenses()
    ElMessage.success('已吊销')
  } catch { /* 用户取消 */ }
}
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">安全设计</h1>
        <p class="page-sub">License 签发、防破解与吊销管理</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button>签发策略</el-button>
        <el-button>审计日志</el-button>
        <el-button type="danger">立即吊销</el-button>
      </div>
    </div>

    <!-- 签发流程 -->
    <div class="card flow-card mb-20">
      <div class="card-title">客户端离线宽限（服务端统一配置）</div>
      <p class="muted" style="font-size: 12px; margin-bottom: 12px">
        客户端离线后可继续使用至宽限时长；超时功能自动禁用，恢复联网心跳成功后自动解锁。此参数对全部客户端（门店端/集团端）统一生效。
      </p>
      <div style="display: flex; gap: 12px; align-items: center">
        <el-input v-model="graceHours" style="width: 160px" placeholder="小时数">
          <template #append>小时</template>
        </el-input>
        <el-button type="primary" @click="saveGrace">保存</el-button>
        <span class="muted" style="font-size: 12px">当前：{{ graceHours }} 小时（默认 72）</span>
      </div>
    </div>

    <div class="card flow-card mb-20">
      <div class="card-title">企业微信通知（运营群推送 + 指令管道）</div>
      <p class="muted" style="font-size: 12px; margin-bottom: 12px">
        紧急/高危告警即时推送、日报每日汇总；集团群 webhook 在「集团管理 → 集团详情」配置（只推本集团事件）。
        dry-run 开启时只记录不外发，用于调试；推送内容自动脱敏。
      </p>
      <div style="display: flex; flex-direction: column; gap: 12px">
        <div class="ws-row">
          <span class="muted small">智能机器人长连接（指令通道，凭据配置于服务端 backend/.env）</span>
          <el-tag v-if="wsStatus?.authenticated" type="success" size="small">已连接</el-tag>
          <el-tag v-else-if="wsStatus?.running" type="warning" size="small">连接中</el-tag>
          <el-tag v-else type="info" size="small">未启用</el-tag>
          <span v-if="wsStatus?.msg_count" class="muted small">已处理 {{ wsStatus.msg_count }} 条消息</span>
          <span v-if="wsStatus?.last_error" class="muted small" style="color: #b91c1c">{{ wsStatus.last_error }}</span>
        </div>
        <div style="display: flex; gap: 12px; align-items: center">
          <el-input v-model="wecom.opsWebhook" placeholder="运营群机器人 webhook 地址" style="flex: 1" show-password />
          <el-switch v-model="wecom.pushEnabled" active-text="启用推送" />
          <el-switch v-model="wecom.dryRun" active-text="dry-run" />
          <el-button type="primary" @click="saveWecom">保存</el-button>
          <el-button @click="testWecom">测试推送</el-button>
          <el-button @click="pushDaily">推日报</el-button>
        </div>
        <div style="display: flex; gap: 12px; align-items: center">
          <el-input v-model="simText" placeholder="指令模拟，如：状态 HT-0001" style="flex: 1" :disabled="simLoading" />
          <el-button type="primary" :loading="simLoading" @click="runSim">执行指令</el-button>
        </div>
        <p v-if="simLoading" class="muted sim-ack">{{ simAck || '🔍 正在检索，请稍候…' }}</p>
        <pre v-else-if="simReply" class="sim-reply">{{ simReply }}</pre>
      </div>
    </div>

    <div class="card flow-card mb-20">
      <div class="card-title">大模型调用审计（集团 / 门店 / 后台 全量留痕）</div>
      <p class="muted" style="font-size: 12px; margin-bottom: 12px">
        所有大模型调用统一经服务端网关（客户端不直连 LLM），每次调用记录：调用来源、发起人、用户输入、模型输入/输出、token 用量、耗时——文本统一脱敏后落库。
      </p>
      <div class="llm-cfg-row">
        <span class="muted small">模型接入</span>
        <el-select v-model="llmCfg.active" placeholder="未接入" style="width: 120px" clearable>
          <el-option label="本地优先" value="local" />
          <el-option label="云端优先" value="cloud" />
        </el-select>
        <el-switch v-model="llmCfg.fallback" active-text="失败自动切另一套" />
        <el-button type="primary" @click="saveLlmCfg">保存</el-button>
        <el-button @click="testLlm">测试连通</el-button>
        <el-tag v-if="llmPing" size="small" :type="llmPing.ok ? 'success' : 'danger'">
          {{ llmPing.ok ? '正常' : '异常' }}
        </el-tag>
      </div>
      <div class="llm-cfg-row">
        <span class="muted small">本地（Ollama / llama.cpp）</span>
        <el-select v-model="llmCfg.local.provider" placeholder="未配置" style="width: 120px" clearable>
          <el-option label="Ollama" value="ollama" />
          <el-option label="llama.cpp" value="llama.cpp" />
        </el-select>
        <el-input v-model="llmCfg.local.endpoint" placeholder="http://localhost:11434" style="width: 210px" />
        <el-input v-model="llmCfg.local.model" placeholder="如 qwen2.5:7b" style="width: 160px" />
        <el-tag v-if="llmPing?.local" size="small" :type="llmPing.local.ok ? 'success' : 'info'">
          {{ llmPing.local.ok ? '本地正常' : '本地未就绪' }}
        </el-tag>
      </div>
      <div class="llm-cfg-row">
        <span class="muted small">云端（OpenAI 兼容：DeepSeek / 腾讯混元等）</span>
        <el-input v-model="llmCfg.cloud.endpoint" placeholder="https://api.deepseek.com/v1" style="width: 230px" />
        <el-input v-model="llmCfg.cloud.model" placeholder="如 deepseek-chat" style="width: 150px" />
        <el-input v-model="llmCfg.cloud.apiKey" type="password" show-password placeholder="API Key（留空不修改）" style="width: 190px" />
        <el-tag v-if="llmPing?.cloud" size="small" :type="llmPing.cloud.ok ? 'success' : 'info'">
          {{ llmPing.cloud.ok ? '云端正常' : '云端未就绪' }}
        </el-tag>
      </div>
      <p v-if="llmPing" class="muted" style="font-size: 12px; margin: 4px 0 12px">{{ llmPing.detail }}</p>
      <div style="display: flex; gap: 12px; align-items: center; margin: 12px 0">
        <el-select v-model="llmFilter" placeholder="全部来源" style="width: 140px" clearable @change="loadLlmLogs">
          <el-option label="集团总端" value="group" />
          <el-option label="门店客户端" value="hotel" />
          <el-option label="管理后台" value="admin" />
        </el-select>
        <el-input v-model="llmSimInput" placeholder="模拟用户输入（验证留痕与脱敏）" style="flex: 1" />
        <el-button @click="simulateLlm">模拟一次调用</el-button>
        <el-button @click="loadLlmLogs">刷新</el-button>
      </div>
      <el-table :data="llmLogs" size="small" max-height="360">
        <el-table-column label="时间" width="140">
          <template #default="{ row }"><span class="mono">{{ fmtBJ(row.createdAt) }}</span></template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.sourceType === 'group' ? 'warning' : row.sourceType === 'hotel' ? 'success' : 'info'">
              {{ sourceTypeLabel(row.sourceType) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="sourceName" label="实体" width="130" show-overflow-tooltip />
        <el-table-column prop="scene" label="场景" width="100" />
        <el-table-column prop="userInput" label="用户输入（脱敏）" min-width="180" show-overflow-tooltip />
        <el-table-column label="tokens" width="110">
          <template #default="{ row }"><span class="mono">{{ row.tokensIn }}+{{ row.tokensOut }}</span></template>
        </el-table-column>
        <el-table-column prop="latencyMs" label="耗时" width="80">
          <template #default="{ row }"><span class="mono">{{ row.latencyMs }}ms</span></template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : 'danger'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="详情" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="llmDetail = row; llmDetailShow = true">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-dialog v-model="llmDetailShow" title="大模型调用详情" width="640px">
        <template v-if="llmDetail">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="来源">{{ sourceTypeLabel(llmDetail.sourceType) }} · {{ llmDetail.sourceName }}</el-descriptions-item>
            <el-descriptions-item label="发起人"><span class="mono">{{ llmDetail.userId }}</span></el-descriptions-item>
            <el-descriptions-item label="场景">{{ llmDetail.scene }}</el-descriptions-item>
            <el-descriptions-item label="模型">{{ llmDetail.modelName }}</el-descriptions-item>
            <el-descriptions-item label="token">{{ llmDetail.tokensIn }} 入 / {{ llmDetail.tokensOut }} 出</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ llmDetail.latencyMs }}ms</el-descriptions-item>
          </el-descriptions>
          <div class="llm-field"><b>用户输入（脱敏）</b><pre>{{ llmDetail.userInput }}</pre></div>
          <div class="llm-field"><b>模型输入（脱敏）</b><pre>{{ llmDetail.modelInput }}</pre></div>
          <div class="llm-field"><b>模型输出（脱敏）</b><pre>{{ llmDetail.modelOutput }}</pre></div>
          <div v-if="llmDetail.error" class="llm-field"><b>错误</b><pre style="color: #b91c1c">{{ llmDetail.error }}</pre></div>
        </template>
      </el-dialog>
    </div>

    <div class="card flow-card mb-20">
      <div class="card-title">License 签发流程（服务端签名 + 客户端验签）</div>
      <div class="flow-row">
        <template v-for="(s, i) in steps" :key="s.n">
          <div class="step">
            <div class="badge" :class="s.server ? 'server' : 'client'">{{ s.n }}</div>
            <div class="step-title">{{ s.title }}</div>
            <div class="step-desc">{{ s.desc }}</div>
          </div>
          <div v-if="i < steps.length - 1" class="arrow">→</div>
        </template>
      </div>
    </div>

    <!-- 规则 + 吊销 双栏 -->
    <div class="grid">
      <div class="card">
        <div class="table-head">
          <span class="th" style="width: 200px">风控告警规则</span>
          <span class="th" style="width: 100px">风险等级</span>
          <span class="th" style="width: 160px">自动处置</span>
          <span class="th" style="flex: 1">状态</span>
        </div>
        <div v-for="r in rules" :key="r.rule" class="table-row">
          <span class="td" style="width: 200px; font-weight: 500">{{ r.rule }}</span>
          <span class="td" style="width: 100px">
            <b :style="{ color: r.level === '紧急' || r.level === '高' ? '#ef4444' : '#f59e0b' }">{{ r.level }}</b>
          </span>
          <span class="td text-2" style="width: 160px">{{ r.action }}</span>
          <span class="td" style="flex: 1">
            <span class="status-on">运行中</span>
          </span>
        </div>
      </div>

      <div class="card">
        <div class="table-head">
          <span class="th" style="flex: 1">License 吊销管理</span>
          <span class="th" style="width: 70px">状态</span>
          <span class="th" style="width: 100px">时间</span>
          <span class="th" style="width: 70px">操作</span>
        </div>
        <div v-if="!revoked.length" class="empty text-3">暂无吊销记录</div>
        <div v-for="l in revoked" :key="l.id" class="table-row">
          <span class="td" style="flex: 1">
            <div class="mono" style="font-weight: 500">{{ l.code }}</div>
            <div class="text-3" style="font-size: 11px">{{ l.hotelName }} · {{ l.plan }}</div>
          </span>
          <span class="td" style="width: 70px"><span class="status-off">已吊销</span></span>
          <span class="td mono text-3" style="width: 100px; font-size: 11px">{{ l.expireAt }}</span>
          <span class="td" style="width: 70px">
            <el-button link type="primary" size="small" @click="revokeWithReason(l)">恢复</el-button>
          </span>
        </div>

        <div class="divider" />
        <div class="table-head">
          <span class="th" style="flex: 1">生效中授权（可吊销）</span>
        </div>
        <div v-for="l in licenses.filter(x => x.status === '生效中').slice(0, 5)" :key="l.id" class="table-row">
          <span class="td" style="flex: 1">
            <div class="mono" style="font-weight: 500">{{ l.code }}</div>
            <div class="text-3" style="font-size: 11px">{{ l.hotelName }} · {{ l.plan }}</div>
          </span>
          <span class="td" style="width: 70px"><span class="status-on">生效中</span></span>
          <span class="td mono text-3" style="width: 100px; font-size: 11px">{{ l.expireAt }}</span>
          <span class="td" style="width: 70px">
            <el-button link type="danger" size="small" @click="revokeWithReason(l)">吊销</el-button>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
.flow-card { padding: 20px; }
.sim-reply { background: #f4f4f5; border-radius: 6px; padding: 10px 14px; font-size: 12px; white-space: pre-wrap; margin: 0; }
.sim-ack { margin: 8px 0 0; font-size: 12px; color: #2563eb; }
.ws-row { display: flex; gap: 10px; align-items: center; background: #fafafa; border: 1px solid var(--border, #e5e5e5); border-radius: 6px; padding: 8px 12px; }
.flow-row { display: flex; align-items: center; gap: 4px; }
.step { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.badge {
  width: 28px; height: 28px; border-radius: 14px; color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600;
}
.badge.server { background: #3b82f6; }
.badge.client { background: #10b981; }
.step-title { font-size: 14px; font-weight: 500; }
.step-desc { font-size: 11px; color: #a1a1aa; }
.arrow { color: #a1a1aa; font-size: 16px; }
.grid { display: flex; gap: 16px; }
.grid > .card { flex: 1; padding: 0; overflow: hidden; }
.table-head {
  display: flex; padding: 14px 20px; background: #fafafa;
  border-bottom: 1px solid #f0f0f2; font-size: 13px; font-weight: 600;
}
.table-row {
  display: flex; padding: 12px 20px; border-bottom: 1px solid #f0f0f2;
  font-size: 13px; align-items: center;
}
.th, .td { display: flex; flex-direction: column; justify-content: center; }
.status-on { color: #10b981; font-size: 12px; font-weight: 500; }
.status-off { color: #ef4444; font-size: 12px; font-weight: 500; }
.empty { padding: 40px; text-align: center; }
.divider { height: 8px; background: #fafafa; }
.llm-field { margin-top: 12px; font-size: 12px; }
.llm-field b { display: block; margin-bottom: 4px; color: #52525b; }
.llm-field pre {
  margin: 0; background: #f4f4f5; border-radius: 6px; padding: 10px 12px;
  white-space: pre-wrap; word-break: break-all; font-size: 12px; max-height: 160px; overflow: auto;
}
.llm-cfg-row {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  background: #fafafa; border: 1px solid #f0f0f2; border-radius: 8px;
  padding: 10px 12px; margin-bottom: 4px;
}
</style>
