<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import type { PolicyCheck, ReviewItem } from '../types'

const auth = useAuthStore()
const items = ref<ReviewItem[]>([])
const loading = ref(false)
const error = ref('')
const toast = ref('')
const notes = ref<Record<string, string>>({})
const working = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.reviews('pending')
    items.value = payload.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    items.value = []
  } finally {
    loading.value = false
  }
}

async function decide(item: ReviewItem, decision: 'approve' | 'reject'): Promise<void> {
  working.value = item.approval_id
  error.value = ''
  try {
    const result = await api.resume(item.thread_id, {
      decision,
      note: notes.value[item.approval_id] ?? '',
      message: notes.value[item.approval_id] ?? '',
    })
    toast.value = `${item.approval_id} 已${decision === 'approve' ? '通过' : '驳回'} · ${result.status === 'completed' ? '会话已恢复' : '仍在等待处理'}`
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    working.value = null
    window.setTimeout(() => (toast.value = ''), 4200)
  }
}

onMounted(load)

function checksOf(item: ReviewItem): PolicyCheck[] {
  const raw = item.payload?.checks
  return Array.isArray(raw) ? (raw as PolicyCheck[]) : []
}
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1>人工审核工作台</h1>
        <p class="sub">高额退款与人工接管都会在这里排队，审批后自动恢复被中断的会话。</p>
      </div>
      <div class="key-row">
        <span class="who">{{ auth.displayName }} · {{ auth.role }}</span>
        <button class="btn" type="button" :disabled="loading" @click="load">
          {{ loading ? '刷新中…' : '刷新队列' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="alert">{{ error }}</p>
    <p v-if="toast" class="toast">{{ toast }}</p>

    <div v-if="!items.length && !loading" class="empty card">
      <strong>当前没有待审请求</strong>
      <p>在对话里发起一笔超过 ¥300 的退款，或直接说「转人工」，这里就会出现待审项。</p>
    </div>

    <div class="list">
      <article v-for="item in items" :key="item.approval_id" class="item card fade-in">
        <header class="item-head">
          <div class="tags">
            <span class="badge" :class="item.intent === 'refund' ? 'badge-warn' : 'badge-danger'">
              {{ item.intent === 'refund' ? '退款审批' : '人工接管' }}
            </span>
            <span class="badge badge-muted mono">{{ item.approval_id }}</span>
          </div>
          <span class="time mono">{{ item.created_at.replace('T', ' ').slice(0, 19) }}</span>
        </header>

        <div class="grid">
          <div class="field">
            <span>订单号</span>
            <strong class="mono">{{ item.order_no ?? '—' }}</strong>
          </div>
          <div class="field">
            <span>金额</span>
            <strong>¥{{ item.amount.toFixed(2) }}</strong>
          </div>
          <div class="field">
            <span>会话</span>
            <strong class="mono">{{ item.thread_id }}</strong>
          </div>
        </div>

        <p class="reason">{{ item.reason }}</p>

        <ul v-if="item.risk_notes.length" class="risks">
          <li v-for="note in item.risk_notes" :key="note">⚠ {{ note }}</li>
        </ul>

        <div v-if="checksOf(item).length" class="checks">
          <div
            v-for="check in checksOf(item)"
            :key="check.rule_id"
            class="check"
          >
            <span class="mono rule">{{ check.rule_id }}</span>
            <span class="mark" :class="check.passed ? 'pass' : 'fail'">{{ check.passed ? '✓' : '✕' }}</span>
            <span>{{ check.title }}</span>
            <span class="detail">{{ check.detail }}</span>
          </div>
        </div>

        <div class="actions">
          <input
            v-model="notes[item.approval_id]"
            class="note"
            type="text"
            placeholder="审核备注 / 给客户的回复"
          />
          <button
            class="btn btn-danger"
            type="button"
            :disabled="working === item.approval_id"
            @click="decide(item, 'reject')"
          >
            驳回
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="working === item.approval_id"
            @click="decide(item, 'approve')"
          >
            {{ working === item.approval_id ? '处理中…' : '通过' }}
          </button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: 22px 26px 40px;
  overflow-y: auto;
  height: 100vh;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

h1 {
  font-size: 18px;
}

.sub {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.key-row {
  display: flex;
  gap: 8px;
}

.who {
  font-size: 12px;
  color: var(--muted);
  align-self: center;
}

.alert {
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
}

.toast {
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--ok-soft);
  color: var(--ok);
  font-size: 12.5px;
}

.empty {
  padding: 26px;
  text-align: center;
  color: var(--ink-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.empty p {
  font-size: 12.5px;
  color: var(--muted);
}

.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.item {
  padding: 15px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tags {
  display: flex;
  gap: 6px;
}

.time {
  font-size: 10.5px;
  color: #b6b6be;
}

.grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 26px;
}

.field {
  display: flex;
  flex-direction: column;
}

.field span {
  font-size: 10.5px;
  color: var(--muted);
}

.field strong {
  font-size: 12.5px;
  font-weight: 500;
}

.reason {
  font-size: 13px;
}

.risks {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 11.5px;
  color: var(--warn);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.checks {
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--surface-2);
}

.check {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11.5px;
}

.rule {
  color: var(--muted);
}

.mark.pass {
  color: var(--ok);
}

.mark.fail {
  color: var(--danger);
}

.detail {
  color: var(--muted);
  font-size: 11px;
}

.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.note {
  flex: 1;
  height: 34px;
  padding: 0 10px;
  border-radius: 9px;
  border: 1px solid var(--line-strong);
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .head {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
