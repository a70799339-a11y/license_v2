<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type License } from '../api/client'

const licenses = ref<License[]>([])
const loading = ref(true)
const graceHours = ref('72')

onMounted(async () => {
  try {
    const [l, s] = await Promise.all([api.listLicenses(), api.getSettings()])
    licenses.value = l
    if (s.heartbeat_timeout_hours) graceHours.value = s.heartbeat_timeout_hours
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
</style>
