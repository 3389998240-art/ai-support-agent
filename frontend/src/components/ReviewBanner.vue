<script setup lang="ts">
import { computed, ref } from 'vue'

import { useAuthStore } from '../stores/auth'
import type { PendingReview } from '../types'

const props = defineProps<{ pending: PendingReview; busy?: boolean }>()
const emit = defineEmits<{
  (event: 'decide', payload: { decision: 'approve' | 'reject'; note: string; message: string }): void
}>()

const auth = useAuthStore()
const note = ref('')
const open = ref(false)

const isRefund = computed(() => props.pending.type === 'refund_approval')
const canDecide = computed(() => auth.isStaff)

function submit(decision: 'approve' | 'reject'): void {
  emit('decide', {
    decision,
    note: note.value.trim(),
    message: note.value.trim(),
  })
  note.value = ''
}
</script>

<template>
  <section class="banner fade-in">
    <header class="head">
      <div class="left">
        <span class="badge badge-warn">{{ isRefund ? '退款审批' : '人工接管' }}</span>
        <strong>{{ isRefund ? '这笔退款需要你确认' : '这个会话等待人工回复' }}</strong>
      </div>
      <button class="btn btn-ghost" type="button" @click="open = !open">
        {{ open ? '收起规则' : '查看判定依据' }}
      </button>
    </header>

    <div class="facts">
      <div v-if="pending.order_no" class="fact">
        <span>订单</span>
        <strong class="mono">{{ pending.order_no }}</strong>
      </div>
      <div v-if="pending.amount" class="fact">
        <span>金额</span>
        <strong>¥{{ pending.amount.toFixed(2) }}</strong>
      </div>
      <div v-if="pending.level" class="fact">
        <span>工单等级</span>
        <strong>{{ pending.level }}</strong>
      </div>
      <div class="fact wide">
        <span>原因</span>
        <strong>{{ pending.reason }}</strong>
      </div>
    </div>

    <p v-if="pending.breakdown && pending.breakdown.goods_amount" class="breakdown">
      金额构成：标价 ¥{{ pending.breakdown.goods_amount?.toFixed(2) }}
      · 优惠 −¥{{ pending.breakdown.coupon_deducted?.toFixed(2) }}
      · 运费 ¥{{ pending.breakdown.shipping_fee?.toFixed(2) }}
      <span v-if="pending.breakdown.refundable">→ 应退 ¥{{ pending.breakdown.refundable.toFixed(2) }}</span>
    </p>

    <ul v-if="pending.risk_notes && pending.risk_notes.length" class="risks">
      <li v-for="item in pending.risk_notes" :key="item">⚠ {{ item }}</li>
    </ul>

    <div v-if="open && pending.checks" class="checks">
      <div v-for="check in pending.checks" :key="check.rule_id" class="check">
        <span class="rule mono">{{ check.rule_id }}</span>
        <span class="mark" :class="check.passed ? 'pass' : 'fail'">{{ check.passed ? '✓' : '✕' }}</span>
        <span class="title">{{ check.title }}</span>
        <span class="detail">{{ check.detail }}</span>
      </div>
    </div>

    <p v-if="!canDecide" class="waiting">
      已提交给审核员（角色 agent / admin 才能审批）。等待人工处理期间可以继续查看政策说明。
    </p>

    <textarea
      v-if="canDecide"
      v-model="note"
      class="note"
      rows="2"
      :placeholder="isRefund ? '审核备注（驳回时必填）：已电话核实 / 缺少故障凭证…' : '人工回复内容，会直接回给客户'"
    />

    <footer v-if="canDecide" class="actions">
      <button class="btn btn-danger" type="button" :disabled="busy" @click="submit('reject')">
        驳回
      </button>
      <button class="btn btn-primary" type="button" :disabled="busy" @click="submit('approve')">
        {{ isRefund ? '通过并执行退款' : '标记已处理' }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.banner {
  margin: 0 24px 10px;
  padding: 14px 16px;
  border: 1px solid #f3d3a3;
  border-radius: var(--radius);
  background: linear-gradient(180deg, #fffaf1, #fff);
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 6px 20px rgba(180, 83, 9, 0.07);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.left strong {
  font-size: 13.5px;
}

.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
}

.fact {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.fact span {
  font-size: 10.5px;
  color: var(--muted);
}

.fact strong {
  font-size: 12.5px;
  font-weight: 500;
}

.fact.wide {
  flex: 1 1 100%;
  min-width: 220px;
}

.breakdown {
  font-size: 11.5px;
  color: var(--ink-2);
  background: #fbfbfd;
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 6px 10px;
}

.risks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11.5px;
  color: var(--warn);
}

.checks {
  border: 1px dashed #ecd6b8;
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  background: #fffdf9;
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

.check .title {
  font-weight: 500;
}

.check .detail {
  color: var(--muted);
  font-size: 11px;
}

.waiting {
  font-size: 12px;
  color: var(--warn);
  background: #fffaf0;
  border: 1px dashed #f0d9b5;
  border-radius: 8px;
  padding: 7px 10px;
}

.note {
  width: 100%;
  border-radius: 10px;
  border: 1px solid var(--line-strong);
  padding: 8px 10px;
  font-family: inherit;
  font-size: 12.5px;
  resize: vertical;
  background: #fff;
}

.note:focus {
  outline: 2px solid #ffd9b8;
  border-color: var(--accent);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
