<script setup lang="ts">
import type { TraceEvent } from '../types'

defineProps<{
  events: TraceEvent[]
  busy?: boolean
  live?: string
}>()
</script>

<template>
  <div class="panel card">
    <div class="section-title">
      <span>执行链路</span>
      <span class="count">{{ events.length }} 步</span>
    </div>

    <ol class="timeline" v-if="events.length">
      <li v-for="(item, index) in events" :key="`${item.stage}-${index}`" class="step fade-in">
        <span class="dot" :class="item.status" />
        <div class="body">
          <div class="head">
            <span class="stage">{{ item.stage }}</span>
            <span class="ts mono">{{ item.ts }}</span>
          </div>
          <p class="detail">{{ item.detail }}</p>
        </div>
      </li>
    </ol>

    <p v-else class="empty">发送一条消息后，这里会显示路由、检索、校验的每一步。</p>

    <div v-if="busy" class="running">
      <span class="pulse" />
      {{ live || '执行中…' }}
    </div>
  </div>
</template>

<style scoped>
.panel {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.count {
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: #b0b0b8;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
}

.step {
  display: flex;
  gap: 9px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 6px;
  flex: none;
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(15, 157, 88, 0.12);
}

.dot.warn {
  background: #e08700;
  box-shadow: 0 0 0 3px rgba(224, 135, 0, 0.14);
}

.dot.error {
  background: var(--danger);
  box-shadow: 0 0 0 3px rgba(217, 45, 32, 0.12);
}

.body {
  min-width: 0;
}

.head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.stage {
  font-size: 12.5px;
  font-weight: 500;
}

.ts {
  color: #b6b6be;
  font-size: 10.5px;
}

.detail {
  font-size: 11.5px;
  color: var(--muted);
  line-height: 1.5;
  word-break: break-word;
}

.empty {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.running {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 11.5px;
}

.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}
</style>
