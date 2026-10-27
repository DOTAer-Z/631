<template>
  <div class="data-access-page">
    <!-- 关键日志接入 -->
    <el-card shadow="never" class="subsection-card">
      <template #header><span>关键日志接入</span></template>
      <div class="access-config">
        <el-form :model="logAccessConfig" label-width="120px">
          <!-- 接入策略和时间段 -->
          <el-form :inline="true" class="inline-form">
            <el-form-item label="接入策略">
              <el-radio-group v-model="logAccessConfig.strategy">
                <el-radio label="realtime">实时接入</el-radio>
                <el-radio label="timeRange">时间段接入</el-radio>
              </el-radio-group>
            </el-form-item>

            <!-- 时间段选择 -->
            <el-form-item label="时间段" v-if="logAccessConfig.strategy === 'timeRange'">
              <el-date-picker
                v-model="logAccessConfig.timeRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                value-format="YYYY-MM-DD HH:mm:ss"
                format="YYYY-MM-DD HH:mm:ss"
                style="width: 300px"
              />
            </el-form-item>
          </el-form>

          <!-- 异常评分、评分阈值、标签类型 -->
          <el-form :inline="true" class="inline-form">
            <el-form-item label="异常评分">
              <el-input-number v-model="logAccessConfig.scoreThreshold" :min="0" :max="100" :step="1" style="width: 120px" />
            </el-form-item>

            <el-form-item label="评分阈值">
              <el-input-number v-model="logAccessConfig.scoreThresholdValue" :min="0" :max="100" :step="1" style="width: 120px" />
            </el-form-item>

            <el-form-item label="标签类型">
              <el-select v-model="logAccessConfig.tagTypes" multiple placeholder="选择标签类型" style="width: 200px">
                <el-option label="故障" value="fault" />
                <el-option label="异常" value="abnormal" />
                <el-option label="警告" value="warning" />
                <el-option label="信息" value="info" />
              </el-select>
            </el-form-item>
          </el-form>

          <el-form-item>
            <el-button type="primary" @click="accessKeyLogs" :loading="accessingLogs">
              <el-icon><DataAnalysis /></el-icon> 开始接入
            </el-button>
          </el-form-item>
        </el-form>
        <div v-if="logAccessResult" class="access-result">
          <el-alert :title="logAccessResult.success ? '接入成功' : '接入失败'" :type="logAccessResult.success ? 'success' : 'error'" :closable="false" />
          <div v-if="logAccessResult.message" class="result-message">{{ logAccessResult.message }}</div>
          <div v-if="logAccessResult.report" class="standard-report">
            <el-collapse>
              <el-collapse-item title="接入报告" name="1">
                <pre>{{ JSON.stringify(logAccessResult.report, null, 2) }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </div>

      <!-- 接入结果列表 -->
      <div class="access-results-list">
        <el-card shadow="never" class="subsection-card" style="margin-top: 20px;">
          <template #header>
            <div class="header-flex">
              <span>接入结果列表</span>
              <el-button type="primary" size="small" @click="getAccessResults">
                <el-icon><Refresh /></el-icon> 刷新
              </el-button>
            </div>
          </template>

          <!-- 查询条件 -->
          <div class="filter-section">
            <el-form :inline="true" :model="filterForm" class="filter-form">
              <el-form-item label="接入时间">
                <el-date-picker
                  v-model="filterForm.dateRange"
                  type="daterange"
                  range-separator="至"
                  start-placeholder="开始日期"
                  end-placeholder="结束日期"
                  value-format="YYYY-MM-DD"
                  clearable
                />
              </el-form-item>
              <el-form-item label="接入来源">
                <el-select v-model="filterForm.source" placeholder="选择接入来源" clearable>
                  <el-option label="日志分析结果库" value="database" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="handleQuery">查询</el-button>
                <el-button @click="handleReset">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <!-- 结果列表 -->
          <el-table :data="accessResultsList" style="width: 100%">
            <el-table-column label="报告ID" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.report_id || row.run_id || row.id }}</template>
            </el-table-column>
            <el-table-column prop="accessTime" label="接入时间" width="240" />
            <el-table-column prop="source" label="来源" />
            <el-table-column prop="totalLogs" label="抽取数量" width="120" />
            <el-table-column prop="uniqueLogs" label="去重数量" width="120" />
            <el-table-column prop="taggedLogs" label="标记数量" width="120" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column label="操作" width="300">
              <template #default="scope">
                <el-button size="small" @click="viewReport(scope.row)">查看报告</el-button>
                <el-button size="small" type="primary" @click="gotoPrediction(scope.row)">软件状态预测</el-button>
                <el-button size="small" type="danger" link @click="removeReport(scope.row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 分页 -->
          <el-pagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="pagination.total"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="handleSizeChange"
            @current-change="handleCurrentChange"
            style="margin-top: 20px; text-align: right"
          />
        </el-card>
      </div>
    </el-card>
  </div>

  <!-- 报告查看弹窗 -->
  <el-dialog
    v-model="reportDialogVisible"
    title="接入报告"
    width="80%"
  >
    <pre>{{ JSON.stringify(currentReport, null, 2) }}</pre>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox, ElDialog } from 'element-plus'
import { DataAnalysis, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { accessKeyLogs as apiAccessKeyLogs, getAccessResults as apiGetAccessResults, deleteReport } from '@/api/prediction'

const router = useRouter()

// 关键日志接入配置
const logAccessConfig = reactive({
  strategy: 'timeRange',
  timeRange: [],
  scoreThreshold: 0,
  scoreThresholdValue: '',
  tagTypes: []
})
const accessingLogs = ref(false)
const logAccessResult = ref(null)

// 接入结果列表相关
const accessResultsList = ref([])
const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0
})

// 接入结果查询条件
const filterForm = reactive({
  dateRange: [],
  source: ''
})
const reportDialogVisible = ref(false)
const currentReport = ref(null)

// 获取接入结果列表
async function getAccessResults() {
  try {
    const params = {
      page: pagination.page,
      pageSize: pagination.pageSize,
      startDate: filterForm.dateRange[0] || '',
      endDate: filterForm.dateRange[1] || '',
      source: filterForm.source
    }
    const res = await apiGetAccessResults(params)
    accessResultsList.value = res.items
    pagination.total = res.total
  } catch (error) {
    ElMessage.error('获取接入结果列表失败')
  }
}

// 查询
function handleQuery() {
  pagination.page = 1
  getAccessResults()
}

// 重置
function handleReset() {
  filterForm.dateRange = []
  filterForm.source = ''
  pagination.page = 1
  getAccessResults()
}

// 分页大小变化
function handleSizeChange(size) {
  pagination.pageSize = size
  getAccessResults()
}

// 当前页码变化
function handleCurrentChange(page) {
  pagination.page = page
  getAccessResults()
}

// 查看报告
function viewReport(row) {
  currentReport.value = row.report
  reportDialogVisible.value = true
}

// 跳转到软件状态预测（对该接入报告做大模型分级）
function gotoPrediction() {
  router.push({ name: 'SoftwarePrediction' })
}

// 级联删除整份报告（连带分级、诊断记录）
async function removeReport(row) {
  const reportId = row.report_id || row.run_id
  if (!reportId) {
    ElMessage.warning('该报告缺少 report_id，无法删除（旧报告请忽略）')
    return
  }
  try {
    await ElMessageBox.confirm('删除将级联移除该报告及其分级、诊断记录，确定删除吗？', '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteReport(reportId)
    ElMessage.success('已删除')
    getAccessResults()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

// 页面挂载时获取接入结果列表
onMounted(() => {
  getAccessResults()
})

// 关键日志接入
async function accessKeyLogs() {
  accessingLogs.value = true
  try {
    if (logAccessConfig.strategy === 'timeRange' && !logAccessConfig.timeRange.length) {
      return ElMessage.warning('请选择时间段')
    }

    // 准备接入数据
    const data = new FormData()
    data.append('strategy', logAccessConfig.strategy)
    data.append('scoreThreshold', logAccessConfig.scoreThreshold)
    data.append('tagTypes', JSON.stringify(logAccessConfig.tagTypes))
    if (logAccessConfig.strategy === 'timeRange') {
      data.append('timeRange', JSON.stringify(logAccessConfig.timeRange))
    }

    const res = await apiAccessKeyLogs(data)
    if (res.success) {
      logAccessResult.value = {
        success: true,
        message: res.message || '成功接入关键日志',
        report: res.report
      }
      ElMessage.success('关键日志接入成功')
      // 接入成功后刷新接入结果列表
      getAccessResults()
    } else {
      logAccessResult.value = {
        success: false,
        message: res.message || '关键日志接入失败'
      }
      ElMessage.error('关键日志接入失败')
    }
  } catch (error) {
    logAccessResult.value = {
      success: false,
      message: error.message || '关键日志数据源连接异常，自动重试'
    }
    ElMessage.error('关键日志接入失败')
  } finally {
    accessingLogs.value = false
  }
}
</script>

<style scoped>
.data-access-page {
  padding: 20px;
}

.subsection-card {
  margin-bottom: 20px;
}

.access-result {
  margin-top: 20px;
}

.result-message {
  margin-top: 10px;
  padding: 10px;
  background-color: #f8f9fa;
  border-radius: 4px;
}

.standard-report {
  margin-top: 10px;
}

.standard-report pre {
  background-color: #f8f9fa;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
}

.header-flex {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.filter-section {
  margin-bottom: 20px;
}

.filter-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.access-results-list {
  margin-top: 20px;
}
</style>
