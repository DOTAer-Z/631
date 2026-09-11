<template>
  <div class="annotation-section">
    <!-- 页内水平导航：标注内页在「数据治理 → 数据标注」section 内部切换，不污染主系统侧边栏菜单 -->
    <el-menu
      class="annotation-section-nav"
      :default-active="activeMenu"
      mode="horizontal"
      router
      background-color="#ffffff"
      text-color="#475569"
      active-text-color="#2563eb"
      :ellipsis="false"
    >
      <el-menu-item
        v-for="item in innerMenuItems"
        :key="item.path"
        :index="item.path"
      >
        <span>{{ item.title }}</span>
      </el-menu-item>
    </el-menu>

    <el-main class="annotation-section-main">
      <router-view />
    </el-main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { annotationRoutes } from '@annotation/router'

// 必须实例化 useRoute 并绑定到 route，否则下方 activeMenu 里 `route.meta` 在本组件作用域
// 永远不定义 → 渲染即抛 `ReferenceError: route is not defined`，整个标注 section 白屏。
const route = useRoute()

// 标注内页 section 挂载的完整前缀（与主系统 router 中 annotation 子路由的 path 对应）
const SECTION_BASE = '/data-governance/annotation'

// 标注页内导航项：取 annotationRoutes 中「非 hidden」的顶层项
const innerMenuItems = computed(() =>
  annotationRoutes
    .filter((r) => r.path && !r.meta?.hidden)
    .map((r) => ({
      path: `${SECTION_BASE}/${r.path}`,
      title: String(r.meta?.title ?? r.name ?? r.path),
    }))
)

const activeMenu = computed(() => {
  // 内页用 meta.activeMenu（如 /packages、/slicing、/annotation）→ 补全
  // 无 activeMenu 的入口页（如 dashboard）→ 用当前路径在 section 内的相对段
  const metaActive = String(route.meta?.activeMenu ?? '')
  if (metaActive) return `${SECTION_BASE}${metaActive}`
  return `${SECTION_BASE}/dashboard`
})
</script>

<style scoped>
.annotation-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #f8fafc;
}

.annotation-section-nav {
  border-bottom: 1px solid #e2e8f0;
}

.annotation-section-nav :deep(.el-menu-item) {
  border-bottom: 2px solid transparent;
}

.annotation-section-nav :deep(.el-menu-item.is-active) {
  border-bottom-color: #2563eb;
  font-weight: 600;
}

.annotation-section-main {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}
</style>
