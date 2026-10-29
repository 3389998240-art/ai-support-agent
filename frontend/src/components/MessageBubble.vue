<script setup lang="ts">
import { ref } from 'vue'

import type { ChatMessage } from '../types'
import IntentBadge from './IntentBadge.vue'

const props = defineProps<{ message: ChatMessage }>()
const showSources = ref(false)

const isUser = props.message.role === 'user'
</script>

<template>
  <div class="row" :class="isUser ? 'user' : 'assistant'">
    <div v-if="!isUser" class="avatar">AI</div>

    <div class="bubble-wrap">
      <div class="bubble" :class="{ pending: message.pending && !isUser }">
        <p class="text" v-if="message.content">{{ message.content }}</p>
        <p class="text typing" v-else>
          <span class="cursor" />
        </p>
      </div>

      <div v-if="!isUser && message.intent" class="foot">
        <IntentBadge :intent="message.intent" />
        <span v-if="message.pending" class="badge badge-warn">等待人工</span>
        <button
          v-if="message.citations && message.citations.length"
          class="source-toggle"
          type="button"
          @click="showSources = !showSources"
        >
          {{ showSources ? '收起来源' : `来源 ${message.citations.length}` }}
        </button>
      </div>

      <ul v-if="showSources && message.citations" class="sources">
        <li v-for="(item, index) in message.citations" :key="item.id">
          <span class="idx">[{{ index + 1 }}]</span>
          <span class="name">{{ item.title }}<em v-if="item.section"> · {{ item.section }}</em></span>
          <span class="score mono">{{ item.score.toFixed(2) }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.row.user {
  justify-content: flex-end;
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: linear-gradient(145deg, #ff7a1a, #ff5b00);
  color: #fff;
  font-size: 10.5px;
  font-weight: 700;
  display: grid;
  place-items: center;
  flex: none;
  margin-top: 2px;
}

.bubble-wrap {
  max-width: min(680px, 82%);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.row.user .bubble-wrap {
  align-items: flex-end;
}

.bubble {
  padding: 11px 14px;
  border-radius: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 1px 2px rgba(16, 16, 20, 0.03);
}

.row.user .bubble {
  background: var(--accent-soft);
  border-color: #ffe2ca;
  border-bottom-right-radius: 4px;
}

.row.assistant .bubble {
  border-bottom-left-radius: 4px;
}

.bubble.pending {
  border-color: #f6d5a8;
  background: #fffdf8;
}

.text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  color: var(--ink);
}

.typing {
  min-height: 20px;
}

.cursor {
  display: inline-block;
  width: 7px;
  height: 14px;
  background: var(--accent);
  border-radius: 2px;
  animation: blink 1s steps(2, start) infinite;
  vertical-align: -2px;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 2px;
}

.source-toggle {
  border: none;
  background: none;
  color: var(--muted);
  font-size: 11.5px;
  padding: 0;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.source-toggle:hover {
  color: var(--accent-ink);
}

.sources {
  list-style: none;
  margin: 0;
  padding: 8px 10px;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  background: var(--surface-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.sources li {
  display: flex;
  gap: 8px;
  font-size: 11.5px;
  color: var(--ink-2);
  align-items: baseline;
}

.idx {
  color: var(--accent-ink);
  font-family: var(--mono);
}

.name em {
  color: var(--muted);
  font-style: normal;
}

.score {
  margin-left: auto;
  color: var(--muted);
}
</style>
