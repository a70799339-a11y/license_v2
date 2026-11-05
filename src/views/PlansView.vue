<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Plan } from '../api/client'

const plans = ref<Plan[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    plans.value = await api.listPlans()
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <h1 class="page-title">套餐管理</h1>
        <p class="page-sub">三档套餐分级，按门店设备数授权</p>
      </div>
      <el-button type="primary">新建套餐</el-button>
    </div>

    <div class="plan-row">
      <div v-for="p in plans" :key="p.id" class="card plan-card">
        <div class="plan-head" :style="{ borderColor: p.color }">
          <div class="plan-name">{{ p.name }}</div>
          <div class="plan-price">
            <span class="currency">¥</span>
            <span class="amount mono">{{ p.price.toLocaleString() }}</span>
            <span class="period">/ 月</span>
          </div>
          <div class="text-3" style="font-size: 12px">年付 ¥{{ p.yearlyPrice.toLocaleString() }}</div>
        </div>
        <div class="plan-meta">
          <div class="meta-item">
            <span class="text-2">设备上限</span>
            <b class="mono">{{ p.maxDevices }}</b>
          </div>
          <div class="meta-item">
            <span class="text-2">在用酒店</span>
            <b class="mono">{{ p.hotels }}</b>
          </div>
        </div>
        <ul class="features">
          <li v-for="f in p.features" :key="f">{{ f }}</li>
        </ul>
        <el-button style="width: 100%" :type="p.name === '专业版' ? 'primary' : 'default'">
          {{ p.name === '专业版' ? '推荐方案' : '编辑套餐' }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.plan-row { display: flex; gap: 20px; }
.plan-card { flex: 1; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.plan-head { border-top: 3px solid; padding-top: 16px; }
.plan-name { font-size: 16px; font-weight: 600; }
.plan-price { margin-top: 8px; display: flex; align-items: baseline; gap: 2px; }
.currency { font-size: 16px; font-weight: 600; }
.amount { font-size: 32px; font-weight: 600; }
.period { font-size: 13px; color: #a1a1aa; margin-left: 4px; }
.plan-meta {
  display: flex; gap: 24px; padding: 12px 0;
  border-top: 1px solid #f0f0f2; border-bottom: 1px solid #f0f0f2;
}
.meta-item { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.features { margin: 0; padding: 0; list-style: none; flex: 1; display: flex; flex-direction: column; gap: 10px; }
.features li {
  font-size: 13px; color: #52525b; padding-left: 20px; position: relative;
}
.features li::before {
  content: '✓'; position: absolute; left: 0; color: #10b981; font-weight: 600;
}
</style>
