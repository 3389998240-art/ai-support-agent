import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { AuthError, api, streamChat } from '../api'
import type { ChatMessage, ChatResult, Citation, PendingReview, TraceEvent } from '../types'

let seq = 0
const nextId = () => `m${Date.now()}-${seq++}`

const INTENT_LABEL: Record<string, string> = {
  policy: '政策咨询',
  order: '订单物流',
  refund: '退款退货',
  invoice: '发票保修',
  human: '人工介入',
  unknown: '待澄清',
}

export const useChatStore = defineStore('chat', () => {
  const threadId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const trace = ref<TraceEvent[]>([])
  const citations = ref<Citation[]>([])
  const intent = ref('unknown')
  const confidence = ref(0)
  const routing = ref('')
  const suggestions = ref<string[]>([])
  const pending = ref<PendingReview | null>(null)
  const policy = ref<ChatResult['policy']>(null)
  const busy = ref(false)
  const error = ref('')
  const live = ref('')

  const intentLabel = computed(() => INTENT_LABEL[intent.value] ?? intent.value)
  const waiting = computed(() => pending.value !== null)

  function reset(): void {
    threadId.value = null
    messages.value = []
    trace.value = []
    citations.value = []
    intent.value = 'unknown'
    confidence.value = 0
    routing.value = ''
    suggestions.value = []
    pending.value = null
    policy.value = null
    error.value = ''
    live.value = ''
  }

  function typeOut(id: string, full: string): void {
    let index = 0
    const step = Math.max(2, Math.ceil(full.length / 70))
    // 必须通过数组取到响应式代理再写，直接改 push 进去的原始对象不会触发更新
    const timer = window.setInterval(() => {
      const target = messages.value.find((item) => item.id === id)
      if (!target) {
        window.clearInterval(timer)
        return
      }
      index += step
      target.content = index >= full.length ? full : full.slice(0, index)
      if (index >= full.length) {
        window.clearInterval(timer)
      }
    }, 22)
  }

  function absorb(result: ChatResult, assistant: ChatMessage): void {
    threadId.value = result.thread_id
    intent.value = result.intent
    confidence.value = result.confidence
    routing.value = result.routing_source
    suggestions.value = result.suggestions ?? []
    citations.value = result.citations ?? []
    trace.value = result.trace ?? []
    pending.value = result.pending_review ?? null
    policy.value = result.policy ?? null

    const target = messages.value.find((item) => item.id === assistant.id)
    if (target) {
      target.intent = result.intent
      target.citations = result.citations
      target.trace = result.trace
      target.pending = Boolean(result.pending_review)
    }

    const reply =
      result.reply ||
      (result.pending_review
        ? '已提交人工处理，请在右侧查看待审内容。'
        : '（本次没有生成回复）')
    typeOut(assistant.id, reply)
  }

  async function send(text: string): Promise<void> {
    const content = text.trim()
    if (!content || busy.value) return

    error.value = ''
    busy.value = true
    live.value = '正在路由…'

    messages.value.push({ id: nextId(), role: 'user', content })
    const assistant: ChatMessage = { id: nextId(), role: 'assistant', content: '' }
    messages.value.push(assistant)
    trace.value = []
    citations.value = []

    try {
      let result: ChatResult | null = null
      await streamChat(content, threadId.value, (event) => {
        if (event.type === 'trace' && event.data) {
          trace.value = [...trace.value, event.data as TraceEvent]
          const last = event.data as TraceEvent
          live.value = `${last.stage}：${last.detail}`
        }
        if (event.type === 'result' && event.data) {
          result = event.data as ChatResult
        }
        if (event.type === 'error') {
          error.value = event.detail ?? '请求失败'
        }
      })

      if (!result) {
        // 不要在这里退化成再发一次 /api/chat：图可能已经执行过，
        // 重发会导致重复退款/重复工单，宁可报错让用户重试。
        throw new Error(error.value || '流式响应中断，请稍后重试')
      }
      absorb(result, assistant)
    } catch (err) {
      if (err instanceof AuthError) {
        window.location.assign('/login')
        return
      }
      error.value = err instanceof Error ? err.message : String(err)
      assistant.content = '服务暂时不可用，请稍后重试。'
    } finally {
      busy.value = false
      live.value = ''
    }
  }

  async function resume(decision: 'approve' | 'reject', note: string, message = ''): Promise<void> {
    if (!threadId.value || busy.value) return
    busy.value = true
    error.value = ''

    const assistant: ChatMessage = { id: nextId(), role: 'assistant', content: '' }
    messages.value.push(assistant)

    try {
      const result = await api.resume(threadId.value, { decision, note, message })
      absorb(result, assistant)
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      messages.value.pop()
    } finally {
      busy.value = false
    }
  }

  return {
    threadId,
    messages,
    trace,
    citations,
    intent,
    intentLabel,
    confidence,
    routing,
    suggestions,
    pending,
    policy,
    busy,
    error,
    live,
    waiting,
    reset,
    send,
    resume,
  }
})
