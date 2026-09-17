<template>
  <el-card>
    <template #header>
      <div class="card-header">服务健康检查</div>
    </template>

    <el-space direction="vertical" fill>
      <el-button type="primary" :loading="loading" @click="loadHealth">
        刷新健康状态
      </el-button>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <el-descriptions v-else border :column="1" title="Health API 响应">
        <el-descriptions-item label="status">{{ health?.status ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="version">{{ health?.version ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="db_connected">{{ health?.database?.connected ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="raw">{{ rawJson }}</el-descriptions-item>
      </el-descriptions>
    </el-space>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { getHealth, type HealthResponse } from '@annotation/api/health'

const loading = ref(false)
const health = ref<HealthResponse | null>(null)
const errorMessage = ref('')

const rawJson = computed(() => {
  if (!health.value) {
    return '-'
  }

  return JSON.stringify(health.value)
})

async function loadHealth() {
  loading.value = true
  errorMessage.value = ''

  try {
    health.value = await getHealth()
  } catch (error) {
    errorMessage.value = '健康检查请求失败，请确认后端服务可用。'
    health.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadHealth()
})
</script>

<style scoped>
.card-header {
  font-weight: 600;
}
</style>
