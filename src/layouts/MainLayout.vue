<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menu = computed(() => {
  const items = [
    { path: '/dashboard', label: '经营总览' },
    { path: '/customers', label: '客户管理' },
    { path: '/licenses', label: '授权管理' },
    { path: '/plans', label: '套餐管理' },
    { path: '/risk', label: 'AI 智能风控' },
    { path: '/renewals', label: '到期续期' },
    { path: '/groups', label: '集团管理' },
    { path: '/security', label: '安全设计' },
  ]
  if (auth.isAdmin) items.push({ path: '/users', label: '用户管理' })
  return items
})
const roleLabel = computed(
  () => ({ admin: '管理员', operator: '运营', viewer: '只读' }[auth.role] ?? auth.role)
)
const active = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) ?? '')

function go(path: string) { router.push(path) }
function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-mark">智</div>
        <span class="logo-text">酒店AI管理</span>
      </div>
      <nav class="nav">
        <div
          v-for="item in menu"
          :key="item.path"
          class="nav-item"
          :class="{ active: active === item.path }"
          @click="go(item.path)"
        >
          {{ item.label }}
          <span v-if="item.path === '/risk'" class="badge">3</span>
        </div>
      </nav>
    </aside>
    <div class="main">
      <header class="header">
        <div class="crumb">
          <span class="text-2">首页</span>
          <span class="sep">/</span>
          <span class="cur">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <el-input placeholder="搜索客户 / 授权码" style="width: 200px" clearable />
          <el-button text @click="logout">退出登录</el-button>
          <el-tag size="small" :type="auth.isAdmin ? 'danger' : auth.canWrite ? 'primary' : 'info'">{{ roleLabel }}</el-tag>
          <el-avatar :size="32" style="background: #3b82f6">{{ auth.user?.name?.[0] ?? '运' }}</el-avatar>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell { display: flex; height: 100%; }
.sidebar {
  width: 232px; flex-shrink: 0;
  background: #fff; border-right: 1px solid #e4e4e7;
  padding: 20px 12px; display: flex; flex-direction: column; gap: 4px;
}
.logo { display: flex; align-items: center; gap: 10px; padding: 0 8px 8px; }
.logo-mark {
  width: 32px; height: 32px; border-radius: 8px; background: #3b82f6;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 15px;
}
.logo-text { font-size: 16px; font-weight: 600; }
.nav { display: flex; flex-direction: column; gap: 4px; margin-top: 12px; }
.nav-item {
  height: 40px; display: flex; align-items: center; padding: 0 12px;
  border-radius: 6px; cursor: pointer; font-size: 14px; color: #52525b;
  gap: 8px;
}
.nav-item:hover { background: #f4f4f5; }
.nav-item.active { background: #3b82f6; color: #fff; }
.badge {
  margin-left: auto; background: #ef4444; color: #fff;
  font-size: 12px; border-radius: 10px; min-width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center; padding: 0 6px;
}
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.header {
  height: 64px; background: #fff; border-bottom: 1px solid #e4e4e7;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px; flex-shrink: 0;
}
.crumb { display: flex; gap: 6px; font-size: 14px; }
.sep { color: #d4d4d8; }
.cur { color: #09090b; font-weight: 500; }
.header-right { display: flex; align-items: center; gap: 16px; }
.content { flex: 1; overflow: auto; }
</style>
