/**
 * 日期格式化工具：把后端返回的 ISO 字符串安全地转成本地时区显示。
 *
 * 后端的 PostgreSQL 用 naive UTC datetime 存储，序列化出来的 ISO 字符串（如
 * "2026-06-14T13:01:45.391117"）不带时区后缀。浏览器 `new Date(...)` 会按
 * **本地时区** 解析这种无后缀字符串，导致显示晚 8 小时。
 *
 * 这里统一：检测无 Z / 无 ±HH:MM 后缀的字符串视为 UTC，自动补 'Z' 再交给 Date。
 */

function _toUtcIso(value) {
  const s = String(value)
  // 已经带时区（Z 或 ±HH:MM 或 ±HHMM）的，原样返回
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)) return s
  return s + 'Z'
}

/** 标准带秒：2026-06-14 21:01:45 */
export function formatDateTime(value) {
  if (!value) return '-'
  return new Date(_toUtcIso(value))
    .toLocaleString('zh-CN', { hour12: false })
    .replace(/\//g, '-')
}

/** 同 formatDateTime，命名兼容 */
export const formatDate = formatDateTime
