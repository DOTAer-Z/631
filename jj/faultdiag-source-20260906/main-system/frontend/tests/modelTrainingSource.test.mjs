import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const trainingPath = new URL('../src/views/ModelManagement/Training.vue', import.meta.url)
const dialogPath = new URL('../src/views/ModelManagement/components/TrainingTaskDialog.vue', import.meta.url)
const drawerPath = new URL('../src/views/ModelManagement/components/TrainingTaskDrawer.vue', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

test('training management exposes task, Adapter, and Worker operational tabs', async () => {
  const training = await source(trainingPath)

  assert.match(training, /<el-tab-pane[^>]+label="训练任务"[^>]+name="tasks"/)
  assert.match(training, /<el-tab-pane[^>]+label="Adapter"[^>]+name="adapters"/)
  assert.match(training, /<el-tab-pane[^>]+label="Worker"[^>]+name="worker"/)
  assert.match(training, /class="training-tabs"/)
  assert.match(training, /listTrainingTasks/)
  assert.match(training, /listTrainingArtifacts/)
  assert.match(training, /getTrainingWorker/)
  assert.match(training, /queue_position/)
  assert.match(training, /latest_metric\.loss/)
  assert.match(training, /gpu/)
  assert.match(training, /canCancel\(row\)/)
  assert.match(training, /canRetry\(row\)/)
  assert.match(training, /canEvaluate\(row\)/)
  assert.match(training, /createSerialPoller/)
  assert.match(training, /mainPoller\.start\(\)/)
  assert.match(training, /onBeforeUnmount/)
  assert.match(training, /mainPoller\.stop\(\)/)
  assert.doesNotMatch(training, /setInterval/)
})

test('six-step task dialog selects Tests once and previews a bounded filtered split', async () => {
  const dialog = await source(dialogPath)

  assert.match(dialog, /<el-step[^>]+title="类型与名称"/)
  assert.match(dialog, /<el-step[^>]+title="选择 Test"/)
  assert.match(dialog, /<el-step[^>]+title="划分预览"/)
  assert.match(dialog, /<el-step[^>]+title="训练参数"/)
  assert.match(dialog, /<el-step[^>]+title="SFT 起点"/)
  assert.match(dialog, /<el-step[^>]+title="确认提交"/)
  assert.match(dialog, /type="selection"/)
  assert.match(dialog, /row-key="version_id"/)
  assert.doesNotMatch(dialog, /Run checkbox|runSelected|selectedRuns|type="selection"[^]*type="selection"/i)
  assert.match(dialog, /round_1_parse_status/)
  assert.match(dialog, /round_2_parse_status/)
  assert.match(dialog, /missing_files/)
  assert.match(dialog, /listTrainingTests/)
  assert.match(dialog, /previewTrainingSplit/)
  assert.match(dialog, /createTrainingTask/)
  assert.match(dialog, /page_size:\s*100/)
  assert.match(dialog, /collectPaginatedIds/)
  assert.match(dialog, /completeness:\s*['"]complete['"]/)
  assert.match(dialog, /选择全部筛选结果/)
  assert.match(dialog, /quick/)
  assert.match(dialog, /formal/)
  assert.match(dialog, /overrides/)
  assert.match(dialog, /sft_source/)
  assert.match(dialog, /cpt_adapter_artifact_id/)
  assert.match(dialog, /buildSplitPreviewPayload/)
  assert.match(dialog, /buildTaskPayload/)
})

test('training dialog wires lifecycle, preview, and paginated selection state helpers', async () => {
  const dialog = await source(dialogPath)
  const lifecycleStarts = dialog.match(/dialogRequests\.start\(\)/g) || []

  assert.match(dialog, /from ['"]\.\.\/trainingUiState['"]/)
  assert.match(dialog, /createLatestRequestState/)
  assert.match(dialog, /collectPaginatedIds/)
  assert.match(dialog, /dialogRequests\.start\(\)/)
  assert.equal(lifecycleStarts.length, 1, 'one lifecycle token must be created per dialog open')
  assert.match(dialog, /dialogToken\s*=\s*dialogRequests\.start\(\)/)
  assert.match(dialog, /dialogRequests\.isLatest\(lifecycleToken\)\s*&&\s*visible\.value/)
  assert.match(dialog, /advanceRequests\.start\(\)/)
  assert.match(dialog, /advanceRequests\.isLatest\(operationToken\)/)
  assert.match(dialog, /previewRequests\.start\(\)/)
  assert.match(dialog, /previewRequests\.isLatest\(requestToken\)/)
  assert.match(dialog, /Object\.freeze\(buildSplitPreviewPayload/)
  assert.match(dialog, /function invalidatePreview\(\)[\s\S]*?previewRequests\.invalidate\(\)[\s\S]*?splitPreview\.value\s*=\s*null/)
  assert.match(dialog, /watch\(\[\(\)\s*=>\s*form\.train_ratio[\s\S]*?invalidatePreview/)
  assert.match(dialog, /collectPaginatedIds\(\{[\s\S]*?isCurrent:[\s\S]*?selectAllRequests\.isLatest/)
  assert.match(dialog, /selectAllRequests\.invalidate\(\)/)
  assert.match(dialog, /:disabled="selectionLocked"/)
  assert.match(dialog, /:selectable="row\s*=>\s*!selectionLocked/)
  assert.match(dialog, /async function refreshPreview\(\)[\s\S]*?validateSplitStep\(\)[\s\S]*?try\s*{[\s\S]*?await loadPreview\(dialogToken\)[\s\S]*?catch/)
  assert.match(dialog, /v-if="errors\.seed"[^>]+:title="errors\.seed"/)
  assert.match(dialog, /v-if="step < 5"[^>]+:disabled="selectionLocked"[^>]+@click="nextStep"/)
  assert.match(dialog, /function resetDialog\(\)[\s\S]*?invalidateDialogWork\(\)/)
  assert.match(dialog, /function searchTests\(\)[\s\S]*?loadTests\(dialogToken\)/)
  assert.match(dialog, /function changeTestPage\(\)[\s\S]*?loadTests\(dialogToken\)/)
  assert.match(dialog, /async function refreshPreview\(\)[\s\S]*?loadPreview\(dialogToken\)/)
  assert.match(dialog, /async function selectAllFiltered\(\)[\s\S]*?const lifecycleToken = dialogToken/)
  assert.match(dialog, /async function submitTask\(\)[\s\S]*?const lifecycleToken = dialogToken/)
  assert.match(dialog, /v-if="step > 0"[^>]+:disabled="selectionLocked \|\| advancing \|\| submitting"[^>]+@click="previousStep"/)
  assert.match(dialog, /function previousStep\(\)[\s\S]*?submitRequests\.invalidate\(\)[\s\S]*?advanceRequests\.invalidate\(\)[\s\S]*?step\.value -= 1/)
  assert.match(dialog, /if \(result\.truncated\)[\s\S]*?ElMessage\.warning/)
})

test('training worker renders only consistent public compute-device fields', async () => {
  const training = await source(trainingPath)

  assert.match(training, /label="计算设备"/)
  assert.doesNotMatch(training, /label="GPU"/)
  assert.match(training, /cpu_qlora_4bit_bf16:\s*'CPU · 4-bit BF16'/)
  assert.match(training, /cpu_qlora_4bit_fp32:\s*'CPU · 4-bit FP32'/)
  assert.match(training, /cpu_lora_bf16:\s*'CPU · LoRA BF16'/)
  assert.match(training, /cpu_lora_fp32:\s*'CPU · LoRA FP32'/)
  assert.match(training, /device_type:\s*null,\s*profile_name:\s*null,\s*device:\s*null,\s*gpu:\s*null/)
  assert.match(training, /deviceType === 'cpu'[\s\S]*?device !== 'CPU'[\s\S]*?cpuProfileLabels\[profileName\] \|\| '-'/)
  assert.match(training, /deviceType === 'cuda'[\s\S]*?profileName !== 'cuda_qlora_bf16'[\s\S]*?typeof device !== 'string'[\s\S]*?return device/)
  assert.doesNotMatch(training, /worker\.value\.gpu|\.gpu\.(?:name|index|memory_|utilization_)/)
})

test('training actions use keyed pending state and task compute device is assignment-specific', async () => {
  const training = await source(trainingPath)
  const taskDisabled = training.match(/:disabled="isTaskPending\(row\.id\)"/g) || []

  assert.match(training, /from ['"]\.\/trainingUiState['"]/)
  assert.match(training, /createKeyedOperationState/)
  assert.match(training, /actionOperations\.start\(`task:\$\{row\.id\}`/)
  assert.match(training, /actionOperations\.start\(`artifact:\$\{row\.id\}`/)
  assert.match(training, /finally\s*{[\s\S]*?actionOperations\.finish\(operationToken\)/)
  assert.ok(taskDisabled.length >= 4, 'all task mutations must be disabled while their row is pending')
  assert.match(training, /:loading="isTaskAction\(row\.id, 'cancel'\)"/)
  assert.match(training, /:loading="isTaskAction\(row\.id, 'retry'\)"/)
  assert.match(training, /:loading="isTaskAction\(row\.id, 'evaluate'\)"/)
  assert.match(training, /:loading="isTaskAction\(row\.id, 'delete'\)"/)
  assert.match(training, /:loading="isArtifactAction\(row\.id, 'delete'\)"/)
  assert.match(training, /worker\.value\.current_task_id\s*===\s*row\.id\s*\?\s*deviceSummary\.value\s*:\s*'-'/)
  assert.match(training, /\{\{ taskDeviceSummary\(row\) \}\}/)
})

test('task drawer streams capped cursor logs and disposes its ECharts instance', async () => {
  const drawer = await source(drawerPath)

  assert.match(drawer, /getTrainingTask/)
  assert.match(drawer, /getTrainingTaskLogs/)
  assert.match(drawer, /timeline/)
  assert.match(drawer, /split_counts/)
  assert.match(drawer, /config/)
  assert.match(drawer, /metrics/)
  assert.match(drawer, /artifacts/)
  assert.match(drawer, /next_after_line/)
  assert.match(drawer, /MAX_LOG_LINES/)
  assert.match(drawer, /\.slice\(-MAX_LOG_LINES\)/)
  assert.match(drawer, /await loadDetailUpdate\(lifecycleToken\)[\s\S]*await pollLogs\(lifecycleToken\)/)
  assert.match(drawer, /logs\.value = \[\.\.\.logs\.value, \.\.\.\(result\.lines \|\| \[\]\)\]/)
  assert.match(drawer, /nextAfterLine\.value = result\.next_after_line/)
  assert.doesNotMatch(drawer, /task\.metrics[\s\S]{0,120}pollLogs/)
  assert.match(drawer, /echarts\.init/)
  assert.match(drawer, /addEventListener\(['"]resize['"]/)
  assert.match(drawer, /removeEventListener\(['"]resize['"]/)
  assert.match(drawer, /chart\.dispose\(\)/)
  assert.match(drawer, /onBeforeUnmount/)
  assert.match(drawer, /<el-tooltip/)
  assert.match(drawer, /@element-plus\/icons-vue/)
})

test('task drawer shows evaluation source training loss without score cards', async () => {
  const drawer = await source(drawerPath)

  assert.match(drawer, /task\.job_kind === ['"]training['"]/)
  assert.match(drawer, /task\.job_kind === ['"]evaluation['"]/)
  assert.match(drawer, /sourceDetailRequests/)
  assert.match(drawer, /loadSourceTask/)
  assert.match(drawer, /getTrainingTask\(parentTaskId\)/)
  assert.match(drawer, /validLossMetrics/)
  assert.match(drawer, /chartMetrics/)
  assert.match(drawer, /来源训练 Loss/)
  assert.match(drawer, /来源训练指标加载失败/)
  assert.match(drawer, /来源训练任务未记录 Loss/)
  assert.match(drawer, /description="暂无训练指标"/)
  assert.match(drawer, /sourceTask\.value\s*=\s*null/)
  assert.match(drawer, /sourceTaskError\.value\s*=\s*['"]['"]/)
  assert.match(drawer, /sourceDetailRequests\.invalidate\(\)/)
  assert.doesNotMatch(drawer, /evaluationMetricItems|metric\.displayValue|evaluation-metric-grid/)
})

test('task drawer orders detail work and uses stopped serialized polling', async () => {
  const drawer = await source(drawerPath)

  assert.match(drawer, /from ['"]\.\.\/trainingUiState['"]/)
  assert.match(drawer, /createLatestRequestState/)
  assert.match(drawer, /createSerialPoller/)
  assert.match(drawer, /detailRequests\.start\(\)/)
  assert.match(drawer, /detailRequests\.isLatest\(requestToken\)/)
  assert.match(drawer, /drawerPoller\.start\(\)/)
  assert.match(drawer, /drawerPoller\.stop\(\)/)
  assert.match(drawer, /onError/)
  assert.doesNotMatch(drawer, /setInterval/)
})

test('training dialog and drawer have usable narrow-screen layouts', async () => {
  const [dialog, drawer] = await Promise.all([source(dialogPath), source(drawerPath)])

  assert.match(dialog, /class="steps-scroll"/)
  assert.match(dialog, /@media\s*\(max-width:\s*760px\)[\s\S]*?\.steps-scroll[\s\S]*?overflow-x:\s*auto/)
  assert.match(dialog, /@media\s*\(max-width:\s*760px\)[\s\S]*?\.ratio-form[\s\S]*?flex-direction:\s*column/)
  assert.match(drawer, /:column="descriptionColumns"/)
  assert.match(drawer, /:span="descriptionColumns === 1 \? 1 : 2"/)
  assert.match(drawer, /descriptionColumns\.value\s*=\s*window\.innerWidth\s*<=\s*640\s*\?\s*1\s*:\s*3/)
})

test('training UI does not expose out-of-scope model lifecycle controls or raw paths', async () => {
  const combined = [
    await source(trainingPath),
    await source(dialogPath),
    await source(drawerPath),
  ].join('\n')

  assert.doesNotMatch(combined, /模型下载|自动评估|发布模型|模型发布|部署模型|推理切换|切换推理|filesystem_path|relative_path|output_path/i)
})
