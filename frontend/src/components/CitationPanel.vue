<script setup lang="ts">
import { ref } from 'vue'

import type { Citation } from '../types'

defineProps<{ citations: Citation[] }>()

const expanded = ref<string | null>(null)

function toggle(id: string): void {
  expanded.value = expanded.value === id ? null : id
}
</script>

<template>
  <div class="panel card">
    <div class="section-title">
      <span>知识来源</span>
      <span class="count">{{ citations.length }} 条</span>
    </div>

    <ul v-if="citations.length" class="list">
      <li v-for="(item, index) in citations" :key="item.id" class="item">
        <button type="button" class="head" @click="toggle(item.id)">
          <span class="rank">{{ index + 1 }}</span>
          <span class="names">
            <strong>{{ item.title }}</strong>
            <em v-if="item.section">{{ item.section }}</em>
          </span>
          <span class="score mono">{{ item.score.toFixed(2) }}</span>
        </button>

        <div class="bar">
          <span class="fill" :style="{ width: `${Math.min(100, item.score * 130)}%` }" />
        </div>

        <p class="snippet" :class="{ open: expanded === item.id }">{{ item.snippet }}</p>

        <p class="meta mono">
          {{ item.source }} · 向量 {{ item.vector_score.toFixed(2) }} · 关键词 {{ item.keyword_score.toFixed(2) }}
        </p>
      </li>
    </ul>

    <p v-else class="empty">命中知识库后，这里会列出原文切片与打分，方便核对答案出处。</p>
  </div>
</template>

<style scoped>
.panel {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.count {
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: #b0b0b8;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.item {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: none;
  padding: 0;
  text-align: left;
}

.rank {
  width: 17px;
  height: 17px;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-size: 10.5px;
  font-weight: 600;
  display: grid;
  place-items: center;
  flex: none;
}

.names {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.35;
}

.names strong {
  font-size: 12.5px;
  font-weight: 500;
}

.names em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--muted);
}

.score {
  margin-left: auto;
  color: var(--muted);
}

.bar {
  height: 3px;
  border-radius: 3px;
  background: #f0f0f3;
  overflow: hidden;
}

.fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #ffb27a, var(--accent));
  border-radius: 3px;
}

.snippet {
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
}

.snippet.open {
  -webkit-line-clamp: unset;
  max-height: 220px;
  overflow-y: auto;
}

.meta {
  color: #b0b0b8;
  font-size: 10px;
}

.empty {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
</style>
