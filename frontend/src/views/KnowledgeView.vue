<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import type { Citation, KnowledgeSummary } from '../types'

const summary = ref<KnowledgeSummary | null>(null)
const query = ref('退款多久到账')
const hits = ref<Citation[]>([])
const searching = ref(false)
const ingesting = ref(false)
const error = ref('')
const auth = useAuthStore()
const message = ref('')

async function load(): Promise<void> {
  try {
    summary.value = await api.knowledge()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function search(): Promise<void> {
  if (!query.value.trim()) return
  searching.value = true
  error.value = ''
  try {
    const payload = await api.search(query.value.trim(), 6)
    hits.value = payload.hits
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    searching.value = false
  }
}

async function rebuild(): Promise<void> {
  ingesting.value = true
  error.value = ''
  message.value = ''
  try {
    const report = await api.ingest()
    message.value = `索引重建完成：${report.documents} 篇文档 / ${report.chunks} 个切片，耗时 ${report.elapsed_ms}ms`
    await load()
    await search()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    ingesting.value = false
    window.setTimeout(() => (message.value = ''), 5000)
  }
}

onMounted(async () => {
  await load()
  await search()
})
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1>知识库</h1>
        <p class="sub">Markdown 政策文档 → 标题感知切片 → 哈希向量 + FAISS → 与 BM25 融合召回。</p>
      </div>
      <div class="ops">
        <span class="who">{{ auth.displayName }} · {{ auth.role }}</span>
        <button v-if="auth.isAdmin" class="btn" type="button" :disabled="ingesting" @click="rebuild">
          {{ ingesting ? '重建中…' : '重建索引' }}
        </button>
      </div>
    </header>

    <section class="stats">
      <div class="stat card">
        <span>文档</span>
        <strong>{{ summary?.index.documents ?? '—' }}</strong>
      </div>
      <div class="stat card">
        <span>切片</span>
        <strong>{{ summary?.index.chunks ?? '—' }}</strong>
      </div>
      <div class="stat card">
        <span>向量维度</span>
        <strong>{{ summary?.index.dim ?? '—' }}</strong>
      </div>
      <div class="stat card">
        <span>索引后端</span>
        <strong>{{ summary?.index.backend ?? '—' }}</strong>
      </div>
      <div class="stat card">
        <span>Embedding</span>
        <strong class="small">{{ summary?.index.embedder ?? '—' }}</strong>
      </div>
    </section>

    <p v-if="error" class="alert">{{ error }}</p>
    <p v-if="message" class="ok">{{ message }}</p>

    <section class="split">
      <div class="docs card">
        <div class="section-title"><span>文档</span><span>{{ summary?.documents.length ?? 0 }}</span></div>
        <ul>
          <li v-for="doc in summary?.documents ?? []" :key="doc.doc_id">
            <div class="doc-name">
              <strong>{{ doc.title }}</strong>
              <span class="mono">{{ doc.source }}</span>
            </div>
            <span class="chunks mono">{{ doc.chunks }} 切片</span>
          </li>
        </ul>
        <p class="dir mono">{{ summary?.knowledge_dir }}</p>
      </div>

      <div class="play card">
        <div class="section-title">
          <span>检索调试台</span>
          <span>Top {{ summary?.retrieval.top_k ?? 6 }} · 阈值 {{ summary?.retrieval.min_score ?? 0.2 }}</span>
        </div>

        <div class="search-row">
          <input
            v-model="query"
            class="search"
            type="text"
            placeholder="输入问题，例如：退货运费谁承担"
            @keydown.enter="search"
          />
          <button class="btn btn-primary" type="button" :disabled="searching" @click="search">
            {{ searching ? '检索中…' : '检索' }}
          </button>
        </div>

        <ul class="hits">
          <li v-for="(hit, index) in hits" :key="hit.id" class="hit">
            <div class="hit-head">
              <span class="rank">{{ index + 1 }}</span>
              <strong>{{ hit.title }}</strong>
              <em v-if="hit.section">{{ hit.section }}</em>
              <span class="score mono">{{ hit.score.toFixed(3) }}</span>
            </div>
            <div class="bars">
              <span class="bar"><i :style="{ width: `${Math.min(100, hit.vector_score * 120)}%` }" /></span>
              <span class="legend">向量 {{ hit.vector_score.toFixed(2) }}</span>
              <span class="bar alt"><i :style="{ width: `${Math.min(100, hit.keyword_score * 55)}%` }" /></span>
              <span class="legend">BM25 {{ hit.keyword_score.toFixed(2) }}</span>
            </div>
            <p class="snippet">{{ hit.snippet }}</p>
          </li>
          <li v-if="!hits.length" class="none">没有命中，试试「退款时效」「发票」「保修」这类关键词。</li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  padding: 22px 26px 40px;
  height: 100vh;
  overflow-y: auto;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

h1 {
  font-size: 18px;
}

.sub {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.ops {
  display: flex;
  gap: 8px;
}

.who {
  font-size: 12px;
  color: var(--muted);
  align-self: center;
}

.stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.stat {
  padding: 11px 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat span {
  font-size: 10.5px;
  color: var(--muted);
}

.stat strong {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.stat strong.small {
  font-size: 12.5px;
  padding-top: 5px;
}

.alert {
  margin: 0 0 10px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
}

.ok {
  margin: 0 0 10px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--ok-soft);
  color: var(--ok);
  font-size: 12.5px;
}

.split {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.docs,
.play {
  padding: 14px;
}

.docs ul {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.docs li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--line);
}

.doc-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.doc-name strong {
  font-size: 12.5px;
  font-weight: 500;
}

.doc-name span {
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chunks {
  font-size: 10.5px;
  color: var(--muted);
  flex: none;
}

.dir {
  margin-top: 10px;
  font-size: 9.5px;
  color: #bcbcc4;
  word-break: break-all;
}

.search-row {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}

.search {
  flex: 1;
  height: 36px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid var(--line-strong);
  font-size: 13px;
}

.search:focus {
  outline: 2px solid #ffe1c9;
  border-color: var(--accent);
}

.hits {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hit {
  border: 1px solid var(--line);
  border-radius: 11px;
  padding: 10px 12px;
  background: var(--surface-2);
}

.hit-head {
  display: flex;
  align-items: baseline;
  gap: 7px;
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

.hit-head strong {
  font-size: 12.5px;
  font-weight: 500;
}

.hit-head em {
  font-style: normal;
  font-size: 11px;
  color: var(--muted);
}

.hit-head .score {
  margin-left: auto;
  color: var(--accent-ink);
}

.bars {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 6px 0;
}

.bar {
  width: 74px;
  height: 4px;
  border-radius: 4px;
  background: #ececf0;
  overflow: hidden;
}

.bar i {
  display: block;
  height: 100%;
  background: var(--accent);
}

.bar.alt i {
  background: #7a8cf0;
}

.legend {
  font-size: 9.5px;
  color: var(--muted);
}

.snippet {
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.6;
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.none {
  font-size: 12px;
  color: var(--muted);
}

@media (max-width: 1100px) {
  .stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .split {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
