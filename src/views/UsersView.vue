<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type User } from '../api/client'

const users = ref<User[]>([])
const dialog = ref(false)
const form = ref({ username: '', password: '', name: '', role: 'operator' })

async function load() {
  users.value = await api.listUsers()
}

async function create() {
  if (!form.value.username || !form.value.password || !form.value.name) {
    ElMessage.warning('请填写完整')
    return
  }
  try {
    await api.createUser(form.value)
    ElMessage.success('已创建')
    dialog.value = false
    form.value = { username: '', password: '', name: '', role: 'operator' }
    await load()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  }
}

const roleTag: Record<string, 'danger' | 'primary' | 'info'> = {
  admin: 'danger',
  operator: 'primary',
  viewer: 'info',
}
const roleName: Record<string, string> = {
  admin: '管理员',
  operator: '运营',
  viewer: '只读',
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="head">
      <div>
        <h2>用户管理</h2>
        <p class="muted">P2 权限分角色：admin 全部 / operator 运营 / viewer 只读（仅管理员可见此页）</p>
      </div>
      <el-button type="primary" @click="dialog = true">新增用户</el-button>
    </div>

    <el-table :data="users" border>
      <el-table-column prop="username" label="账号" width="180" />
      <el-table-column prop="name" label="姓名" width="180" />
      <el-table-column label="角色" width="140">
        <template #default="{ row }">
          <el-tag :type="roleTag[row.role]">{{ roleName[row.role] ?? row.role }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="权限说明">
        <template #default="{ row }">
          {{ row.role === 'admin' ? '全部操作，含设置/用户管理' : row.role === 'operator' ? '客户/授权/续期/告警处置' : '仅查看，不能修改' }}
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="新增用户" width="440">
      <el-form label-width="80px">
        <el-form-item label="账号"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="管理员" value="admin" />
            <el-option label="运营" value="operator" />
            <el-option label="只读" value="viewer" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; justify-content: space-between; align-items: flex-start; }
.head h2 { font-size: 20px; margin-bottom: 4px; }
.muted { color: #a1a1aa; font-size: 13px; }
</style>
