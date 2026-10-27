// Preview arbitrary file extensions without uploading or executing their contents.
export const FILE_PREVIEW_BYTES = 256 * 1024
export const FAULT_LABELS = Object.freeze({
  F1101: '硬件运行参数越限(CPU 核心温度超限)',
  F1206: '任务/线程运行与调度异常(进程/线程调度异常)',
  F2301: '虚拟资源或隔离异常(CPU 配额限流)',
  F3409: '设备访问与驱动运行异常(硬中断线数量激增)',
  F4512: '任务/线程运行与调度异常(系统长期高负载)',
  F4620: '系统资源与核心服务异常(可用内存枯竭)',
  F5106: '输入数据有效性异常(UDP 校验和错误)',
  F5203: '应用逻辑/状态机异常(监听端口数量异常增长)',
  F5313: '功能输出异常/功能失效(TCP 建连失败)',
  F5402: '功能时序与性能退化异常(已建立 TCP 连接数量异常增长)',
})
export const EXECUTED_FAULT_LABELS = Object.freeze({
  ...FAULT_LABELS,
  // Keep old codes readable in historical files and task details.
  F2305: '虚拟资源或隔离异常(容器进程数逼近配额上限)',
  F5201: '应用逻辑/状态机异常(Java 进程数异常膨胀)',
  F5403: '功能时序与性能退化异常(业务 TCP 重传偏高)',
})
// Historical files may contain the former request code and its recorded
// executed replacement. New requests always use the executed code directly.
export const FAULT_FALLBACKS = Object.freeze({ F2305: 'F2301', F5201: 'F5203', F5403: 'F5402' })
const FAULT_TYPES = new Set(Object.keys(FAULT_LABELS))
const normalizeLabel = text => text.replace(/（/g, '(').replace(/）/g, ')').replace(/\s+/g, '')
const describeType = code => `${code}（${EXECUTED_FAULT_LABELS[code] || '未知类型'}）`

function declaredIdentity(text) {
  // Only explicit JSON provenance can justify two different codes. Arbitrary
  // text mentioning two codes remains ambiguous, even for a valid fallback.
  try {
    const value = JSON.parse(text)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    const requested = value.requested_fault_type
    const executed = value.executed_fault_type
    if (typeof requested !== 'string' || typeof executed !== 'string') return null
    if (requested !== executed && FAULT_FALLBACKS[requested] !== executed) return null
    if (!FAULT_TYPES.has(executed)) return null
    if (value.fault_type !== undefined && value.fault_type !== executed) return null
    return { requested, executed }
  } catch { return null }
}

function decodeText(bytes) {
  const hasUtf16Bom = bytes.length >= 2 && ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff))
  const encodings = hasUtf16Bom ? [bytes[0] === 0xff ? 'utf-16le' : 'utf-16be'] : ['utf-8', 'gb18030']
  for (const encoding of encodings) {
    try {
      const text = new TextDecoder(encoding, { fatal: true }).decode(bytes)
      if (!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text)) return { text, encoding }
    } catch { /* Try the next text encoding, then use a binary preview. */ }
  }
  return null
}

function hexPreview(bytes) {
  const lines = []
  for (let offset = 0; offset < Math.min(bytes.length, 256); offset += 16) {
    const hex = [...bytes.slice(offset, offset + 16)].map(value => value.toString(16).padStart(2, '0')).join(' ')
    lines.push(`${offset.toString(16).padStart(8, '0')}  ${hex}`)
  }
  return lines.join('\n')
}

export async function readLocalFaultFile(file, expectedFaultType) {
  const bytes = new Uint8Array(await file.slice(0, FILE_PREVIEW_BYTES).arrayBuffer())
  const decoded = decodeText(bytes)
  const truncated = file.size > bytes.length
  // Both codes and full category(subtype) labels identify the same allow-listed
  // type. Conflicting declarations reject the file; filenames are never evidence.
  const content = decoded ? normalizeLabel(decoded.text) : ''
  const codes = decoded ? [...new Set([
    ...(decoded.text.match(/\bF\d{4}\b/g) || []),
    ...Object.entries(EXECUTED_FAULT_LABELS).filter(([, label]) => content.includes(normalizeLabel(label))).map(([code]) => code),
  ])] : []
  const identity = decoded ? declaredIdentity(decoded.text) : null
  const validIdentity = identity && codes.every(code => [identity.requested, identity.executed].includes(code))
  const faultType = validIdentity ? identity.executed : codes.length === 1 ? codes[0] : null
  const expectedExecutedType = expectedFaultType
  let status, message
  if (!decoded) {
    status = 'binary'
    message = '文件已读取，当前显示二进制摘要；无法从该内容识别故障类型。压缩包不会自动解压。'
  } else if (truncated) {
    status = 'partial'
    message = '大文件仅预览前 256 KiB，尚未校验完整内容，不能确认故障类型匹配。'
  } else if (!codes.length) {
    status = 'unknown'
    message = '文件内容中未找到明确的 Fxxxx 故障代码或完整大类(小类)名称，无法确认类型匹配。'
  } else if (content.includes('网卡帧错误速率超限') && codes.includes('F1206')) {
    status = 'mismatch'
    message = '文件将 F1206 声明为网卡帧错误，与新版 Agent 的进程/线程调度异常不一致。'
  } else if (codes.length > 1 && !validIdentity) {
    status = 'ambiguous'
    message = `文件包含多个或相互冲突的故障类型（${codes.map(describeType).join('、')}），无法确认唯一类型。`
  } else if (!FAULT_TYPES.has(faultType) || faultType !== expectedExecutedType) {
    status = 'mismatch'
    message = `故障文件类型为 ${describeType(faultType)}，与当前选择的 ${describeType(expectedFaultType)} 不匹配。`
  } else {
    status = 'matched'
    message = `文件故障类型与当前选择的 ${describeType(expectedFaultType)} 一致。此检查确认类型，不代表已验证完整故障数据格式。`
  }
  return {
    name: file.name || '本地文件', size: file.size, readBytes: bytes.length,
    encoding: decoded?.encoding || '二进制', truncated, codes, status, message,
    requestedFaultType: validIdentity ? identity.requested : faultType,
    executedFaultType: validIdentity ? identity.executed : faultType,
    preview: decoded ? decoded.text : hexPreview(bytes),
  }
}
