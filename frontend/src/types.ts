export interface User {
  username: string
  display_name: string
  role: 'customer' | 'agent' | 'admin' | string
  customer_id?: string | null
  permissions: string[]
}

export interface Citation {
  id: string
  title: string
  section: string
  source: string
  snippet: string
  score: number
  vector_score: number
  keyword_score: number
  rank: number
  doc_id: string
}

export interface TraceEvent {
  stage: string
  status: 'ok' | 'warn' | 'error'
  detail: string
  ts: string
  [key: string]: unknown
}

export interface PolicyCheck {
  rule_id: string
  title: string
  passed: boolean
  detail: string
}

export interface PolicyDecision {
  eligible: boolean
  action: string
  rule_code: string
  reason: string
  requires_approval: boolean
  refundable_amount: number
  checks: PolicyCheck[]
  risk_notes: string[]
}

export interface PendingReview {
  type: 'refund_approval' | 'human_handoff'
  intent: string
  amount?: number
  order_no?: string | null
  product_name?: string
  level?: string
  reason: string
  risk_notes?: string[]
  checks?: PolicyCheck[]
  breakdown?: {
    goods_amount?: number
    coupon_deducted?: number
    shipping_fee?: number
    paid_amount?: number
    refundable?: number
  }
  citations?: Citation[]
  message?: string
  draft_answer?: string
  approval_id: string
  thread_id: string
}

export interface ChatResult {
  thread_id: string
  status: 'completed' | 'waiting_human'
  reply: string
  intent: string
  intent_confidence: number
  routing_source: string
  confidence: number
  citations: Citation[]
  trace: TraceEvent[]
  suggestions: string[]
  policy: PolicyDecision | null
  order: Record<string, unknown> | null
  pending_review: PendingReview | null
  degraded: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  intent?: string
  citations?: Citation[]
  trace?: TraceEvent[]
  pending?: boolean
  typed?: boolean
}

export interface KnowledgeSummary {
  index: {
    chunks: number
    documents: number
    dim: number
    embedder: string
    backend: string
    built_at: string | null
  }
  knowledge_dir: string
  documents: { doc_id: string; title: string; source: string; chunks: number }[]
  retrieval: { top_k: number; min_score: number; hybrid: string }
}

export interface ReviewItem {
  approval_id: string
  thread_id: string
  order_no: string | null
  intent: string
  amount: number
  reason: string
  risk_notes: string[]
  payload: Record<string, unknown>
  status: string
  created_at: string
}
