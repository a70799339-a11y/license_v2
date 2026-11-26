<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Alert, type AlertGroup } from '../api/client'
import { downloadCsv } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { fmtBJ } from '../utils/time'

const auth = useAuthStore()
// T6：服务端分页（stats 随分页响应回传，统计卡片仍为全量口径）
const items = ref<Alert[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(true)
const levelFilter = ref('')
const statusFilter = ref('')
const viewMode = ref<'detail' | 'group'>('group')
const groups = ref<AlertGroup[]>([])
const stats = ref({ pending: 0, handling: 0, done: 0, urgent: 0 })

async function loadPage() {
  const r = await api.listAlertsPage({
    page: page.value,
    pageSize: pageSize.value,
    level: levelFilter.value || undefined,
    status: statusFilter.value || undefined,
  })
  items.value = r.items
  total.value = r.total
  stats.value = r.stats
}

onMounted(async () => {
  try {
    await loadPage()
    groups.value = await api.alertGroups()
  } finally {
    loading.value = false
  }
})

function onFilterChange() {
  page.value = 1
  loadPage()
}
function onPageChange(p: number) {
  page.value = p
  loadPage()
}
function onSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  loadPage()
}

function levelType(l: Alert['level']) {
  return l === '紧急' ? 'danger' : l === '高' ? 'warning' : 'info'
}
function statusType(s: Alert['status']) {
  return s === '待处理' ? 'danger' : s === '处理中' ? 'warning' : 'success'
}

// 处置闭环：弹窗填备注 → 落库 + 推企微
const handleDialog = ref(false)
const handleAlert = ref<Alert | null>(null)
const handleNote = ref('')

function openHandle(a: Alert) {
  handleAlert.value = a
  handleNote.value = ''
  handleDialog.value = true
}

async function confirmHandle() {
  if (!handleAlert.value) return
  try {
    await api.handleAlert(handleAlert.value.id, handleNote.value)
    handleDialog.value = false
    await loadPage()
    groups.value = await api.alertGroups()
    ElMessage.success('已处置，结果已推送企微')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  }
}

// AI 解读（本地大模型，经审计网关留痕）
const aiDialog = ref(false)
const aiLoading = ref(false)
const aiResult = ref('')
const aiMeta = ref('')
const aiAlert = ref<Alert | null>(null)

async function aiExplain(a: Alert) {
  aiAlert.value = a
  aiResult.value = ''
  aiMeta.value = ''
  aiDialog.value = true
  aiLoading.value = true
  try {
    const r = await api.aiExplainAlert({ alertId: a.id, rule: a.rule, hotelName: a.hotelName, detail: a.detail })
    aiResult.value = r.explain || '（无输出）'
    aiMeta.value = `模型：${r.audit.model} · 状态：${r.audit.status}（本次调用已记入大模型审计日志）`
  } catch (e) {
    aiResult.value = e instanceof Error ? e.message : '调用失败'
  } finally {
    aiLoading.value = false
  }
}

function groupLevel(l: string) {
  return l === '紧急' ? 'danger' : l === '高' ? 'warning' : 'info'
}
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">AI 智能风控</h1>
        <p class="page-sub">授权违规与使用行为双重检测，自动预警与处置</p>
      </div>
      <el-button @click="downloadCsv('/reports/alerts.csv', '告警报表.csv')">导出报表</el-button>
      <el-button>风控规则配置</el-button>
    </div>

    <div class="mode-bar">
      <el-radio-group v-model="viewMode" size="small">
        <el-radio-button value="group">聚合视图</el-radio-button>
        <el-radio-button value="detail">明细视图</el-radio-button>
      </el-radio-group>
      <span class="muted" style="font-size: 12px">
        {{ viewMode === 'group' ? '按规则聚合，规模上去后一眼看清哪类问题最多' : '逐条告警明细' }}
      </span>
    </div>

    <!-- 聚合视图（P2 告警聚合） -->
    <div v-if="viewMode === 'group'" class="card">
      <el-table :data="groups" border>
        <el-table-column label="等级" width="80">
          <template #default="{ row }">
            <el-tag :type="groupLevel(row.level)" size="small">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rule" label="告警规则" min-width="180" />
        <el-table-column label="总次数" width="90" align="center">
          <template #default="{ row }"><b>{{ row.count }}</b></template>
        </el-table-column>
        <el-table-column label="待处理" width="90" align="center">
          <template #default="{ row }">
            <span :style="{ color: row.pending ? '#ef4444' : '#10b981' }">{{ row.pending }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="hotelCount" label="涉及酒店" width="100" align="center" />
        <el-table-column prop="latest" label="最近发生" width="160">
          <template #default="{ row }"><span class="mono" style="font-size:12px">{{ row.latest }}</span></template>
        </el-table-column>
        <el-table-column prop="sample" label="样例" min-width="220" show-overflow-tooltip />
      </el-table>
    </div>

    <div class="kpi-row">
      <div class="card kpi-card">
        <div class="kpi-label">待处理</div>
        <div class="kpi-value" style="color: #ef4444">{{ stats.pending }}</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">处理中</div>
        <div class="kpi-value" style="color: #f59e0b">{{ stats.handling }}</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">今日已处置</div>
        <div class="kpi-value" style="color: #10b981">{{ stats.done }}</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">紧急告警</div>
        <div class="kpi-value" style="color: #ef4444">{{ stats.urgent }}</div>
      </div>
    </div>

    <div v-if="viewMode === 'detail'" class="card filter-bar mb-20">
      <el-radio-group v-model="levelFilter" @change="onFilterChange">
        <el-radio-button value="">全部等级</el-radio-button>
        <el-radio-button value="紧急">紧急</el-radio-button>
        <el-radio-button value="高">高</el-radio-button>
        <el-radio-button value="中">中</el-radio-button>
      </el-radio-group>
      <el-select v-model="statusFilter" placeholder="处置状态" style="width: 130px" clearable @change="onFilterChange">
        <el-option v-for="s in ['待处理', '处理中', '已处置']" :key="s" :label="s" :value="s" />
      </el-select>
    </div>

    <div v-if="viewMode === 'detail'" class="card">
      <el-table :data="items">
        <el-table-column label="等级" width="80">
          <template #default="{ row }">
            <el-tag :type="levelType(row.level)" size="small">{{ row.level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rule" label="触发规则" width="150" />
        <el-table-column prop="hotelName" label="酒店" width="180" />
        <el-table-column prop="detail" label="告警详情" min-width="220" show-overflow-tooltip />
        <el-table-column prop="time" label="时间" width="150">
          <template #default="{ row }"><span class="mono">{{ fmtBJ(row.time) }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="auth.canWrite"
              link type="warning" size="small"
              @click="aiExplain(row)"
            >AI 解读</el-button>
            <el-button
              v-if="row.status !== '已处置' && auth.canWrite"
              link type="primary" size="small"
              @click="openHandle(row)"
            >处置</el-button>
            <el-button v-else-if="row.status === '已处置'" link size="small" disabled>已处置</el-button>
            <el-button v-else link size="small" disabled>无权限</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="display: flex; justify-content: flex-end; margin-top: 12px">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>

    <el-dialog v-model="handleDialog" title="处置告警" width="480px">
      <template v-if="handleAlert">
        <p style="margin: 0 0 12px">
          <el-tag :type="levelType(handleAlert.level)" size="small">{{ handleAlert.level }}</el-tag>
          {{ handleAlert.rule }} ｜ {{ handleAlert.hotelName }}
        </p>
        <p class="muted" style="font-size: 12px; margin: 0 0 12px">{{ handleAlert.detail }}</p>
        <el-input v-model="handleNote" type="textarea" :rows="3" placeholder="处置备注（可空，将随结果推送企微）" />
      </template>
      <template #footer>
        <el-button @click="handleDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmHandle">确认处置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="aiDialog" title="告警 AI 解读（本地大模型）" width="620px">
      <template v-if="aiAlert">
        <p class="muted" style="font-size: 12px">
          {{ aiAlert.rule }} · {{ aiAlert.hotelName }} · {{ aiAlert.detail }}
        </p>
      </template>
      <div v-if="aiLoading" style="padding: 24px; text-align: center" class="muted">本地模型推理中，请稍候…</div>
      <pre v-else class="ai-result">{{ aiResult }}</pre>
      <p v-if="aiMeta" class="muted" style="font-size: 12px; margin-top: 10px">{{ aiMeta }}</p>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; padding: 16px 20px; align-items: center; }
.mode-bar { display: flex; gap: 12px; align-items: center; margin-bottom: 4px; }
.ai-result {
  margin: 0; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 14px 16px; white-space: pre-wrap; word-break: break-word;
  font-size: 13px; line-height: 1.8; max-height: 360px; overflow: auto;
}
</style>
