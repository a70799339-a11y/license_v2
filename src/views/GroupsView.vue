<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Group, type Hotel } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const groups = ref<Group[]>([])
const loading = ref(true)

// 创建集团
const showCreate = ref(false)
const form = ref({ name: '', contact: '', phone: '', plan: '旗舰版', expireAt: '2027-12-31' })

// 集团详情
const detail = ref<Group | null>(null)
const members = ref<Hotel[]>([])
const showDetail = ref(false)
const allHotels = ref<Hotel[]>([])
const selectedHotels = ref<string[]>([])
const showAssign = ref(false)
const hookUrl = ref('')

async function load() {
  loading.value = true
  try {
    groups.value = await api.listGroups()
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.name) {
    ElMessage.warning('请填写集团名称')
    return
  }
  try {
    const res = await api.createGroup(form.value)
    ElMessage.success(`已创建，集团码 ${res.code}（总端激活用）`)
    showCreate.value = false
    form.value = { name: '', contact: '', phone: '', plan: '旗舰版', expireAt: '2027-12-31' }
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  }
}

async function openDetail(g: Group) {
  detail.value = g
  const res = await api.groupDetail(g.id)
  members.value = res.members
  showDetail.value = true
  try {
    const cfg = await api.wecomConfig()
    hookUrl.value = cfg.groupHooks[g.id] || ''
  } catch {
    hookUrl.value = ''
  }
}

async function saveHook() {
  if (!detail.value) return
  try {
    await api.wecomGroupHook(detail.value.id, hookUrl.value)
    ElMessage.success('集团企微 webhook 已保存（本集团告警/日报将推送到该群）')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function openAssign() {
  allHotels.value = await api.listHotels()
  selectedHotels.value = []
  showAssign.value = true
}

async function assign() {
  if (!detail.value || !selectedHotels.value.length) return
  try {
    await api.assignHotels(selectedHotels.value, detail.value.id)
    ElMessage.success(`已划入 ${selectedHotels.value.length} 家门店`)
    showAssign.value = false
    await openDetail(detail.value)
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '调整失败')
  }
}

async function renewAll() {
  if (!detail.value) return
  try {
    const res = await api.groupRenew(detail.value.id)
    ElMessage.success(`集团续期完成：${res.renewed} 家门店续期至 2027-12-31`)
    await openDetail(detail.value)
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '续期失败')
  }
}

function statusType(s: string) {
  return s === '正常' ? 'success' : s === '即将到期' ? 'warning' : s === '已过期' ? 'danger' : 'info'
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">集团管理</h1>
        <p class="page-sub">总/子关系管理：集团总端用「集团码」激活客户端，统一查看与管理旗下门店</p>
      </div>
      <el-button v-if="auth.canWrite" type="primary" @click="showCreate = true">新建集团</el-button>
    </div>

    <el-table :data="groups" border @row-click="openDetail" class="cursor-pointer">
      <el-table-column prop="code" label="集团码" width="110">
        <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
      </el-table-column>
      <el-table-column prop="name" label="集团名称" min-width="160" />
      <el-table-column prop="contact" label="联系人" width="110" />
      <el-table-column label="门店数" width="90" align="center">
        <template #default="{ row }"><b>{{ row.memberCount }}</b></template>
      </el-table-column>
      <el-table-column prop="deviceCount" label="设备总数" width="100" align="center" />
      <el-table-column label="到期门店" width="100" align="center">
        <template #default="{ row }">
          <span :style="{ color: row.expiringSoon ? '#ef4444' : '#10b981' }">{{ row.expiringSoon }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="expireAt" label="集团到期" width="110">
        <template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建集团 -->
    <el-dialog v-model="showCreate" title="新建集团" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="集团名称" required>
          <el-input v-model="form.name" placeholder="如：华住集团" />
        </el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item label="集团套餐">
          <el-select v-model="form.plan" style="width: 100%">
            <el-option v-for="p in ['基础版', '专业版', '旗舰版']" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="到期时间"><el-input v-model="form.expireAt" placeholder="2027-12-31" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- 集团详情 -->
    <el-drawer v-model="showDetail" :title="detail?.name" size="60%">
      <div v-if="detail" class="detail-wrap">
        <div class="kv-row">
          <div><span class="muted">集团码</span><b class="mono">{{ detail.code }}</b></div>
          <div><span class="muted">套餐</span><b>{{ detail.plan }}</b></div>
          <div><span class="muted">到期</span><b class="mono">{{ detail.expireAt }}</b></div>
          <div><span class="muted">门店 / 设备</span><b>{{ detail.memberCount }} / {{ detail.deviceCount }}</b></div>
        </div>
        <p class="muted small">集团总端激活方式：客户端输入集团码 <b class="mono">{{ detail.code }}</b> 即可进入总端模式。</p>
        <div class="hook-row">
          <span class="muted small">集团企微群 webhook</span>
          <el-input v-model="hookUrl" placeholder="集团群机器人 webhook（本集团告警/日报推送）" size="small" style="flex: 1" show-password />
          <el-button size="small" type="primary" @click="saveHook">保存</el-button>
        </div>
        <div class="btn-row">
          <el-button v-if="auth.canWrite" type="primary" size="small" @click="renewAll">全部门店续期一年</el-button>
          <el-button v-if="auth.canWrite" size="small" @click="openAssign">划入门店</el-button>
        </div>
        <el-table :data="members" border size="small">
          <el-table-column prop="code" label="编号" width="100">
            <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
          </el-table-column>
          <el-table-column prop="name" label="门店" min-width="150" />
          <el-table-column prop="city" label="城市" width="80" />
          <el-table-column prop="plan" label="套餐" width="90" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="设备" width="80">
            <template #default="{ row }"><span class="mono">{{ row.devices }}/{{ row.maxDevices }}</span></template>
          </el-table-column>
          <el-table-column prop="expireAt" label="到期" width="110">
            <template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <!-- 划入门店 -->
    <el-dialog v-model="showAssign" title="划入门店" width="560px">
      <el-select v-model="selectedHotels" multiple filterable placeholder="选择门店（可多选）" style="width: 100%">
        <el-option v-for="h in allHotels" :key="h.id" :label="`${h.name}（${h.code}）`" :value="h.id" />
      </el-select>
      <template #footer>
        <el-button @click="showAssign = false">取消</el-button>
        <el-button type="primary" @click="assign">确认划入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cursor-pointer :deep(.el-table__row) { cursor: pointer; }
.detail-wrap { display: flex; flex-direction: column; gap: 14px; }
.kv-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kv-row > div { display: flex; flex-direction: column; gap: 2px; }
.btn-row { display: flex; gap: 8px; }
.hook-row { display: flex; gap: 8px; align-items: center; }
.muted { color: #a1a1aa; font-size: 12px; }
.small { font-size: 12px; }
</style>
