<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type License } from '../api/client'

const all = ref<License[]>([])
const loading = ref(true)
const keyword = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = 5

onMounted(async () => {
  try {
    all.value = await api.listLicenses()
  } finally {
    loading.value = false
  }
})

const filtered = computed(() =>
  all.value.filter(l => {
    if (keyword.value && !l.hotelName.includes(keyword.value) && !l.code.includes(keyword.value)) return false
    if (statusFilter.value && l.status !== statusFilter.value) return false
    return true
  })
)
const paged = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize)
)

function statusType(s: License['status']) {
  return s === '生效中' ? 'success' : s === '待激活' ? 'warning' : s === '已吊销' ? 'danger' : 'info'
}

async function revoke(row: License) {
  try {
    const { value } = await ElMessageBox.prompt(
      `吊销授权 ${row.code}（${row.hotelName}）后客户端将立即失效，请填写吊销原因：`,
      '吊销确认',
      { confirmButtonText: '确认吊销', cancelButtonText: '取消', inputPlaceholder: '如：欠费停用 / 违规转租 / 申请退订' }
    )
    await api.revokeLicense(row.id, value || '手动吊销')
    all.value = await api.listLicenses()
    ElMessage.success('已吊销')
  } catch { /* 用户取消 */ }
}

const showIssue = ref(false)
// P1-3：features=授权功能模块（空=全开）；maxRooms=房量配额（0=不限）
const issueForm = ref({
  hotelId: '',
  plan: '基础版',
  type: '新签',
  months: 12,
  features: [] as string[],
  maxRooms: 0,
})
const hotels = ref<{ id: string; name: string }[]>([])

const MODULE_OPTIONS = [
  { key: 'ai_risk', label: 'AI 风控（ai_risk）' },
  { key: 'group_portal', label: '集团总端（group_portal）' },
  { key: 'report', label: '报表/简报（report）' },
  { key: 'api', label: 'API 对接（api）' },
  { key: 'push', label: '企微推送（push）' },
]

async function openIssue() {
  hotels.value = await api.listHotels()
  if (hotels.value.length && !issueForm.value.hotelId) issueForm.value.hotelId = hotels.value[0].id
  showIssue.value = true
}

async function issueLicense() {
  if (!issueForm.value.hotelId) {
    ElMessage.warning('请选择酒店')
    return
  }
  try {
    await api.createLicense(issueForm.value)
    all.value = await api.listLicenses()
    showIssue.value = false
    ElMessage.success('授权已发放')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '发放失败')
  }
}
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">授权管理</h1>
        <p class="page-sub">License 签发、激活与吊销全生命周期管理</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button>签发记录</el-button>
        <el-button type="primary" @click="openIssue">发放授权</el-button>
      </div>
    </div>

    <div class="card filter-bar mb-20">
      <el-input v-model="keyword" placeholder="搜索授权码 / 酒店名称" style="width: 220px" clearable />
      <el-select v-model="statusFilter" placeholder="授权状态" style="width: 130px" clearable>
        <el-option v-for="s in ['生效中', '待激活', '已吊销', '已过期']" :key="s" :label="s" :value="s" />
      </el-select>
    </div>

    <div class="card">
      <el-table :data="paged">
        <el-table-column label="授权码" width="160">
          <template #default="{ row }"><span class="mono" style="font-weight: 500">{{ row.code }}</span></template>
        </el-table-column>
        <el-table-column prop="hotelName" label="酒店" min-width="180" />
        <el-table-column label="套餐" width="90">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.plan }}</el-tag></template>
        </el-table-column>
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ row.type }}</template>
        </el-table-column>
        <el-table-column prop="issuedAt" label="签发日期" width="110">
          <template #default="{ row }"><span class="mono">{{ row.issuedAt }}</span></template>
        </el-table-column>
        <el-table-column prop="expireAt" label="到期日期" width="110">
          <template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template>
        </el-table-column>
        <el-table-column label="设备占用" width="100">
          <template #default="{ row }"><span class="mono">{{ row.devices }}/{{ row.maxDevices }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small">详情</el-button>
            <el-button link type="danger" size="small" @click="revoke(row)">吊销</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <span class="text-3">共 {{ filtered.length }} 条，每页 {{ pageSize }} 条</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="filtered.length"
          layout="prev, pager, next"
          size="small"
        />
      </div>
    </div>

    <!-- 发放授权对话框 -->
    <el-dialog v-model="showIssue" title="发放授权" width="480px">
      <el-form :model="issueForm" label-width="90px">
        <el-form-item label="酒店" required>
          <el-select v-model="issueForm.hotelId" style="width: 100%" filterable>
            <el-option v-for="h in hotels" :key="h.id" :label="h.name" :value="h.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="套餐">
          <el-select v-model="issueForm.plan" style="width: 100%">
            <el-option v-for="p in ['基础版', '专业版', '旗舰版']" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="issueForm.type">
            <el-radio-button v-for="t in ['新签', '续期', '升级']" :key="t" :value="t">{{ t }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="有效期">
          <el-select v-model="issueForm.months" style="width: 100%">
            <el-option v-for="m in [1, 3, 6, 12, 24]" :key="m" :label="`${m} 个月`" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能模块">
          <el-checkbox-group v-model="issueForm.features">
            <el-checkbox v-for="m in MODULE_OPTIONS" :key="m.key" :value="m.key" :label="m.key">
              {{ m.label }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="text-3" style="font-size: 12px; line-height: 1.6">
            全不勾 = 全部模块开放（兼容存量）；勾选后仅开放所选模块（E5：先拆 ai_risk / group_portal 验证）。
          </div>
        </el-form-item>
        <el-form-item label="房量配额">
          <el-input-number v-model="issueForm.maxRooms" :min="0" :max="9999" />
          <span class="text-3" style="font-size: 12px; margin-left: 8px">0 = 不限（按房量计价时填写）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showIssue = false">取消</el-button>
        <el-button type="primary" @click="issueLicense">签发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; padding: 16px 20px; }
.pager {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 20px;
}
</style>
