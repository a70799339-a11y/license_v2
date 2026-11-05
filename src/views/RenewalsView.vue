<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Hotel } from '../api/client'

const hotels = ref<Hotel[]>([])
const settings = ref<Record<string, string>>({})
const loading = ref(true)
const selected = ref<Hotel[]>([])

onMounted(async () => {
  try {
    const [h, s] = await Promise.all([api.listHotels(), api.getSettings()])
    hotels.value = h
    settings.value = s
  } finally {
    loading.value = false
  }
})

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
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="renewOne(row)">续期</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
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
