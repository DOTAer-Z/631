<template>
  <el-config-provider>
    <main v-if="connectionFailed" class="portal-connection-error" role="alert">
      <h1>门户连接失败</h1>
      <p>无法从主系统获取当前连接上下文，请重新连接。</p>
      <el-button
        type="primary"
        :loading="retrying"
        data-testid="portal-context-retry"
        @click="retryConnection"
      >
        重新连接
      </el-button>
    </main>
    <RouterView v-else />
  </el-config-provider>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import { clearPortalContext, initializePortalContext } from '@annotation/platform/portalContext'

const props = withDefaults(defineProps<{
  initialPortalContextError?: boolean
}>(), {
  initialPortalContextError: false
})

const connectionFailed = ref(props.initialPortalContextError)
const retrying = ref(false)

async function retryConnection() {
  if (retrying.value) return
  retrying.value = true
  try {
    await initializePortalContext()
    connectionFailed.value = false
  } catch {
    connectionFailed.value = true
  } finally {
    retrying.value = false
  }
}

onBeforeUnmount(clearPortalContext)
</script>

<style scoped>
.portal-connection-error {
  display: flex;
  min-height: 100%;
  padding: 48px 24px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  text-align: center;
  background: #f8fafc;
}

.portal-connection-error h1,
.portal-connection-error p {
  margin: 0;
}

.portal-connection-error h1 {
  font-size: 22px;
  color: #b42318;
}

.portal-connection-error p {
  color: #475467;
}
</style>
