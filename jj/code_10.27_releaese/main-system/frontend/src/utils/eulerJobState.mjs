export const ACTIVE_JOB_STATUSES = new Set(['queued', 'preparing', 'running', 'finalizing', 'cancel_requested'])
export const TERMINAL_JOB_STATUSES = new Set(['succeeded', 'partial_failed', 'failed', 'capability_failed', 'cancelled', 'acceleration_target_not_met', 'dry_run', 'interrupted'])
export const isActiveJob = status => ACTIVE_JOB_STATUSES.has(status)
export const isTerminalJob = status => TERMINAL_JOB_STATUSES.has(status)
export const canCancelJob = status => ['queued', 'preparing', 'running'].includes(status)
export const hasJobArtifact = job => ['succeeded', 'acceleration_target_not_met'].includes(job?.status)
  && !!job.artifact_path && !job.artifact_error
export const requestedFaultType = job => job?.requested_fault_type
  || job?.summary?.requested_fault_type
  || job?.job?.requested_fault_type
  || job?.job?.options?.requested_fault_type
  || job?.job?.fault_type
  || job?.fault_type
export const executedFaultType = job => job?.executed_fault_type
  || job?.summary?.executed_fault_type
  || job?.job?.executed_fault_type
  || job?.job?.fault_type
  || job?.fault_type
export const jobStatusLabel = status => ({ queued: '排队中', preparing: '准备中', running: '执行中', finalizing: '收尾中', cancel_requested: '请求停止', succeeded: '已完成', partial_failed: '部分失败', failed: '失败', capability_failed: '能力不足', cancelled: '已取消', acceleration_target_not_met: '未达到加速目标', dry_run: '预演完成', interrupted: '已中断' })[status] || status || '未知'
