<script setup lang="ts">
import { nextTick, ref } from 'vue'

const props = defineProps<{
  suggestions: string[]
  disabled?: boolean
}>()

const emit = defineEmits<{ (event: 'send', text: string): void }>()

const draft = ref('')
const area = ref<HTMLTextAreaElement | null>(null)

function submit(): void {
  const value = draft.value.trim()
  if (!value || props.disabled) return
  emit('send', value)
  draft.value = ''
  nextTick(() => area.value?.focus())
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}

function pick(text: string): void {
  draft.value = text
  nextTick(() => area.value?.focus())
}
</script>

<template>
  <div class="composer">
    <div v-if="suggestions.length" class="chips">
      <button
        v-for="item in suggestions"
        :key="item"
        class="chip"
        type="button"
        :disabled="disabled"
        @click="pick(item)"
      >
        {{ item }}
      </button>
    </div>

    <div class="box">
      <textarea
        ref="area"
        v-model="draft"
        class="area"
        rows="1"
        maxlength="1200"
        placeholder="描述你的售后问题，例如：订单 SO202610180001 耳机有杂音，想退货"
        @keydown="onKeydown"
      />
      <div class="foot">
        <span class="hint">Enter 发送 · Shift + Enter 换行</span>
        <button class="btn btn-primary send" type="button" :disabled="disabled || !draft.trim()" @click="submit">
          {{ disabled ? '处理中…' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--line);
  background: var(--surface);
  padding: 10px 24px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.box {
  border: 1px solid var(--line-strong);
  border-radius: 13px;
  padding: 8px 10px 8px 12px;
  background: #fff;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px #ffeede;
}

.area {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  font-size: 13.5px;
  line-height: 1.6;
  max-height: 130px;
  background: transparent;
  color: var(--ink);
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 4px;
}

.hint {
  font-size: 10.5px;
  color: #b6b6be;
}

.send {
  height: 32px;
  padding: 0 18px;
}
</style>
