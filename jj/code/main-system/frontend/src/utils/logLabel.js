// 统一「上传文件名 + 上传时间」显示标签（取代长 run_id）。
// 三页（故障诊断 / 日志分析 / 预测预警）对同一文件显示一致；无文件名则回退 run_id。

function fmtTime(v) {
  if (!v) return ''
  const s = String(v)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const d = new Date(hasTz ? s : s + 'Z')
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// row 可含：filename、run_id/id、uploaded_at/analyzed_at/created_at
export function logLabel(row) {
  if (!row) return '-'
  const name = row.filename
  const t = fmtTime(row.uploaded_at || row.analyzed_at || row.created_at)
  const id = row.run_id || row.id || ''
  if (name) return t ? `${name} · ${t}` : name
  return id || '-'
}

// 文件来源：upload=用户上传的文件 / dataset=从数据库(DB1/数据列表)选择的 run
export function sourceLabel(source) {
  return { upload: '上传文件', dataset: '数据库选择' }[source] || '-'
}

export { fmtTime }
