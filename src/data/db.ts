// 本地数据层 —— v0.0.1 使用 localStorage 持久化
// 接口形状与云数据库对齐，后续切换云服务只需替换本模块实现
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

const KEY = 'hotel_license_db_v1'

interface DB {
  hotels: Hotel[]
  licenses: License[]
  alerts: Alert[]
  plans: Plan[]
  user: { username: string; name: string } | null
}

function seed(): DB {
  const hotels: Hotel[] = [
    { id: 'h1', name: '杭州西湖大酒店', code: 'HZ-XH-001', group: '华住集团', city: '杭州', contact: '王建国', phone: '138****6721', plan: '旗舰版', status: '正常', devices: 86, maxDevices: 100, expireAt: '2027-03-15', autoRenew: true, createdAt: '2024-03-15', lastActive: '2026-11-01 09:12' },
    { id: 'h2', name: '上海外滩茂悦大酒店', code: 'SH-WT-002', group: '凯悦集团', city: '上海', contact: '李敏', phone: '139****3388', plan: '旗舰版', status: '正常', devices: 120, maxDevices: 150, expireAt: '2026-12-28', autoRenew: true, createdAt: '2024-06-20', lastActive: '2026-11-01 08:47' },
    { id: 'h3', name: '成都天府丽呈酒店', code: 'CD-TF-003', group: '丽呈集团', city: '成都', contact: '张伟', phone: '137****9042', plan: '专业版', status: '即将到期', devices: 42, maxDevices: 60, expireAt: '2026-11-08', autoRenew: false, createdAt: '2024-11-08', lastActive: '2026-10-31 21:30' },
    { id: 'h4', name: '深圳湾万怡酒店', code: 'SZ-WY-004', group: '万豪集团', city: '深圳', contact: '陈晓', phone: '135****2210', plan: '专业版', status: '正常', devices: 55, maxDevices: 60, expireAt: '2027-01-20', autoRenew: true, createdAt: '2025-01-20', lastActive: '2026-11-01 10:05' },
    { id: 'h5', name: '北京国贸亚朵酒店', code: 'BJ-GM-005', group: '亚朵集团', city: '北京', contact: '刘洋', phone: '136****7788', plan: '基础版', status: '正常', devices: 18, maxDevices: 30, expireAt: '2027-05-12', autoRenew: true, createdAt: '2025-05-12', lastActive: '2026-10-31 18:22' },
    { id: 'h6', name: '三亚海棠湾君悦', code: 'SY-HT-006', group: '凯悦集团', city: '三亚', contact: '赵静', phone: '133****4561', plan: '旗舰版', status: '即将到期', devices: 95, maxDevices: 100, expireAt: '2026-11-22', autoRenew: false, createdAt: '2024-11-22', lastActive: '2026-11-01 07:58' },
    { id: 'h7', name: '西安钟楼全季酒店', code: 'XA-ZL-007', group: '华住集团', city: '西安', contact: '孙磊', phone: '131****0099', plan: '基础版', status: '已过期', devices: 12, maxDevices: 30, expireAt: '2026-09-30', autoRenew: false, createdAt: '2024-09-30', lastActive: '2026-09-30 23:10' },
    { id: 'h8', name: '南京金陵饭店', code: 'NJ-JL-008', group: '金陵集团', city: '南京', contact: '周婷', phone: '130****3366', plan: '专业版', status: '正常', devices: 48, maxDevices: 60, expireAt: '2027-02-14', autoRenew: true, createdAt: '2025-02-14', lastActive: '2026-11-01 09:40' },
  ]
  const licenses: License[] = [
    { id: 'l1', code: 'LIC-2026-0892', hotelName: '杭州西湖大酒店', plan: '旗舰版', type: '续期', issuedAt: '2026-03-15', expireAt: '2027-03-15', status: '生效中', devices: 86, maxDevices: 100 },
    { id: 'l2', code: 'LIC-2026-0847', hotelName: '上海外滩茂悦大酒店', plan: '旗舰版', type: '新签', issuedAt: '2025-12-28', expireAt: '2026-12-28', status: '生效中', devices: 120, maxDevices: 150 },
    { id: 'l3', code: 'LIC-2026-0803', hotelName: '成都天府丽呈酒店', plan: '专业版', type: '续期', issuedAt: '2025-11-08', expireAt: '2026-11-08', status: '生效中', devices: 42, maxDevices: 60 },
    { id: 'l4', code: 'LIC-2026-0791', hotelName: '深圳湾万怡酒店', plan: '专业版', type: '升级', issuedAt: '2026-01-20', expireAt: '2027-01-20', status: '生效中', devices: 55, maxDevices: 60 },
    { id: 'l5', code: 'LIC-2026-0758', hotelName: '北京国贸亚朵酒店', plan: '基础版', type: '新签', issuedAt: '2025-05-12', expireAt: '2027-05-12', status: '生效中', devices: 18, maxDevices: 30 },
    { id: 'l6', code: 'LIC-2026-0731', hotelName: '三亚海棠湾君悦', plan: '旗舰版', type: '续期', issuedAt: '2025-11-22', expireAt: '2026-11-22', status: '生效中', devices: 95, maxDevices: 100 },
    { id: 'l7', code: 'LIC-2026-0688', hotelName: '西安钟楼全季酒店', plan: '基础版', type: '新签', issuedAt: '2025-09-30', expireAt: '2026-09-30', status: '已过期', devices: 12, maxDevices: 30 },
    { id: 'l8', code: 'LIC-2026-0652', hotelName: '南京金陵饭店', plan: '专业版', type: '新签', issuedAt: '2026-02-14', expireAt: '2027-02-14', status: '生效中', devices: 48, maxDevices: 60 },
  ]
  const alerts: Alert[] = [
    { id: 'a1', level: '紧急', rule: '破解特征检测', hotelName: '西安钟楼全季酒店', detail: '客户端完整性校验失败，疑似被篡改', time: '2026-11-01 10:22', status: '待处理' },
    { id: 'a2', level: '高', rule: '多设备并发激活', hotelName: '三亚海棠湾君悦', detail: '并发设备 103 台，超出授权上限 100 台', time: '2026-11-01 09:15', status: '处理中' },
    { id: 'a3', level: '高', rule: '时间回滚', hotelName: '成都天府丽呈酒店', detail: '客户端时间戳倒流 48 小时', time: '2026-10-31 22:40', status: '待处理' },
    { id: 'a4', level: '中', rule: '设备指纹变更', hotelName: '北京国贸亚朵酒店', detail: '2 台设备指纹哈希变化超过阈值', time: '2026-10-31 16:08', status: '已处置' },
    { id: 'a5', level: '中', rule: '心跳中断（72h）', hotelName: '西安钟楼全季酒店', detail: '连续 76 小时无心跳回连', time: '2026-10-30 08:00', status: '处理中' },
    { id: 'a6', level: '高', rule: 'License 共享检测', hotelName: '上海外滩茂悦大酒店', detail: '同一授权码在 3 个不同城市 IP 激活', time: '2026-10-29 14:33', status: '已处置' },
  ]
  const plans: Plan[] = [
    { id: 'p1', name: '基础版', price: 1999, yearlyPrice: 19999, maxDevices: 30, features: ['基础经营看板', '设备管理', '到期提醒', '标准客服支持'], hotels: 2, color: '#3B82F6' },
    { id: 'p2', name: '专业版', price: 4999, yearlyPrice: 49999, maxDevices: 60, features: ['全部基础版功能', 'AI 智能风控', '多门店集团管理', '批量操作与导入导出', '优先客服支持'], hotels: 3, color: '#8B5CF6' },
    { id: 'p3', name: '旗舰版', price: 9999, yearlyPrice: 99999, maxDevices: 150, features: ['全部专业版功能', '定制风控规则', '专属客户成功经理', 'API 开放接口', '私有化部署可选'], hotels: 3, color: '#F59E0B' },
  ]
  return { hotels, licenses, alerts, plans, user: null }
}

function load(): DB {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as DB
  } catch { /* corrupted → reseed */ }
  const db = seed()
  localStorage.setItem(KEY, JSON.stringify(db))
  return db
}

function save(db: DB) {
  localStorage.setItem(KEY, JSON.stringify(db))
}

export const db = {
  read(): DB { return load() },
  write(mutator: (d: DB) => void) {
    const d = load()
    mutator(d)
    save(d)
    return d
  },
  reset() {
    localStorage.removeItem(KEY)
    load()
  },
}
