<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'

const router = useRouter()
const chat = useChatStore()
const auth = useAuthStore()

const ROLE_LABEL: Record<string, string> = {
  customer: '客户',
  agent: '审核员',
  admin: '管理员',
}

const online = ref<boolean | null>(null)
const llmLabel = ref('检测中…')
const kbLabel = ref('')

const nav = [
  { to: '/', label: '对话工作台', hint: 'Chat Studio', roles: ['customer', 'agent', 'admin'] },
  { to: '/review', label: '人工审核', hint: 'Review Queue', roles: ['agent', 'admin'] },
  { to: '/knowledge', label: '知识库', hint: 'Knowledge Base', roles: ['agent', 'admin'] },
]

const navItems = computed(() =>
  nav.filter((item) => item.roles.includes(auth.role || 'customer')),
)
const roleLabel = computed(() => ROLE_LABEL[auth.role] ?? auth.role)

onMounted(async () => {
  try {
    const health = (await api.health()) as {
      llm?: { provider?: string; enabled?: boolean }
      knowledge?: { chunks?: number; documents?: number }
    }
    online.value = true
    const provider = health.llm?.provider ?? 'mock'
    llmLabel.value = health.llm?.enabled ? `${provider} 已接入` : `${provider} · 离线降级`
    kbLabel.value = `${health.knowledge?.documents ?? 0} 篇 / ${health.knowledge?.chunks ?? 0} 切片`
  } catch {
    online.value = false
    llmLabel.value = '后端未连接'
  }
})

function newSession(): void {
  chat.reset()
  if (router.currentRoute.value.path !== '/') {
    router.push('/')
  }
}

function logout(): void {
  auth.logout()
  chat.reset()
  router.replace('/login')
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="mark">售</div>
      <div class="brand-text">
        <strong>星辰售后</strong>
        <span>Support Agent Console</span>
      </div>
    </div>

    <button class="new-session" type="button" @click="newSession">
      <span class="plus">＋</span> 新建会话
    </button>

    <nav class="nav">
      <RouterLink v-for="item in navItems" :key="item.to" :to="item.to" class="nav-item">
        <span class="nav-label">{{ item.label }}</span>
        <span class="nav-hint">{{ item.hint }}</span>
      </RouterLink>
    </nav>

    <div class="spacer" />

    <div class="user card">
      <div class="user-row">
        <strong>{{ auth.displayName || '未登录' }}</strong>
        <span class="badge" :class="auth.isStaff ? 'badge-accent' : 'badge-muted'">{{ roleLabel }}</span>
      </div>
      <p class="user-meta mono">
        {{ auth.user?.username }}
        <span v-if="auth.user?.customer_id"> · {{ auth.user?.customer_id }}</span>
      </p>
      <button class="btn btn-ghost logout" type="button" @click="logout">退出登录</button>
    </div>

    <div class="status card">
      <div class="status-row">
        <span class="dot" :class="{ ok: online === true, bad: online === false }" />
        <span>{{ online === null ? '连接中…' : online ? '服务在线' : '服务离线' }}</span>
      </div>
      <div class="status-line">{{ llmLabel }}</div>
      <div class="status-line" v-if="kbLabel">知识库 {{ kbLabel }}</div>
    </div>

    <p class="foot">LangGraph · FAISS · FastAPI<br />Vue 3 重写前端 v1.0</p>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 20px 16px;
  background: var(--surface);
  border-right: 1px solid var(--line);
  height: 100vh;
  overflow-y: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 4px;
}

.mark {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: linear-gradient(145deg, #ff7a1a, #ff5b00);
  color: #fff;
  font-weight: 700;
  font-size: 17px;
  display: grid;
  place-items: center;
  box-shadow: 0 6px 16px rgba(255, 105, 0, 0.28);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.brand-text strong {
  font-size: 15px;
}

.brand-text span {
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: 0.03em;
}

.new-session {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 38px;
  border-radius: 10px;
  border: 1px dashed var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-2);
  font-size: 13px;
  transition: all 0.15s ease;
}

.new-session:hover {
  border-color: var(--accent);
  color: var(--accent-ink);
  background: var(--accent-soft);
}

.plus {
  font-size: 15px;
  line-height: 1;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  flex-direction: column;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid transparent;
  transition: all 0.15s ease;
}

.nav-item:hover {
  background: var(--surface-2);
}

.nav-item.router-link-active {
  background: var(--accent-soft);
  border-color: #ffe0c7;
}

.nav-label {
  font-size: 13.5px;
  font-weight: 500;
}

.nav-hint {
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: 0.04em;
}

.nav-item.router-link-active .nav-label {
  color: var(--accent-ink);
}

.spacer {
  flex: 1;
}

.user {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.user-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.user-row strong {
  font-size: 12.5px;
}

.user-meta {
  font-size: 10.5px;
  color: var(--muted);
}

.logout {
  align-self: flex-start;
  height: 26px;
  padding: 0 8px;
  font-size: 11.5px;
  color: var(--muted);
}

.logout:hover {
  color: var(--danger);
}

.status {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  color: var(--ink-2);
}

.status-row {
  display: flex;
  align-items: center;
  gap: 7px;
  font-weight: 500;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--muted);
}

.dot.ok {
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(15, 157, 88, 0.14);
}

.dot.bad {
  background: var(--danger);
  box-shadow: 0 0 0 3px rgba(217, 45, 32, 0.12);
}

.status-line {
  font-size: 11.5px;
  color: var(--muted);
}

.foot {
  font-size: 10.5px;
  color: #b0b0b8;
  line-height: 1.6;
  padding: 0 4px;
}

@media (max-width: 900px) {
  .sidebar {
    display: none;
  }
}
</style>
