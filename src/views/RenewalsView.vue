<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Hotel } from '../api/client'

const hotels = ref<Hotel[]>([])
const settings = ref<Record<string, string>>({})
const loading = ref(true)
const selected = ref<Hotel[]>([])

// P2-2 流失风险 ｜ P0-2 续期订单 ｜ P2-1 授权盘点
const risks = ref<
  Array<{
    hotel_id: string
    risk: string
    factors: string[]
    days_left: number
    active_ratio: number
    idle_days: number
  }>
>([])
const orders = ref<
  Array<{
    id: number
    orderNo: string
    targetName: string
    months: number
    amount: number
    channel: string
    status: string
    proofNo: string
    proofNote: string
    createdAt: string
  }>
>([])
const audit = ref<{
  summary: { total: number; idle: number; underused: number; suspect: number; empty: number }
  idle: Array<{ hotel: string; code: string; idleDays: number; expireAt: string }>
  underused: Array<{ hotel: string; code: string; activeRatio: number; devices: number }>
  suspects: Array<{ hotel: string; alerts: Array<{ rule: string; detail: string; level: string }> }>
  empty: Array<{ hotel: string; code: string; group: string }>
} | null>(null)
const scriptDialog = ref(false)
const scriptText = ref('')
const scriptHotel = ref('')

onMounted(async () => {
  try {
    const [h, s, r, o, a] = await Promise.all([
      api.listHotels(),
      api.getSettings(),
      api.renewalRisks().catch(() => []),
      api.renewOrders().catch(() => []),
      api.renewalAudit().catch(() => null),
    ])
    hotels.value = h
    settings.value = s
    risks.value = r
    orders.value = o
    audit.value = a
  } finally {
    loading.value = false
  }
})

const riskMap = computed(() => {
  const m: Record<string, { risk: string; factors: string[] }> = {}
  risks.value.forEach(r => {
    m[r.hotel_id] = { risk: r.risk, factors: r.factors }
  })
  return m
})

const pendingOrders = computed(() =>
  orders.value.filter(o => o.status === '待确认' || o.status === '待支付')
)

function riskTag(risk: string) {
  return risk === '紧急' ? 'danger' : risk === '高' ? 'warning' : risk === '中' ? 'info' : 'success'
}

async function confirmOrder(o: { id: number; orderNo: string }) {
  await api.confirmRenewOrder(o.id)
  orders.value = await api.renewOrders()
  hotels.value = await api.listHotels()
  ElMessage.success(`订单 ${o.orderNo} 已确认生效`)
}

async function rejectOrder(o: { id: number; orderNo: string }) {
  const reason = window.prompt('驳回原因（将记录给客户）：', '凭证未核实') || '凭证未核实'
  await api.rejectRenewOrder(o.id, reason)
  orders.value = await api.renewOrders()
  ElMessage.success(`订单 ${o.orderNo} 已驳回`)
}

async function openScript(hotelId: string, hotelName: string) {
  scriptHotel.value = hotelName
  scriptText.value = '生成中…'
  scriptDialog.value = true
  try {
    const r = await api.aiRenewScript(hotelId)
    scriptText.value = r.script
  } catch (e) {
    scriptText.value = `生成失败：${e}`
  }
}

function copyScript() {
  navigator.clipboard?.writeText(scriptText.value)
  ElMessage.success('话术已复制')
}

const autoRenewOn = computed(() => settings.value.auto_renew === 'on')

// 到期分布（7/30/60/90 天内）
const buckets = computed(() => {
  const now = Date.now()
  const day = 86400000
  const count = (n: number) =>
    hotels.value.filter(h => {
      const diff = (new Date(h.expireAt).getTime() - now) / day
      return diff >= -3650 && diff <= n
    }).length
  return [
    { label: '7 天内到期', n: count(7), color: '#fef2f2', text: '#ef4444' },
    { label: '30 天内到期', n: count(30), color: '#fffbeb', text: '#f59e0b' },
    { label: '60 天内到期', n: count(60), color: '#eff6ff', text: '#3b82f6' },
    { label: '90 天内到期', n: count(90), color: '#f4f4f5', text: '#52525b' },
  ]
})

// 待续期列表：30 天内到期 + 已过期
const renewList = computed(() => {
  const now = Date.now()
  const day = 86400000
  return hotels.value
    .filter(h => {
      const diff = (new Date(h.expireAt).getTime() - now) / day
      return diff <= 30
    })
    .sort((a, b) => a.expireAt.localeCompare(b.expireAt))
})

function daysLeft(expireAt: string) {
  return Math.ceil((new Date(expireAt).getTime() - Date.now()) / 86400000)
}

function exportCsv() {
  const rows = [['酒店', '客户编号', '套餐', '到期时间', '剩余天数', '自动续期']]
  renewList.value.forEach(h => rows.push([h.name, h.code, h.plan, h.expireAt, String(daysLeft(h.expireAt)), h.autoRenew ? '是' : '否']))
  const csv = '﻿' + rows.map(r => r.join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = '待续期清单.csv'
  a.click()
}

async function batchRenew() {
  const ids = selected.value.map(h => h.id)
  if (!ids.length) return
  await api.batchRenew(ids)
  hotels.value = await api.listHotels()
  selected.value = []
  ElMessage.success(`已为 ${ids.length} 家酒店续期`)
}

async function renewOne(row: Hotel) {
  await api.batchRenew([row.id])
  hotels.value = await api.listHotels()
  ElMessage.success(`已为 ${row.name} 续期`)
}

async function toggleAutoRenew(val: boolean) {
  await api.updateSetting('auto_renew', val ? 'on' : 'off')
  settings.value.auto_renew = val ? 'on' : 'off'
  ElMessage.success(val ? '自动续期已开启' : '自动续期已关闭')
}
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">到期续期工作台</h1>
        <p class="page-sub">到期分布、批量续期与自动续期管理</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button @click="exportCsv">导出报表</el-button>
        <el-button type="primary" :disabled="!selected.length" @click="batchRenew">批量续期</el-button>
      </div>
    </div>

    <!-- 到期分布 -->
    <div class="kpi-row">
      <div
        v-for="b in buckets"
        :key="b.label"
        class="card kpi-card"
        :style="{ background: b.color }"
      >
        <div class="kpi-label">{{ b.label }}</div>
        <div class="kpi-value" :style="{ color: b.text }">{{ b.n }}</div>
      </div>
    </div>

    <!-- 自动续期面板 -->
    <div class="card auto-panel mb-20">
      <div>
        <div class="auto-title">自动续期</div>
        <div class="text-2" style="font-size: 13px; margin-top: 4px">
          到期前 7 天自动发起续期扣款，成功后自动延长授权
        </div>
      </div>
      <el-switch :model-value="autoRenewOn" @change="toggleAutoRenew" />
    </div>

    <!-- 续期列表 -->
    <div v-if="selected.length" class="batch-bar">
      <span>已选 <b>{{ selected.length }}</b> 项</span>
      <el-button size="small" type="primary" @click="batchRenew">批量续期一年</el-button>
      <el-button size="small" text @click="selected = []">取消</el-button>
    </div>

    <div class="card">
      <el-table :data="renewList" @selection-change="selected = $event">
        <el-table-column type="selection" width="48" />
        <el-table-column label="酒店 / 客户编号" min-width="200">
          <template #default="{ row }">
            <div style="font-weight: 500">{{ row.name }}</div>
            <div class="mono text-3" style="font-size: 12px">{{ row.code }}</div>
          </template>
        </el-table-column>
        <el-table-column label="套餐" width="90">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.plan }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="expireAt" label="到期时间" width="110">
          <template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template>
        </el-table-column>
        <el-table-column label="剩余天数" width="110">
          <template #default="{ row }">
            <span
              class="mono"
              :style="{ color: daysLeft(row.expireAt) < 0 ? '#ef4444' : daysLeft(row.expireAt) <= 7 ? '#f59e0b' : '#52525b' }"
            >
              {{ daysLeft(row.expireAt) < 0 ? `逾期 ${-daysLeft(row.expireAt)} 天` : `${daysLeft(row.expireAt)} 天` }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="自动续期" width="100">
          <template #default="{ row }">
            <el-tag :type="row.autoRenew ? 'success' : 'info'" size="small">{{ row.autoRenew ? '已开启' : '未开启' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="流失风险" width="110">
          <template #default="{ row }">
            <el-tooltip
              v-if="riskMap[row.id]"
              :content="(riskMap[row.id].factors || []).join('；') || '无异常因子'"
              placement="top"
            >
              <el-tag :type="riskTag(riskMap[row.id].risk)" size="small">{{ riskMap[row.id].risk }}</el-tag>
            </el-tooltip>
            <span v-else class="text-3">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="renewOne(row)">续期</el-button>
            <el-button link size="small" @click="openScript(row.id, row.name)">AI 话术</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- P0-2 续期订单（客户自助下单 → 服务商确认到账） -->
    <div class="card mb-20">
      <div class="card-title">续期订单（自助下单）</div>
      <p class="text-3" style="font-size: 12px; margin: 4px 0 12px">
        客户在客户端自助续期并提交对公汇款凭证后，订单进入「待确认」；确认到账后自动顺延到期日。
      </p>
      <el-table :data="pendingOrders" size="small">
        <el-table-column prop="orderNo" label="订单号" width="190">
          <template #default="{ row }"><span class="mono">{{ row.orderNo }}</span></template>
        </el-table-column>
        <el-table-column prop="targetName" label="客户" min-width="140" />
        <el-table-column label="续期" width="90">
          <template #default="{ row }">{{ row.months }} 个月</template>
        </el-table-column>
        <el-table-column label="金额" width="90">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="凭证" min-width="150">
          <template #default="{ row }">
            <span class="mono" style="font-size: 12px">{{ row.proofNo || '（待提交）' }}</span>
            <div class="text-3" style="font-size: 12px">{{ row.proofNote }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === '待确认' ? 'warning' : 'info'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="row.status !== '待确认'" @click="confirmOrder(row)">
              确认到账
            </el-button>
            <el-button link type="danger" size="small" @click="rejectOrder(row)">驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="!pendingOrders.length" class="text-3" style="font-size: 12px; margin: 8px 0">暂无待处理订单。</p>
    </div>

    <!-- P2-1 授权盘点 -->
    <div v-if="audit" class="card">
      <div class="card-title">授权盘点（闲置 / 超装嫌疑）</div>
      <div class="kpi-row" style="margin: 12px 0">
        <div class="card kpi-card"><div class="kpi-label">在管客户</div><div class="kpi-value">{{ audit.summary.total }}</div></div>
        <div class="card kpi-card"><div class="kpi-label">闲置 ≥30 天</div><div class="kpi-value" style="color: #f59e0b">{{ audit.summary.idle }}</div></div>
        <div class="card kpi-card"><div class="kpi-label">半闲置（使用率 &lt;30%）</div><div class="kpi-value">{{ audit.summary.underused }}</div></div>
        <div class="card kpi-card"><div class="kpi-label">超装嫌疑</div><div class="kpi-value" style="color: #ef4444">{{ audit.summary.suspect }}</div></div>
      </div>
      <el-table :data="audit.idle.slice(0, 8)" size="small" v-if="audit.idle.length">
        <el-table-column prop="hotel" label="闲置客户（≥30 天无心跳）" min-width="180" />
        <el-table-column prop="code" label="编号" width="110"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="idleDays" label="闲置天数" width="90" />
        <el-table-column prop="expireAt" label="到期日" width="110"><template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template></el-table-column>
      </el-table>
      <p v-else class="text-3" style="font-size: 12px; margin: 8px 0">暂无闲置授权，使用健康。</p>
    </div>

    <!-- AI 话术弹窗 -->
    <el-dialog v-model="scriptDialog" :title="`续期跟进话术 · ${scriptHotel}`" width="520px">
      <div style="white-space: pre-wrap; line-height: 1.8; font-size: 13px">{{ scriptText }}</div>
      <template #footer>
        <el-button @click="copyScript">复制话术</el-button>
        <el-button type="primary" @click="scriptDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.auto-panel {
  display: flex; justify-content: space-between; align-items: center;
  padding: 18px 24px;
}
.auto-title { font-size: 15px; font-weight: 600; }
.batch-bar {
  display: flex; align-items: center; gap: 12px;
  background: #ebf4ff; border: 1px solid #bfdbfe; border-radius: 8px;
  padding: 10px 16px; margin-bottom: 12px; font-size: 13px;
}
</style>
