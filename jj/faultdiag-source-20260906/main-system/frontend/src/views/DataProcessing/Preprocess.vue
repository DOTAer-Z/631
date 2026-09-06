<template>
  <!--
    数据标注 (原 日志预处理)
    通过 iframe 嵌入「日志数据标注子系统」，由 nginx 反向代理到同源路径 /annotate/。
    nginx 同时把 /annotate-api/v1/ 转发到标注后端 :8000，所以子系统内部
    所有 axios 请求 (VITE_API_BASE_URL=/annotate-api/v1) 都是同源调用，无需 CORS。
  -->
  <div class="preprocess-embed">
    <iframe
      ref="annotationIframe"
      :src="iframeSrc"
      class="preprocess-iframe"
      title="数据标注"
      referrerpolicy="origin"
    />
  </div>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { installAnnotationContextBridge } from '../../utils/annotationContextBridge.mjs'
import {
  getAppOrigin,
  getAnnotateUrl,
  getPlatformContext,
} from '../../utils/platformRuntime.mjs'
import { APP_CLEANUP_KEY } from '../../utils/wujieLifecycle.mjs'

const annotationIframe = ref(null)
const iframeSrc = computed(() => getAnnotateUrl('/annotate/'))
const cleanup = inject(APP_CLEANUP_KEY, null)
let disposeBridge = null
let unregisterRuntimeCleanup = null

onMounted(() => {
  const iframe = annotationIframe.value
  if (!iframe) return
  disposeBridge = installAnnotationContextBridge({
    windowRef: window,
    iframe,
    context: () => getPlatformContext(),
    appOrigin: getAppOrigin(),
  })
  unregisterRuntimeCleanup = cleanup?.register(disposeBridge) ?? null
})

onBeforeUnmount(() => {
  disposeBridge?.()
  disposeBridge = null
  unregisterRuntimeCleanup?.()
  unregisterRuntimeCleanup = null
})
</script>

<style scoped>
.preprocess-embed {
  width: 100%;
  height: 100%;
  min-height: 0;
  margin: 0;
  padding: 0;
  background: #fff;
}

.preprocess-iframe {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
}
</style>
