<template>
  <el-config-provider>
    <RouterView />
  </el-config-provider>
</template>

<script setup lang="ts">
import { onBeforeUnmount } from 'vue'

import { clearPortalContext } from '@annotation/platform/portalContext'

// 深融合后不再有 postMessage 握手，也就不存在「门户连接失败」状态：
// 融合态由宿主注入登录态；独立部署以匿名上下文启动（后端不强制鉴权）。
onBeforeUnmount(clearPortalContext)
</script>
