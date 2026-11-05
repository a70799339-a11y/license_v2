<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const username = ref('admin')
const password = ref('admin123')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  loading.value = true
  await new Promise(r => setTimeout(r, 300))
  if (await auth.login(username.value, password.value)) {
    router.push('/dashboard')
  } else {
    error.value = '账号或密码错误（演示账号 admin / admin123）'
  }
  loading.value = false
}
</script>

<template>
  <div class="login-page">
    <div class="login-card card">
      <div class="brand">
        <div class="logo-mark">智</div>
        <div>
          <div class="title">酒店AI管理</div>
          <div class="sub">License 授权管理系统</div>
        </div>
      </div>
      <el-form @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="username" placeholder="请输入账号" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" placeholder="请输入密码" size="large" show-password @keyup.enter="submit" />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb-20" />
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="submit">
          登 录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(160deg, #eff6ff 0%, #fafafa 60%);
}
.login-card { width: 400px; padding: 40px; }
.brand { display: flex; gap: 12px; align-items: center; margin-bottom: 32px; }
.logo-mark {
  width: 44px; height: 44px; border-radius: 10px; background: #3b82f6;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: 600;
}
.title { font-size: 20px; font-weight: 600; }
.sub { font-size: 13px; color: #a1a1aa; margin-top: 2px; }
</style>
