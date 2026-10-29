import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, clearToken, getToken, setToken } from '../api'
import type { User } from '../types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref<User | null>(null)
  const loading = ref(false)
  const error = ref('')

  const isAuthenticated = computed(() => Boolean(token.value))
  const role = computed(() => user.value?.role ?? '')
  const isStaff = computed(() => role.value === 'agent' || role.value === 'admin')
  const isAdmin = computed(() => role.value === 'admin')
  const displayName = computed(() => user.value?.display_name || user.value?.username || '')

  async function login(username: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = ''
    try {
      const result = await api.login(username, password)
      setToken(result.access_token)
      token.value = result.access_token
      user.value = result.user
      return true
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return false
    } finally {
      loading.value = false
    }
  }

  /** 刷新页面后用已有 token 恢复身份 */
  async function restore(): Promise<boolean> {
    if (!token.value) return false
    try {
      user.value = await api.me()
      return true
    } catch {
      logout()
      return false
    }
  }

  function logout(): void {
    clearToken()
    token.value = ''
    user.value = null
  }

  return {
    token,
    user,
    role,
    loading,
    error,
    isAuthenticated,
    isStaff,
    isAdmin,
    displayName,
    login,
    restore,
    logout,
  }
})
