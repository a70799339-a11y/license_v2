import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '经营总览' } },
    { path: '/customers', name: 'customers', component: () => import('../views/CustomersView.vue'), meta: { title: '客户管理' } },
    { path: '/customers/:id', name: 'customer-detail', component: () => import('../views/CustomerDetailView.vue'), meta: { title: '客户详情' } },
    { path: '/licenses', name: 'licenses', component: () => import('../views/LicensesView.vue'), meta: { title: '授权管理' } },
    { path: '/plans', name: 'plans', component: () => import('../views/PlansView.vue'), meta: { title: '套餐管理' } },
    { path: '/risk', name: 'risk', component: () => import('../views/RiskView.vue'), meta: { title: 'AI 智能风控' } },
    { path: '/renewals', name: 'renewals', component: () => import('../views/RenewalsView.vue'), meta: { title: '到期续期工作台' } },
    { path: '/groups', name: 'groups', component: () => import('../views/GroupsView.vue'), meta: { title: '集团管理' } },
    { path: '/security', name: 'security', component: () => import('../views/SecurityView.vue'), meta: { title: '安全设计' } },
    { path: '/users', name: 'users', component: () => import('../views/UsersView.vue'), meta: { title: '用户管理', adminOnly: true } },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.path !== '/login' && !auth.user) return '/login'
  if (to.meta.adminOnly && auth.role !== 'admin') return '/dashboard'
  return true
})

export default router
