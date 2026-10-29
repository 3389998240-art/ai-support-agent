<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('alice')
const password = ref('demo1234')

const demoAccounts = [
  { username: 'alice', role: '客户 C1001', hint: '只能看自己的订单与会话' },
  { username: 'agent01', role: '售后审核员', hint: '可看待审、可审批（≤¥5000）' },
  { username: 'admin', role: '管理员', hint: '全部权限，可重建索引' },
]

function fill(name: string): void {
  username.value = name
  password.value = 'demo1234'
}

async function submit(): Promise<void> {
  const ok = await auth.login(username.value.trim(), password.value)
  if (ok) {
    const redirect = (route.query.redirect as string) || '/'
    await router.replace(redirect)
  }
}
</script>

<template>
  <div class="login-page">
    <div class="panel">
      <header class="brand">
        <div class="mark">售</div>
        <div>
          <h1>星辰售后 · Support Agent</h1>
          <p>登录后才会加载会话与订单数据（JWT + 角色权限）</p>
        </div>
      </header>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span>账号</span>
          <input v-model="username" autocomplete="username" placeholder="alice / agent01 / admin" />
        </label>

        <label class="field">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="demo1234"
          />
        </label>

        <p v-if="auth.error" class="error">{{ auth.error }}</p>

        <button class="btn btn-primary submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? '登录中…' : '登录' }}
        </button>
      </form>

      <div class="demo">
        <p class="section-title"><span>演示账号</span><span class="plain">密码均为 demo1234</span></p>
        <button
          v-for="item in demoAccounts"
          :key="item.username"
          class="account"
          type="button"
          @click="fill(item.username)"
        >
          <strong>{{ item.username }}</strong>
          <span class="role">{{ item.role }}</span>
          <em>{{ item.hint }}</em>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: radial-gradient(1200px 600px at 20% -10%, #fff3e8, transparent 60%), var(--bg);
}

.panel {
  width: min(440px, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 26px 26px 20px;
  box-shadow: 0 1px 2px rgba(16, 16, 20, 0.04), 0 18px 50px rgba(16, 16, 20, 0.08);
}

.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 20px;
}

.mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(145deg, #ff7a1a, #ff5b00);
  color: #fff;
  font-weight: 700;
  font-size: 18px;
  display: grid;
  place-items: center;
  box-shadow: 0 8px 20px rgba(255, 105, 0, 0.28);
}

.brand h1 {
  font-size: 16px;
}

.brand p {
  font-size: 11.5px;
  color: var(--muted);
  margin-top: 2px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field span {
  font-size: 11.5px;
  color: var(--muted);
}

.field input {
  height: 38px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid var(--line-strong);
  font-size: 13.5px;
  font-family: inherit;
}

.field input:focus {
  outline: 2px solid #ffe1c9;
  border-color: var(--accent);
}

.error {
  font-size: 12px;
  color: var(--danger);
  background: var(--danger-soft);
  border-radius: 8px;
  padding: 7px 10px;
}

.submit {
  height: 38px;
  margin-top: 4px;
}

.demo {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px dashed var(--line);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.plain {
  text-transform: none;
  letter-spacing: 0;
}

.account {
  text-align: left;
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 2px 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--surface-2);
  transition: all 0.15s ease;
}

.account:hover {
  border-color: #ffcda6;
  background: #fffaf5;
}

.account strong {
  font-size: 12.5px;
  font-family: var(--mono);
}

.account .role {
  font-size: 12px;
  color: var(--ink-2);
}

.account em {
  grid-column: 1 / -1;
  font-style: normal;
  font-size: 10.5px;
  color: var(--muted);
}
</style>
