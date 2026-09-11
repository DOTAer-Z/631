<template>
  <el-container class="app-layout">
    <el-aside width="220px" class="layout-aside">
      <div class="layout-logo">
        <div class="layout-logo__mark">L</div>
        <div class="layout-logo__body">
          <strong>日志标注平台</strong>
          <span>Database Driven V1</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        class="layout-menu"
        router
        background-color="#ffffff"
        text-color="#475569"
        active-text-color="#2563eb"
        :collapse-transition="false"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div>
          <div class="layout-header__title">{{ currentTitle }}</div>
          <div class="layout-header__subtitle">{{ currentSubtitle }}</div>
        </div>
      </el-header>

      <el-main class="layout-main">
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const menuItems = computed(() =>
  (router.options.routes[0]?.children ?? [])
    .filter((item) => !item.meta?.hidden)
    .map((item) => ({
      path: `/${item.path}`,
      title: String(item.meta?.title ?? item.name ?? item.path)
    }))
)

const activeMenu = computed(() => {
  return String(route.meta?.activeMenu ?? route.path)
})

const currentTitle = computed(() => String(route.meta?.title ?? '日志数据标注平台'))

const currentSubtitle = computed(() => {
  if (route.name === 'dashboard') {
    return '总览导入、切片、窗口与标注工作进度'
  }
  if (route.path.startsWith('/packages')) {
    return '管理原始压缩包、导入状态和数据包详情'
  }
  if (route.path.startsWith('/slicing')) {
    return '基于 source_log_lines 驱动切片任务和窗口浏览'
  }
  if (route.path.startsWith('/annotation')) {
    return '围绕单窗口唯一标注模型完成连续标注'
  }
  return '按 Phase 6 统一前端信息架构与交互风格'
})
</script>

<style scoped>
.app-layout {
  min-height: 100vh;
}

.layout-aside {
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
}

.layout-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 72px;
  padding: 0 20px;
  color: #1e293b;
  border-bottom: 1px solid #e2e8f0;
}

.layout-logo__mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  font-weight: 700;
  background: linear-gradient(135deg, #60a5fa, #38bdf8);
  color: #0f172a;
}

.layout-logo__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.layout-logo__body strong {
  font-size: 15px;
  line-height: 1.2;
}

.layout-logo__body span {
  font-size: 12px;
  color: #94a3b8;
}

.layout-menu {
  height: 100%;
  border-right: none;
}

.layout-menu :deep(.el-menu-item) {
  border-left: 3px solid transparent;
}

.layout-menu :deep(.el-menu-item:hover) {
  background-color: #f1f5f9;
}

.layout-menu :deep(.el-menu-item.is-active) {
  background-color: #eff6ff;
  border-left-color: #2563eb;
  font-weight: 600;
}

.layout-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 72px;
  padding: 0 28px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
}

.layout-header__title {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.layout-header__subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}

.layout-main {
  padding: 24px 28px;
  background: #f8fafc;
}

@media (max-width: 960px) {
  .layout-aside {
    width: 88px !important;
  }

  .layout-logo {
    justify-content: center;
    padding: 0;
  }

  .layout-logo__body {
    display: none;
  }

  .layout-header {
    padding: 0 20px;
  }

  .layout-main {
    padding: 20px;
  }
}
</style>
