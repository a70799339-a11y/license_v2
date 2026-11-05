<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, type HotelDetail } from '../api/client'

const route = useRoute()
const router = useRouter()
const detail = ref<HotelDetail | null>(null)
const loading = ref(true)
const activeTab = ref('info')

onMounted(async () => {
  try {
    detail.value = await api.hotelDetail(route.params.id as string)
  } catch {
    ElMessage.error('客户不存在')
    router.push('/customers')
  } finally {
    loading.value = false
  }
})

const hotel = computed(() => detail.value?.hotel)

function statusType(s: string) {
  return s === '正常' ? 'success' : s === '即将到期' ? 'warning' : s === '已过期' ? 'danger' : 'info'
}

async function renew() {
  if (!hotel.value) return
  await api.batchRenew([hotel.value.id])
  detail.value = await api.hotelDetail(hotel.value.id)
  ElMessage.success('已续期一年')
}

async function toggleAutoRenew(val: boolean) {
  if (!hotel.value) return
  await api.updateHotel(hotel.value.id, { autoRenew: val })
  ElMessage.success(val ? '已开启自动续期' : '已关闭自动续期')
}
</script>

<template>
  <div class="page" v-loading="loading">
    <template v-if="hotel">
      <div class="page-head">
        <div style="display: flex; align-items: center; gap: 12px">
          <el-button text @click="router.push('/customers')">← 返回</el-button>
          <div>
            <h1 class="page-title">{{ hotel.name }}</h1>
            <p class="page-sub">{{ hotel.code }} · {{ hotel.group }} · {{ hotel.city }}</p>
          </div>
        </div>
        <div style="display: flex; gap: 8px">
          <el-button @click="renew">一键续期</el-button>
          <el-button type="primary">编辑信息</el-button>
        </div>
      </div>

      <!-- 概要卡 -->
      <div class="kpi-row">
        <div class="card kpi-card">
          <div class="kpi-label">授权状态</div>
          <div style="margin-top: 8px">
            <el-tag :type="statusType(hotel.status)" size="large">{{ hotel.status }}</el-tag>
          </div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-label">套餐</div>
          <div class="kpi-value" style="font-size: 22px">{{ hotel.plan }}</div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-label">到期时间</div>
          <div class="kpi-value mono" style="font-size: 20px">{{ hotel.expireAt }}</div>
          <div style="margin-top: 6px">
            <el-switch
              :model-value="hotel.autoRenew"
              size="small"
              active-text="自动续期"
              @change="toggleAutoRenew"
            />
          </div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-label">设备占用</div>
          <div class="kpi-value mono">{{ hotel.devices }}/{{ hotel.maxDevices }}</div>
        </div>
        <div class="card kpi-card">
          <div class="kpi-label">最近活跃</div>
          <div class="kpi-value mono" style="font-size: 15px">{{ hotel.lastActive || '—' }}</div>
        </div>
      </div>

      <!-- Tabs -->
      <div class="card">
        <el-tabs v-model="activeTab" style="padding: 0 20px">
          <el-tab-pane label="基本信息" name="info">
            <el-form label-width="100px" class="info-form">
              <el-form-item label="酒店全称">{{ hotel.name }}</el-form-item>
              <el-form-item label="客户编号"><span class="mono">{{ hotel.code }}</span></el-form-item>
              <el-form-item label="所属集团">{{ hotel.group }}</el-form-item>
              <el-form-item label="城市">{{ hotel.city }}</el-form-item>
              <el-form-item label="联系人">{{ hotel.contact }}</el-form-item>
              <el-form-item label="联系电话"><span class="mono">{{ hotel.phone }}</span></el-form-item>
              <el-form-item label="套餐类型">{{ hotel.plan }}</el-form-item>
              <el-form-item label="注册时间"><span class="mono">{{ hotel.createdAt }}</span></el-form-item>
              <el-form-item label="到期时间"><span class="mono">{{ hotel.expireAt }}</span></el-form-item>
              <el-form-item label="自动续期">{{ hotel.autoRenew ? '已开启' : '已关闭' }}</el-form-item>
            </el-form>
          </el-tab-pane>

          <el-tab-pane :label="`设备清单（${detail!.devices.length}）`" name="devices">
            <el-table :data="detail!.devices">
              <el-table-column prop="id" label="设备 ID" width="120">
                <template #default="{ row }"><span class="mono">{{ row.id }}</span></template>
              </el-table-column>
              <el-table-column prop="name" label="设备名称" width="150" />
              <el-table-column prop="os" label="操作系统" width="130" />
              <el-table-column prop="fingerprint" label="设备指纹" min-width="180">
                <template #default="{ row }"><span class="mono text-3">{{ row.fingerprint }}</span></template>
              </el-table-column>
              <el-table-column prop="lastActive" label="最近活跃" width="150">
                <template #default="{ row }"><span class="mono">{{ row.lastActive }}</span></template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.status === '在线' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="授权历史" name="licenses">
            <el-table :data="detail!.licenses">
              <el-table-column prop="code" label="授权码" width="160">
                <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
              </el-table-column>
              <el-table-column prop="plan" label="套餐" width="100" />
              <el-table-column prop="type" label="类型" width="80" />
              <el-table-column prop="issuedAt" label="签发" width="110">
                <template #default="{ row }"><span class="mono">{{ row.issuedAt }}</span></template>
              </el-table-column>
              <el-table-column prop="expireAt" label="到期" width="110">
                <template #default="{ row }"><span class="mono">{{ row.expireAt }}</span></template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.status === '生效中' ? 'success' : row.status === '已吊销' ? 'danger' : 'info'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="操作日志" name="logs">
            <div class="log-list">
              <div v-if="!detail!.alerts.length && !detail!.licenses.length" class="text-3">暂无记录</div>
              <el-timeline v-else>
                <el-timeline-item
                  v-for="l in detail!.licenses"
                  :key="l.id"
                  :timestamp="l.issuedAt"
                  placement="top"
                >
                  {{ l.type }}授权 {{ l.code }}（{{ l.plan }}）
                </el-timeline-item>
              </el-timeline>
            </div>
          </el-tab-pane>

          <el-tab-pane :label="`风控记录（${detail!.alerts.length}）`" name="alerts">
            <el-table :data="detail!.alerts">
              <el-table-column label="等级" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.level === '紧急' ? 'danger' : row.level === '高' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="rule" label="规则" width="160" />
              <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
              <el-table-column prop="time" label="时间" width="150">
                <template #default="{ row }"><span class="mono">{{ row.time }}</span></template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.status === '已处置' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </template>
  </div>
</template>

<style scoped>
.info-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 40px;
  padding: 8px 0 16px;
  max-width: 900px;
}
.log-list { padding: 16px 0; }
</style>
