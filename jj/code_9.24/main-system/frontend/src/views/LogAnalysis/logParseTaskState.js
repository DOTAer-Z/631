export const TERMINAL_PARSE_STATES = new Set(['cancelled', 'succeeded', 'failed'])

const STAGE_LABELS = {
  queued: '排队中',
  loading: '读取日志',
  preprocessing: '预处理',
  scoring: '规则评分',
  llm: '模型解析',
  persisting: '保存结果',
  completed: '已完成',
  cancelled: '已取消',
  failed: '解析失败',
}

export function isTerminalParseTask(state) {
  return TERMINAL_PARSE_STATES.has(state)
}

export function clampTaskProgress(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)))
}

export function parseTaskStageLabel(stage) {
  return STAGE_LABELS[stage] || '处理中'
}

export function shouldKeepPreviousResult({ hasAnalysis }) {
  return Boolean(hasAnalysis)
}
