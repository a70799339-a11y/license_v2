// API client —— 对接自研 FastAPI 后端（数据存自建 PostgreSQL）
const TOKEN_KEY = 'license_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`/api${path}`, { ...options, headers })
  if (res.status === 401) {
    setToken(null)
    if (!location.pathname.startsWith('/login')) location.href = '/login'
    throw new Error('登录已过期')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `请求失败 ${res.status}`)
  }
  return res.json()
}

export interface Hotel {
  id: string
  name: string
  code: string
  group: string
  city: string
  contact: string
  phone: string
  plan: '基础版' | '专业版' | '旗舰版'
  status: '正常' | '即将到期' | '已过期' | '已停用'
  devices: number
  maxDevices: number
  expireAt: string
  autoRenew: boolean
  createdAt: string
  lastActive: string
}

export interface License {
  id: string
  code: string
  hotelName: string
  plan: '基础版' | '专业版' | '旗舰版'
  type: '新签' | '续期' | '升级'
  issuedAt: string
  expireAt: string
  status: '生效中' | '待激活' | '已吊销' | '已过期'
  devices: number
  maxDevices: number
}

export interface Alert {
  id: string
  level: '紧急' | '高' | '中'
  rule: string
  hotelName: string
  detail: string
  time: string
  status: '待处理' | '处理中' | '已处置'
}

export interface Plan {
  id: string
  name: '基础版' | '专业版' | '旗舰版'
  price: number
  yearlyPrice: number
  maxDevices: number
  features: string[]
  hotels: number
  color: string
}

export interface Device {
  id: string
  name: string
  os: string
  fingerprint: string
  lastActive: string
  status: string
}

export interface AuditLog {
  id: number
  operator: string
  action: string
  target: string
  detail: string
  createdAt: string
}

export interface LlmCallLog {
  id: number
  sourceType: string
  sourceId: string
  sourceName: string
  userId: string
  scene: string
  userInput: string
  modelInput: string
  modelOutput: string
  modelName: string
  tokensIn: number
  tokensOut: number
  latencyMs: number
  status: string
  error: string
  createdAt: string
}

export interface HotelDetail {
  hotel: Hotel
  devices: Device[]
  licenses: License[]
  alerts: Alert[]
}

export interface User {
  username: string
  name: string
  role: 'admin' | 'operator' | 'viewer'
}

export interface Group {
  id: string
  name: string
  code: string
  contact: string
  phone: string
  status: string
  plan: string
  expireAt: string
  autoRenew: boolean
  memberCount: number
  deviceCount: number
  expiringSoon: number
}

export interface AlertGroup {
  rule: string
  level: string
  count: number
  pending: number
  hotelCount: number
  latest: string
  sample: string
}

export const api = {
  // auth
  login: (username: string, password: string) =>
    request<{ token: string; username: string; name: string; role: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  // hotels
  listHotels: () => request<Hotel[]>('/hotels'),
  listHotelsPage: (params: { page?: number; pageSize?: number; keyword?: string; status?: string; plan?: string }) => {
    const q = new URLSearchParams()
    if (params.page) q.set('page', String(params.page))
    if (params.pageSize) q.set('page_size', String(params.pageSize))
    if (params.keyword) q.set('keyword', params.keyword)
    if (params.status) q.set('status', params.status)
    if (params.plan) q.set('plan', params.plan)
    return request<{ items: Hotel[]; total: number; page: number; pageSize: number }>(`/hotels/page?${q}`)
  },
  createHotel: (data: { name: string; code: string; group?: string; city?: string; contact?: string; phone?: string; plan?: string }) =>
    request<{ ok: boolean; id: string }>('/hotels', { method: 'POST', body: JSON.stringify(data) }),
  batchRenew: (ids: string[], expireAt = '2027-11-01') =>
    request<Hotel[]>('/hotels/batch-renew', {
      method: 'POST',
      body: JSON.stringify({ ids, expireAt }),
    }),
  batchDisable: (ids: string[]) =>
    request<Hotel[]>('/hotels/batch-disable', { method: 'POST', body: JSON.stringify({ ids }) }),
  updateHotel: (id: string, upd: Partial<Pick<Hotel, 'status' | 'autoRenew' | 'expireAt' | 'plan'>>) =>
    request<Hotel>(`/hotels/${id}`, { method: 'PATCH', body: JSON.stringify(upd) }),
  hotelDetail: (id: string) => request<HotelDetail>(`/hotels/${id}/detail`),

  // licenses
  listLicenses: () => request<License[]>('/licenses'),
  createLicense: (data: { hotelId: string; plan?: string; type?: string; months?: number }) =>
    request<License>('/licenses', { method: 'POST', body: JSON.stringify(data) }),
  revokeLicense: (id: string, reason = '手动吊销') =>
    request<{ ok: boolean }>(`/licenses/${id}/revoke`, { method: 'POST', body: JSON.stringify({ reason }) }),

  // alerts
  listAlerts: () => request<Alert[]>('/alerts'),
  handleAlert: (id: string) => request<{ ok: boolean }>(`/alerts/${id}/handle`, { method: 'POST' }),
  alertGroups: () => request<AlertGroup[]>('/alerts/groups'),

  // users（admin）
  listUsers: () => request<User[]>('/users'),
  createUser: (data: { username: string; password: string; name: string; role: string }) =>
    request<{ ok: boolean }>('/users', { method: 'POST', body: JSON.stringify(data) }),

  // groups（总/子管理）
  listGroups: () => request<Group[]>('/groups'),
  createGroup: (data: { name: string; contact?: string; phone?: string; plan?: string; expireAt?: string }) =>
    request<{ ok: boolean; id: string; code: string }>('/groups', { method: 'POST', body: JSON.stringify(data) }),
  groupDetail: (id: string) =>
    request<{ group: Group; members: Hotel[] }>(`/groups/${id}`),
  assignHotels: (hotelIds: string[], groupId: string) =>
    request<{ ok: boolean }>('/groups/assign', { method: 'POST', body: JSON.stringify({ hotelIds, groupId }) }),
  groupRenew: (id: string) =>
    request<{ ok: boolean; renewed: number }>(`/groups/${id}/renew`, { method: 'POST' }),

  // wecom（企业微信机器人）
  wecomConfig: () =>
    request<{ opsWebhook: string; pushEnabled: boolean; dryRun: boolean; groupHooks: Record<string, string> }>('/wecom/config'),
  wecomConfigUpdate: (data: { opsWebhook: string; pushEnabled: boolean; dryRun: boolean }) =>
    request<{ ok: boolean }>('/wecom/config', { method: 'POST', body: JSON.stringify(data) }),
  wecomGroupHook: (groupId: string, webhookUrl: string) =>
    request<{ ok: boolean }>('/wecom/group-hook', { method: 'POST', body: JSON.stringify({ groupId, webhookUrl }) }),
  wecomTest: () => request<{ ok: boolean; sent: string[]; dryRun: boolean }>('/wecom/test', { method: 'POST' }),
  wecomFlushDaily: () => request<Record<string, unknown>>('/wecom/flush-daily', { method: 'POST' }),
  wecomSimulate: (text: string, userid = 'admin-sim') =>
    request<{ reply: string; ackSent: boolean; ack: string }>('/wecom/simulate', { method: 'POST', body: JSON.stringify({ text, userid }) }),

  // plans & stats
  listPlans: () => request<Plan[]>('/plans'),
  stats: () => request<{ total: number; devices: number; expiring: number; alerts: number; expireSoon: { d7: number; d30: number; d60: number; d90: number } }>('/stats'),

  // settings & logs
  getSettings: () => request<Record<string, string>>('/settings'),
  updateSetting: (key: string, value: string) =>
    request<{ ok: boolean }>('/settings', { method: 'POST', body: JSON.stringify({ key, value }) }),
  listLogs: () => request<AuditLog[]>('/logs'),

  // 大模型调用审计（每次 LLM 调用全量留痕，脱敏落库）
  listLlmLogs: (params?: { sourceType?: string; scene?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.sourceType) q.set('sourceType', params.sourceType)
    if (params?.scene) q.set('scene', params.scene)
    q.set('limit', String(params?.limit ?? 50))
    return request<LlmCallLog[]>(`/llm-logs?${q.toString()}`)
  },
  simulateLlmCall: (data: { sourceType: string; scene: string; userInput: string }) =>
    request<{ ok: boolean; result: { status: string; latencyMs: number } }>('/llm-logs/simulate', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // 云端 + 本地大模型接入（Ollama / llama.cpp / OpenAI 兼容云端 API）
  llmStatus: () =>
    request<{
      active: string
      fallback: string
      local: { provider: string; endpoint: string; model: string }
      cloud: { endpoint: string; model: string; apiKeySet: boolean }
      ping: { ok: boolean; detail: string; local?: { ok: boolean; detail: string }; cloud?: { ok: boolean; detail: string } }
    }>('/ai/llm-status'),
  llmConfigSave: (data: {
    active: string
    fallback: boolean
    local: { provider: string; endpoint: string; model: string }
    cloud: { endpoint: string; model: string; apiKey?: string }
  }) => request<{ ok: boolean }>('/ai/config', { method: 'POST', body: JSON.stringify(data) }),
  aiExplainAlert: (data: { alertId?: string; rule?: string; hotelName?: string; detail?: string }) =>
    request<{ ok: boolean; explain: string; audit: { status: string; model: string } }>('/ai/alert-explain', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}
