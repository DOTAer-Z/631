<template>
  <div class="page-shell">
    <div class="page-toolbar dashboard-toolbar">
      <div>
        <div class="page-toolbar__title">运行概览</div>
        <div class="page-toolbar__hint">汇总导入、切片、窗口与标注最近活动</div>
      </div>

      <el-button type="primary" :loading="store.refreshing" data-testid="dashboard-refresh" @click="refreshData">
        刷新数据
      </el-button>
    </div>

    <div class="dashboard-stats" v-loading="store.loading">
      <el-card v-for="card in summaryCards" :key="card.label" shadow="never" class="console-card stat-card">
        <div class="stat-card__value">{{ card.value }}</div>
        <div class="stat-card__label">{{ card.label }}</div>
        <div class="stat-card__hint">{{ card.hint }}</div>
      </el-card>
    </div>

    <el-row :gutter="20">
      <el-col :span="24">
        <el-card shadow="never" class="console-card section-card">
          <template #header>
            <div class="section-card__header">
              <span>最近上传包</span>
              <span class="section-card__meta">{{ store.recentPackages.length }} 条</span>
            </div>
          </template>

          <el-table :data="store.recentPackages" size="small" empty-text="暂无数据包">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="name" label="名称" min-width="220" />
            <el-table-column prop="import_status" label="导入状态" width="140">
              <template #default="{ row }">
                <el-tag :type="packageStatusTag(row.import_status)" effect="light">{{ row.import_status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" min-width="180">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20">
      <el-col :span="12">
        <el-card shadow="never" class="console-card section-card">
          <template #header>
            <div class="section-card__header">
              <span>最近切片任务</span>
              <span class="section-card__meta">{{ store.recentSliceTasks.length }} 条</span>
            </div>
          </template>

          <el-table :data="store.recentSliceTasks" size="small" empty-text="暂无切片任务">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="name" label="任务名称" min-width="180" />
            <el-table-column prop="package_id" label="数据包" width="100" />
            <el-table-column prop="status" label="状态" width="120">
              <template #default="{ row }">
                <el-tag :type="taskStatusTag(row.status)" effect="light">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never" class="console-card section-card">
          <template #header>
            <div class="section-card__header">
              <span>最近标注记录</span>
              <span class="section-card__meta">{{ store.recentAnnotations.length }} 条</span>
            </div>
          </template>

          <el-table :data="store.recentAnnotations" size="small" empty-text="暂无标注记录">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="slice_window_id" label="窗口" width="100" />
            <el-table-column prop="label" label="标签" width="120">
              <template #default="{ row }">
                <el-tag :type="annotationTag(row.label)" effect="light">{{ row.label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="anomaly_type" label="异常类型" width="140">
              <template #default="{ row }">{{ row.anomaly_type || '-' }}</template>
            </el-table-column>
            <el-table-column label="更新时间" min-width="180">
              <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted } from 'vue'

import { useDashboardStore } from '@annotation/stores/dashboardStore'

const store = useDashboardStore()

const summaryCards = computed(() => [
  {
    label: '数据包',
    value: store.summary.total_packages,
    hint: '已上传并进入平台管理的数据包'
  },
  {
    label: '切片任务 / 窗口',
    value: `${store.summary.total_slice_tasks} / ${store.summary.total_windows}`,
    hint: '数据库驱动切片任务与物化窗口总量'
  },
  {
    label: '已标注 / 待标注',
    value: `${store.summary.annotated_windows} / ${store.summary.pending_windows}`,
    hint: '当前窗口标注进度'
  },
  {
    label: 'normal / abnormal',
    value: `${store.summary.normal_count} / ${store.summary.abnormal_count}`,
    hint: '标注结果分布'
  },
  {
    label: '数据包（结构化 / 半结构化 / 非结构化）',
    value: `${store.summary.structured_packages ?? 0} / ${store.summary.semi_structured_packages ?? 0} / ${store.summary.unstructured_packages ?? 0}`,
    hint: '结构化=监控/追踪(保留桶)，半结构化=日志，非结构化=报告类文本'
  },
  {
    label: '窗口·语义段（结构化 / 半结构化 / 非结构化）',
    value: `${store.summary.structured_windows ?? 0} / ${store.summary.semi_structured_windows ?? 0} / ${store.summary.unstructured_windows ?? 0}`,
    hint: '结构化=时间窗口(保留桶)，半结构化=日志时间窗口，非结构化=报告语义段'
  }
])

function formatDateTime(value: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function packageStatusTag(status: string) {
  if (status === 'imported') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'importing') return 'warning'
  return 'info'
}

function taskStatusTag(status: string) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function annotationTag(label: string) {
  return label === 'abnormal' ? 'danger' : 'success'
}

async function refreshData() {
  try {
    await store.refreshDashboard()
  } catch {
    ElMessage.error('刷新 Dashboard 失败')
  }
}

onMounted(() => {
  void store.initialize().catch(() => {
    ElMessage.error('加载 Dashboard 失败')
  })
})
</script>

<style scoped>
.dashboard-toolbar {
  margin-bottom: 4px;
}

.dashboard-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}

.stat-card {
  min-height: 152px;
}

.stat-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  min-height: 150px;
}

.stat-card__value {
  font-size: 30px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.1;
}

.stat-card__label {
  font-size: 15px;
  font-weight: 600;
  color: #334155;
}

.stat-card__hint {
  font-size: 13px;
  line-height: 1.6;
  color: #64748b;
}

.section-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
  color: #0f172a;
}

.section-card__meta {
  font-size: 13px;
  font-weight: 500;
  color: #64748b;
}

@media (max-width: 1200px) {
  .dashboard-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .dashboard-stats {
    grid-template-columns: 1fr;
  }
}
</style>
