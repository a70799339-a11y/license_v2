<script setup lang="ts">
import { h, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, type Hotel } from '../api/client'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

// ---------- 服务端分页 + 筛选（P2：支撑 1000+ 家） ----------
const rows = ref<Hotel[]>([])
const total = ref(0)
const loading = ref(true)
const keyword = ref('')
const statusFilter = ref('')
const planFilter = ref('')
const page = ref(1)
const pageSize = ref(100)
const selected = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  try {
    const res = await api.listHotelsPage({
      page: page.value,
      pageSize: pageSize.value,
      keyword: keyword.value,
      status: statusFilter.value,
      plan: planFilter.value,
    })
    rows.value = res.items
    total.value = res.total
    selected.value = new Set()
  } finally {
    loading.value = false
  }
}

let timer: ReturnType<typeof setTimeout> | null = null
watch([keyword, statusFilter, planFilter], () => {
  page.value = 1
  if (timer) clearTimeout(timer)
  timer = setTimeout(load, 300)
})
watch([page, pageSize], load)
onMounted(load)

// ---------- 虚拟滚动表格列（el-table-v2） ----------
function statusType(s: Hotel['status']) {
  return s === '正常' ? '#10b981' : s === '即将到期' ? '#f59e0b' : s === '已过期' ? '#ef4444' : '#a1a1aa'
}

function toggle(id: string) {
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}
function toggleAll() {
  selected.value =
    selected.value.size === rows.value.length ? new Set() : new Set(rows.value.map(r => r.id))
}

const columns = [
  {
    key: 'sel',
    width: 46,
    title: '',
    align: 'center' as const,
    cellRenderer: ({ rowData }: any) =>
      h('input', {
        type: 'checkbox',
        checked: selected.value.has(rowData.id),
        disabled: !auth.canWrite,
        onClick: (e: Event) => {
          e.stopPropagation()
          toggle(rowData.id)
        },
      }),
  },
  {
    key: 'name',
    dataKey: 'name',
    title: '酒店 / 客户编号',
    width: 220,
    cellRenderer: ({ rowData }: any) =>
      h('div', { style: 'line-height:1.3' }, [
        h('div', { style: 'font-weight:500' }, rowData.name),
        h('div', { style: 'font-size:12px;color:#a1a1aa;font-family:monospace' }, rowData.code),
      ]),
  },
  { key: 'group', dataKey: 'group', title: '集团', width: 110 },
  { key: 'city', dataKey: 'city', title: '城市', width: 80 },
  { key: 'contact', dataKey: 'contact', title: '联系人', width: 90 },
  {
    key: 'plan',
    dataKey: 'plan',
    title: '套餐',
    width: 90,
    cellRenderer: ({ rowData }: any) =>
      h('span', { style: 'border:1px solid #e5e5e5;border-radius:4px;padding:1px 8px;font-size:12px' }, rowData.plan),
  },
  {
    key: 'status',
    dataKey: 'status',
    title: '状态',
    width: 96,
    cellRenderer: ({ rowData }: any) =>
      h('span', { style: `color:${statusType(rowData.status)};font-size:12px;font-weight:500` }, rowData.status),
  },
  {
    key: 'devices',
    title: '设备',
    width: 80,
    cellRenderer: ({ rowData }: any) =>
      h('span', { style: 'font-family:monospace;font-size:12px' }, `${rowData.devices}/${rowData.maxDevices}`),
  },
  {
    key: 'expireAt',
    dataKey: 'expireAt',
    title: '到期时间',
    width: 110,
    cellRenderer: ({ rowData }: any) =>
      h('span', { style: 'font-family:monospace;font-size:12px' }, rowData.expireAt),
  },
  {
    key: 'ops',
    title: '操作',
    width: 150,
    cellRenderer: ({ rowData }: any) =>
      h('div', { style: 'display:flex;gap:8px' }, [
        h(
          'button',
          { class: 'link-btn', onClick: () => router.push(`/customers/${rowData.id}`) },
          '详情'
        ),
        ...(auth.canWrite
          ? [
              h(
                'button',
                { class: 'link-btn', onClick: () => renewOne(rowData) },
                '续期'
              ),
            ]
          : []),
      ]),
  },
]

// ---------- 操作 ----------
function exportCsv() {
  const data = rows.value
  const head = ['酒店名称', '客户编号', '集团', '城市', '联系人', '套餐', '状态', '设备数', '到期时间']
  const body = data.map(h2 => [h2.name, h2.code, h2.group, h2.city, h2.contact, h2.plan, h2.status, `${h2.devices}/${h2.maxDevices}`, h2.expireAt])
  const csv = '﻿' + [head, ...body].map(r => r.join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = `客户列表-第${page.value}页.csv`
  a.click()
}

const showCreate = ref(false)
const createForm = ref({ name: '', code: '', group: '', city: '', contact: '', phone: '', plan: '基础版' })

async function createHotel() {
  if (!createForm.value.name || !createForm.value.code) {
    ElMessage.warning('请填写酒店名称和客户编号')
    return
  }
  try {
    await api.createHotel(createForm.value)
    showCreate.value = false
    createForm.value = { name: '', code: '', group: '', city: '', contact: '', phone: '', plan: '基础版' }
    ElMessage.success('新增客户成功')
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '新增失败')
  }
}

async function batchRenew() {
  const ids = [...selected.value]
  try {
    await api.batchRenew(ids)
    ElMessage.success(`已为 ${ids.length} 家酒店续期一年`)
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '批量续期失败')
  }
}

async function batchDisable() {
  const ids = [...selected.value]
  try {
    await api.batchDisable(ids)
    ElMessage.success(`已停用 ${ids.length} 家酒店`)
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '批量停用失败')
  }
}

async function renewOne(row: Hotel) {
  await api.batchRenew([row.id])
  ElMessage.success(`已为 ${row.name} 续期`)
  await load()
}
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">客户管理</h1>
        <p class="page-sub">共 {{ total }} 家酒店客户，服务端分页 + 虚拟滚动（支撑 1000+ 家）</p>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button @click="exportCsv">导出 CSV</el-button>
        <el-button v-if="auth.canWrite" type="primary" @click="showCreate = true">新增客户</el-button>
      </div>
    </div>

    <div class="card filter-bar mb-20">
      <el-input v-model="keyword" placeholder="搜索酒店名称 / 客户编号 / 集团" style="width: 240px" clearable />
      <el-select v-model="statusFilter" placeholder="授权状态" style="width: 130px" clearable>
        <el-option v-for="s in ['正常', '即将到期', '已过期', '已停用']" :key="s" :label="s" :value="s" />
      </el-select>
      <el-select v-model="planFilter" placeholder="套餐" style="width: 130px" clearable>
        <el-option v-for="p in ['基础版', '专业版', '旗舰版']" :key="p" :label="p" :value="p" />
      </el-select>
      <el-select v-model="pageSize" style="width: 110px">
        <el-option v-for="n in [50, 100, 200]" :key="n" :label="`${n} 条/页`" :value="n" />
      </el-select>
    </div>

    <div v-if="selected.size" class="batch-bar">
      <span>已选 <b>{{ selected.size }}</b> 项</span>
      <el-button size="small" type="primary" @click="batchRenew">批量续期一年</el-button>
      <el-button size="small" type="danger" @click="batchDisable">批量停用</el-button>
      <el-button size="small" text @click="selected = new Set()">取消</el-button>
    </div>

    <div class="card">
      <!-- 虚拟滚动表格：大数据量仅渲染可视区行 -->
      <el-table-v2
        :columns="columns"
        :data="rows"
        :width="1100"
        :height="560"
        :row-height="56"
        :header-height="44"
        fixed
      >
        <template #header>
          <div class="v2-header">
            <input type="checkbox" :checked="selected.size === rows.length && rows.length > 0" :disabled="!auth.canWrite" @change="toggleAll" />
            <span v-for="c in columns.slice(1)" :key="c.key" :style="{ width: `${c.width}px`, padding: '0 12px', textAlign: (c as any).align ?? 'left' }">{{ c.title }}</span>
          </div>
        </template>
      </el-table-v2>
      <div class="pager">
        <span class="text-3">共 {{ total }} 条，每页 {{ pageSize }} 条，虚拟滚动渲染</span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[50, 100, 200]"
          layout="sizes, prev, pager, next"
          size="small"
        />
      </div>
    </div>

    <!-- 新增客户对话框 -->
    <el-dialog v-model="showCreate" title="新增客户" width="520px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="酒店名称" required>
          <el-input v-model="createForm.name" placeholder="如：杭州西湖大酒店" />
        </el-form-item>
        <el-form-item label="客户编号" required>
          <el-input v-model="createForm.code" placeholder="如：HT-0041" />
        </el-form-item>
        <el-form-item label="所属集团">
          <el-input v-model="createForm.group" placeholder="如：华住集团" />
        </el-form-item>
        <el-form-item label="城市">
          <el-input v-model="createForm.city" placeholder="如：杭州" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="createForm.contact" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="createForm.phone" />
        </el-form-item>
        <el-form-item label="套餐">
          <el-select v-model="createForm.plan" style="width: 100%">
            <el-option v-for="p in ['基础版', '专业版', '旗舰版']" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="createHotel">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; gap: 12px; padding: 16px 20px; }
.batch-bar {
  display: flex; align-items: center; gap: 12px;
  background: #ebf4ff; border: 1px solid #bfdbfe; border-radius: 8px;
  padding: 10px 16px; margin-bottom: 12px; font-size: 13px;
}
.pager {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 20px;
}
.v2-header {
  display: flex; align-items: center; height: 44px;
  background: #fafafa; border-bottom: 1px solid #e5e5e5;
  font-size: 12px; color: #71717a; font-weight: 500;
}
.v2-header input { margin: 0 12px; }
:deep(.link-btn) {
  border: 0; background: none; color: #3b82f6; cursor: pointer; font-size: 12px; padding: 0;
}
:deep(.link-btn:hover) { text-decoration: underline; }
</style>
