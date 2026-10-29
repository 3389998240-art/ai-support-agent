<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import CitationPanel from '../components/CitationPanel.vue'
import Composer from '../components/Composer.vue'
import IntentBadge from '../components/IntentBadge.vue'
import MessageBubble from '../components/MessageBubble.vue'
import ReviewBanner from '../components/ReviewBanner.vue'
import TraceTimeline from '../components/TraceTimeline.vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()
const scroller = ref<HTMLElement | null>(null)

const samples = [
  { title: '查订单物流', text: '我的订单 SO202610180001 到哪了？', hint: '工具调用 + 物流政策依据' },
  { title: '政策问答', text: '退款一般多久到账？', hint: 'FAISS 检索 + 来源引用' },
  { title: '退款申请', text: '订单 SO202610240003 不要了，退了吧', hint: '规则引擎判定能否自助退' },
  { title: '需要审批', text: '订单 SO202610180001 我要退款，耳机有问题', hint: '金额超阈值 → 人工审批' },
]

function scrollToBottom(): void {
  nextTick(() => {
    const element = scroller.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

watch(() => store.messages.map((item) => item.content.length).join(','), scrollToBottom)
watch(() => store.messages.length, scrollToBottom)

function onDecide(payload: { decision: 'approve' | 'reject'; note: string; message: string }): void {
  void store.resume(payload.decision, payload.note, payload.message)
}
</script>

<template>
  <div class="chat">
    <header class="head">
      <div class="title">
        <h1>售后支持会话</h1>
        <span class="sub">混合意图路由 · 多业务子图 · RAG 引用 · 人工审批</span>
      </div>
      <div class="meta">
        <IntentBadge :intent="store.intent" :confidence="store.confidence" :routing="store.routing" />
        <span v-if="store.threadId" class="thread mono">{{ store.threadId }}</span>
      </div>
    </header>

    <div class="body">
      <section ref="scroller" class="thread">
        <div v-if="!store.messages.length" class="welcome">
          <p class="eyebrow">AFTER-SALES AGENT</p>
          <h2>把售后咨询、退款审核、政策解释交给一条可追溯的流程</h2>
          <p class="lede">
            意图分类决定走哪条子图；政策问题走 FAISS 混合检索并给出处；
            退款金额超过阈值会中断执行、等待人工审批；审批通过后恢复执行并落工单。
          </p>

          <div class="samples">
            <button
              v-for="item in samples"
              :key="item.title"
              class="sample"
              type="button"
              @click="store.send(item.text)"
            >
              <strong>{{ item.title }}</strong>
              <span>{{ item.text }}</span>
              <em>{{ item.hint }}</em>
            </button>
          </div>

          <div class="facts">
            <span class="badge badge-ok">确定性规则引擎</span>
            <span class="badge badge-accent">FAISS + BM25 混合检索</span>
            <span class="badge badge-warn">interrupt/resume 审批</span>
            <span class="badge badge-muted">LangGraph Checkpointer</span>
          </div>
        </div>

        <div class="msgs">
          <MessageBubble v-for="message in store.messages" :key="message.id" :message="message" />
        </div>
      </section>

      <aside class="rail">
        <TraceTimeline :events="store.trace" :busy="store.busy" :live="store.live" />
        <CitationPanel :citations="store.citations" />
      </aside>
    </div>

    <ReviewBanner
      v-if="store.pending"
      :pending="store.pending"
      :busy="store.busy"
      @decide="onDecide"
    />

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <Composer :suggestions="store.suggestions" :disabled="store.busy" @send="store.send" />
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.title h1 {
  font-size: 16px;
}

.sub {
  font-size: 11.5px;
  color: var(--muted);
}

.meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.thread {
  color: #b6b6be;
  font-size: 10.5px;
}

.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 14px;
  padding: 14px 24px;
  overflow: hidden;
}

.thread {
  overflow-y: auto;
  padding-right: 4px;
}

.welcome {
  max-width: 720px;
  margin: 18px auto 26px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.eyebrow {
  font-size: 10.5px;
  letter-spacing: 0.16em;
  color: var(--accent-ink);
  font-weight: 600;
}

.welcome h2 {
  font-size: 21px;
  line-height: 1.45;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.lede {
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.75;
}

.samples {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 6px;
}

.sample {
  text-align: left;
  padding: 12px 13px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 3px;
  transition: all 0.16s ease;
}

.sample:hover {
  border-color: #ffcda6;
  background: #fffaf5;
  transform: translateY(-1px);
  box-shadow: var(--shadow);
}

.sample strong {
  font-size: 12.5px;
}

.sample span {
  font-size: 11.5px;
  color: var(--ink-2);
}

.sample em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--muted);
}

.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.msgs {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 10px;
}

.rail {
  display: grid;
  grid-template-rows: minmax(230px, 1.1fr) minmax(200px, 1fr);
  gap: 12px;
  min-height: 0;
}

.error {
  margin: 0 24px 8px;
  padding: 8px 12px;
  border-radius: 9px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12px;
}

@media (max-width: 1180px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
  }

  .rail {
    display: none;
  }
}
</style>
