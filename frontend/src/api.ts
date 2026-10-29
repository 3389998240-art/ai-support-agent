import type { ChatResult, KnowledgeSummary, ReviewItem, User } from './types'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''
const TOKEN_KEY = 'support-token'

/** 认证失效：调用方通常要跳登录页 */
export class AuthError extends Error {}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers ?? {})
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers })

  if (response.status === 401) {
    let detail = '登录已失效，请重新登录'
    try {
      const payload = await response.json()
      detail = payload.detail ?? detail
    } catch {
      /* 忽略非 JSON 错误体 */
    }
    clearToken()
    throw new AuthError(detail)
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      detail = payload.detail ?? detail
    } catch {
      /* 非 JSON 错误体保持原样 */
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

function jsonHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json' }
}

export interface StreamEvent {
  type: 'start' | 'trace' | 'result' | 'error'
  thread_id?: string
  node?: string
  data?: unknown
  detail?: string
}

export const api = {
  health: () => request<Record<string, unknown>>('/api/health'),

  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string; expires_at: number; user: User }>(
      '/api/auth/login',
      { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ username, password }) },
    ),

  me: () => request<User>('/api/auth/me'),

  chat: (message: string, threadId?: string | null) =>
    request<ChatResult>('/api/chat', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ message, thread_id: threadId ?? undefined }),
    }),

  resume: (threadId: string, payload: Record<string, unknown>) =>
    request<ChatResult>(`/api/threads/${threadId}/resume`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    }),

  history: (threadId: string) =>
    request<{
      thread_id: string
      messages: { role: string; content: string }[]
      citations: ChatResult['citations']
      trace: ChatResult['trace']
      status: string
      pending_review: ChatResult['pending_review']
    }>(`/api/threads/${threadId}`),

  reviews: (status = 'pending') =>
    request<{ items: ReviewItem[]; count: number }>(`/api/reviews?status=${status}`),

  knowledge: () => request<KnowledgeSummary>('/api/knowledge'),

  search: (query: string, k = 5) =>
    request<{ query: string; hits: ChatResult['citations'] }>('/api/knowledge/search', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ query, k }),
    }),

  ingest: () => request<Record<string, unknown>>('/api/knowledge/ingest', { method: 'POST' }),

  orders: () =>
    request<{ customer_id: string; items: Record<string, unknown>[] }>('/api/demo/orders'),
}

/** 逐事件读取 SSE（后端用 data-only 帧，便于前端统一解析） */
export async function streamChat(
  message: string,
  threadId: string | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const headers = new Headers(jsonHeaders())
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, thread_id: threadId ?? undefined }),
  })

  if (response.status === 401) {
    clearToken()
    throw new AuthError('登录已失效，请重新登录')
  }

  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      const line = frame.split('\n').find((item) => item.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent)
      } catch {
        /* 忽略半包 */
      }
    }
  }
}
