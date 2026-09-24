<template>
  <el-container class="app-layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon size="22"><Monitor /></el-icon>
        <span>故障定位系统</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#ffffff"
        text-color="#475569"
        active-text-color="#2563eb"
        :collapse-transition="false"
      >
        <template v-for="item in menuItems" :key="item.path">
          <!-- 有子菜单的情况 -->
          <el-sub-menu v-if="item.children && item.children.length > 0" :index="item.path">
            <template #title>
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </template>
            <el-menu-item
              v-for="child in item.children"
              :key="child.path"
              :index="`/${item.path}/${child.path}`"
            >
              <el-icon><component :is="child.icon" /></el-icon>
              <span>{{ child.title }}</span>
            </el-menu-item>
          </el-sub-menu>
          <!-- 没有子菜单的情况 -->
          <el-menu-item v-else :index="`/${item.path}`">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header v-if="viewMode.showProjectHeader" class="header">
        <span class="page-title">{{ currentTitle }}</span>
      </el-header>
      <el-main class="main" :style="{ height: viewMode.mainHeight }">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getWujieViewMode } from '../../utils/wujieViewRuntime.mjs'

const route = useRoute()
const router = useRouter()
const viewMode = getWujieViewMode()

const activeMenu = computed(() => {
  // 优先读 mainActiveMenu（标注内页等嵌入子 section 用它固定高亮主菜单项），再退化为 activeMenu / 当前路径
  return route.meta?.mainActiveMenu || route.meta?.activeMenu || route.path
})
const currentTitle = computed(() => route.meta?.title || '')

// 从路由配置中生成菜单数据
const menuItems = computed(() => {
  return router.options.routes[0].children
    .filter(route => !route.meta?.hidden)
    .map(route => {
      const menuItem = {
        path: route.path,
        title: route.meta.title,
        icon: route.meta.icon,
      }

      // 如果有子路由，添加子菜单
      if (route.children && route.children.length > 0) {
        menuItem.children = route.children
          .filter(childRoute => !childRoute.meta?.hidden)
          .map(childRoute => ({
            path: childRoute.path,
            title: childRoute.meta.title,
            icon: childRoute.meta.icon,
          }))
      }

      return menuItem
    })
})
</script>

<style scoped>
.app-layout {
  height: 100%;
}
.app-layout > .el-container {
  min-width: 0;
  min-height: 0;
}
.aside {
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  color: #1e293b;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid #e2e8f0;
}
.logo .el-icon {
  color: #2563eb;
}
.el-menu {
  border-right: none;
  flex: 1;
}
/* 白底主题下的悬停 / 选中高亮 */
.el-menu :deep(.el-menu-item:hover),
.el-menu :deep(.el-sub-menu__title:hover) {
  background-color: #f1f5f9;
}
.el-menu :deep(.el-menu-item.is-active) {
  background-color: #eff6ff;
  border-left: 3px solid #2563eb;
}
.header {
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  padding: 0 24px;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: #1e293b;
}
.main {
  background: #f8fafc;
  min-width: 0;
  min-height: 0;
  padding: 24px;
  overflow-y: auto;
}
</style>
