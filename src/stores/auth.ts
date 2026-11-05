import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, getToken, setToken } from '../api/client'

export type Role = 'admin' | 'operator' | 'viewer'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<{ username: string; name: string; role: Role } | null>(
    getToken() ? { username: '', name: '', role: 'viewer' } : null
  )

  const role = computed<Role>(() => user.value?.role ?? 'viewer')
  const isAdmin = computed(() => role.value === 'admin')
  const canWrite = computed(() => role.value === 'admin' || role.value === 'operator')

  async function login(username: string, password: string): Promise<boolean> {
    try {
      const res = await api.login(username, password)
      setToken(res.token)
      user.value = { username: res.username, name: res.name, role: (res.role as Role) || 'operator' }
      return true
    } catch {
      return false
    }
  }

  function logout() {
    setToken(null)
    user.value = null
  }

  return { user, role, isAdmin, canWrite, login, logout }
})
