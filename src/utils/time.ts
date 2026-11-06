/**
 * 时间展示工具 —— 库存 UTC，对用户一律换算北京时间（UTC+8）展示。
 * 与后端 wecom._fmt_bj 同规则：纯日期（YYYY-MM-DD）原样返回，带时间的串 +8 小时。
 */
export function fmtBJ(value?: string | null, opts?: { keepSeconds?: boolean }): string {
  const s = (value || '').trim()
  if (!s) return '—'
  // 纯日期无时区语义（如到期日），原样展示
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return s
  const [, y, mo, d, h, mi, sec] = m
  const utc = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +(sec || 0)))
  const bj = new Date(utc.getTime() + 8 * 3600 * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  const base = `${bj.getFullYear()}-${p(bj.getMonth() + 1)}-${p(bj.getDate())} ${p(bj.getHours())}:${p(bj.getMinutes())}`
  return opts?.keepSeconds ? `${base}:${p(bj.getSeconds())}` : base
}
