import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('./views/LoginView.vue'),
      meta: { title: '登录' },
    },
    {
      path: '/',
      name: 'chat',
      component: () => import('./views/ChatView.vue'),
      meta: { title: '对话工作台' },
    },
    {
      path: '/review',
      name: 'review',
      component: () => import('./views/ReviewView.vue'),
      meta: { title: '人工审核', roles: ['agent', 'admin'] },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('./views/KnowledgeView.vue'),
      meta: { title: '知识库', roles: ['agent', 'admin'] },
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.name === 'login') {
    return auth.isAuthenticated ? { name: 'chat' } : true
  }

  if (!auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (!auth.user) {
    const restored = await auth.restore()
    if (!restored) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }

  const roles = to.meta.roles as string[] | undefined
  if (roles && !roles.includes(auth.role)) {
    return { name: 'chat' }
  }
  return true
})

export default router
