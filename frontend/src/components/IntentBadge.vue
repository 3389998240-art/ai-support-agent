<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  intent: string
  confidence?: number
  routing?: string
}>()

const LABEL: Record<string, string> = {
  policy: '政策咨询',
  order: '订单物流',
  refund: '退款退货',
  invoice: '发票保修',
  human: '人工介入',
  unknown: '待澄清',
}

const TONE: Record<string, string> = {
  refund: 'badge-warn',
  human: 'badge-danger',
  order: 'badge-accent',
  invoice: 'badge-accent',
  policy: 'badge-ok',
  unknown: 'badge-muted',
}

const label = computed(() => LABEL[props.intent] ?? props.intent)
const tone = computed(() => TONE[props.intent] ?? 'badge-muted')
const percent = computed(() => Math.round((props.confidence ?? 0) * 100))
</script>

<template>
  <span class="wrap">
    <span class="badge" :class="tone">{{ label }}</span>
    <span v-if="confidence" class="conf">{{ percent }}%</span>
    <span v-if="routing" class="route mono">{{ routing }}</span>
  </span>
</template>

<style scoped>
.wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.conf {
  font-size: 11.5px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.route {
  color: #b0b0b8;
}
</style>
