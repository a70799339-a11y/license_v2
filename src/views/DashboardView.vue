<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Hotel } from '../api/client'

const stats = ref({ total: 0, devices: 0, expiring: 0, alerts: 0 })
const soonExpire = ref<Hotel[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    const [s, hotels] = await Promise.all([api.stats(), api.listHotels()])
    stats.value = s
    soonExpire.value = hotels
      .filter(h => h.status === '即将到期' || h.status === '已过期')
      .sort((a, b) => a.expireAt.localeCompare(b.expireAt))
  } finally {
    loading.value = false
  }
})

// 近 7 月授权增长（演示数据）
const trend = [
  { m: '5月', v: 52 }, { m: '6月', v: 58 }, { m: '7月', v: 61 },
  { m: '8月', v: 67 }, { m: '9月', v: 72 }, { m: '10月', v: 78 }, { m: '11月', v: 83 },
]
const maxV = 90
const points = trend.map((t, i) => {
  const x = 50 + i * 113
  const y = 230 - (t.v / maxV) * 180
  return { ...t, x, y }
})
const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x} ${p.y}`).join(' ')
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">经营总览</h1>
        <p class="page-sub">授权规模、到期风险与风控态势一览</p>
      </div>
    </div>

    <div class="kpi-row">
      <div class="card kpi-card">
        <div class="kpi-label">接入酒店</div>
        <div class="kpi-value">{{ stats.total }}</div>
        <div class="kpi-delta up">较上月 +3</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">激活设备</div>
        <div class="kpi-value">{{ stats.devices }}</div>
        <div class="kpi-delta up">较上月 +12%</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">30 天内到期</div>
        <div class="kpi-value" style="color: #f59e0b">{{ stats.expiring }}</div>
        <div class="kpi-delta down">需跟进续期</div>
      </div>
      <div class="card kpi-card">
        <div class="kpi-label">待处理告警</div>
        <div class="kpi-value" style="color: #ef4444">{{ stats.alerts }}</div>
        <div class="kpi-delta down">含紧急 1 条</div>
      </div>
    </div>

    <div class="grid">
      <div class="card chart-card">
        <div class="card-title">授权增长趋势（近 7 个月）</div>
        <svg width="100%" viewBox="0 0 760 260" preserveAspectRatio="xMidYMid meet">
          <path d="M50 40 L50 230 L730 230" stroke="#d4d4d8" stroke-width="2" fill="none" />
          <path
            d="M50 190 L730 190 M50 150 L730 150 M50 110 L730 110 M50 70 L730 70"
            stroke="#e5e5e5" stroke-width="2" fill="none"
          />
          <path :d="linePath" stroke="#3b82f6" stroke-width="3" fill="none" />
          <g fill="#3b82f6">
            <circle v-for="p in points" :key="p.m" :cx="p.x" :cy="p.y" r="5" />
          </g>
          <g fill="#a1a1aa" font-size="12" text-anchor="middle" font-family="Noto Sans SC">
            <text v-for="p in points" :key="p.m" :x="p.x" y="252">{{ p.m }}</text>
          </g>
        </svg>
      </div>

      <div class="card list-card">
        <div class="card-title">到期风险提醒</div>
        <div v-for="h in soonExpire" :key="h.id" class="expire-row">
          <div>
            <div class="hotel-name">{{ h.name }}</div>
            <div class="text-3" style="font-size: 12px">{{ h.group }} · {{ h.city }}</div>
          </div>
          <div class="text-right">
            <el-tag :type="h.status === '已过期' ? 'danger' : 'warning'" size="small">{{ h.status }}</el-tag>
            <div class="mono text-3" style="font-size: 12px; margin-top: 4px">{{ h.expireAt }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: flex; gap: 16px; }
.chart-card { flex: 1.5; padding: 20px; }
.list-card { flex: 1; padding: 20px; }
.card-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; }
.expire-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 0; border-bottom: 1px solid #f0f0f2;
}
.expire-row:last-child { border-bottom: none; }
.hotel-name { font-weight: 500; }
.text-right { text-align: right; }
</style>
