<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from './components/AppSidebar.vue'

const route = useRoute()
const bare = computed(() => route.name === 'login')
</script>

<template>
  <div class="shell" :class="{ bare }">
    <AppSidebar v-if="!bare" />
    <main class="main">
      <RouterView v-slot="{ Component }">
        <component :is="Component" />
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 252px minmax(0, 1fr);
  height: 100vh;
  overflow: hidden;
}

.shell.bare {
  grid-template-columns: minmax(0, 1fr);
}

.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--bg);
}

@media (max-width: 900px) {
  .shell {
    grid-template-columns: 1fr;
  }
}
</style>
