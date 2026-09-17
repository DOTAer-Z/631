import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'

const layoutPath = new URL('../src/components/Layout/AppLayout.vue', import.meta.url)
const routeShellPath = new URL('../src/views/LogAnalysis/index.vue', import.meta.url)
const annotationShellPath = new URL('../src/views/DataProcessing/AnnotationSectionShell.vue', import.meta.url)
const routerPath = new URL('../src/router/index.js', import.meta.url)
const mainPath = new URL('../src/main.js', import.meta.url)

const [layoutSource, routeShellSource, annotationShellSource, routerSource, mainSource] = await Promise.all([
  readFile(layoutPath, 'utf8'),
  readFile(routeShellPath, 'utf8'),
  readFile(annotationShellPath, 'utf8'),
  readFile(routerPath, 'utf8'),
  readFile(mainPath, 'utf8'),
])

function descriptor(source, filename) {
  const result = parse(source, { filename })
  assert.deepEqual(result.errors, [])
  return result.descriptor
}

function elements(root, tag) {
  const matches = []
  const visit = node => {
    if (!node || typeof node !== 'object') return
    if (node.tag === tag) matches.push(node)
    if (Array.isArray(node.children)) node.children.forEach(visit)
  }
  visit(root)
  return matches
}

function hasStaticAttribute(node, name, value) {
  return node.props.some(prop => (
    prop.type === 6
    && prop.name === name
    && (value === undefined || prop.value?.content === value)
  ))
}

function hasDirective(node, name, expression) {
  return node.props.some(prop => (
    prop.type === 7
    && prop.name === name
    && (expression === undefined || prop.exp?.content === expression)
  ))
}

const layout = descriptor(layoutSource, 'AppLayout.vue')
const routeShell = descriptor(routeShellSource, 'LogAnalysis/index.vue')
const annotationShell = descriptor(annotationShellSource, 'AnnotationSectionShell.vue')

test('compiled SFC structure hides only the project Header and preserves the left menu wiring', () => {
  const [header] = elements(layout.template.ast, 'el-header')
  assert.ok(header)
  assert.equal(hasDirective(header, 'if', 'viewMode.showProjectHeader'), true)
  assert.equal(elements(layout.template.ast, 'el-aside').length, 1)
  assert.equal(elements(layout.template.ast, 'el-menu').length, 1)
  assert.match(layout.scriptSetup.content, /getWujieViewMode/)
})

test('route shell and annotation section preserve a definite non-intrinsic height chain (no iframe)', () => {
  // 深融合：标注不再用 iframe。路由壳与标注 section 都必须是可伸缩高度链，保证不出现内部滚动条错位。
  const [shellDiv] = elements(routeShell.template.ast, 'div')

  assert.equal(hasStaticAttribute(shellDiv, 'class', 'route-shell'), true)
  assert.match(routeShell.styles[0].content, /\.route-shell\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0/s)

  // 标注 section 自身也是 height:100% / min-height:0，且内部用 <router-view/> 而非 iframe
  const [sectionDiv] = elements(annotationShell.template.ast, 'div')
  const [routerView] = elements(annotationShell.template.ast, 'router-view')
  assert.equal(hasStaticAttribute(sectionDiv, 'class', 'annotation-section'), true)
  assert.ok(routerView, 'annotation section must render annotation routes via <router-view/>')
  assert.equal(elements(annotationShell.template.ast, 'iframe').length, 0, 'deep fusion must not use an iframe')
  assert.match(annotationShell.styles[0].content, /\.annotation-section\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0/s)

  // 主布局 el-main 仍按 viewMode.mainHeight 显式设置高度，保证整体高度链闭合
  assert.match(layout.template.content, /<el-main[^>]*:style="\{\s*height:\s*viewMode\.mainHeight\s*\}"/s)
})

test('annotation routes are mounted as real child routes (no iframe src, no wujie basePath)', () => {
  // 深融合：主系统 router 把 annotationRoutes 挂为 data-governance 下 annotation 的 children，
  // 用 AnnotationSectionShell 包一层；不再有 getAppUrl('/annotate/') 或 blob/basePath 取值。
  assert.match(routerSource, /import\s*\{[^}]*annotationRoutes[^}]*\}\s*from\s*['"]@annotation\/router['"]/s)
  assert.match(routerSource, /AnnotationSectionShell\.vue/)
  assert.match(routerSource, /children:\s*annotationRoutes/)
  assert.doesNotMatch(routerSource, /getAppUrl\(\s*['"]\/annotate\/['"]\s*\)/)
  assert.doesNotMatch(routerSource, /window\.\$wujie\?\.props\?\.basePath/)

  // 标注内页不再使用 URL/iframe 上下文，避免 token 等登录态出现在 URL
  assert.doesNotMatch(annotationShellSource, /[?&#](?:token|Token|userInfo|namespaceId)=/)
})

test('main provides runtime cleanup and injects portal context into the annotation subsystem', () => {
  assert.match(mainSource, /import\s*\{[^}]*APP_CLEANUP_KEY[^}]*\}\s*from\s*['"]\.\/utils\/wujieLifecycle\.mjs['"]/s)
  assert.match(mainSource, /function\s+createMyApp\(cleanup\)/)
  assert.match(mainSource, /app\.provide\(APP_CLEANUP_KEY,\s*cleanup\)/)
  // 深融合登录态注入：主系统在应用创建时把自身平台上下文写入标注 portalContext，
  // 标注 http.ts 的请求拦截器据此读 token 注入 X-Token。
  assert.match(mainSource, /import\s*\{[^}]*setPortalContext[^}]*\}\s*from\s*['"]@annotation\/platform\/portalContext['"]/s)
  assert.match(mainSource, /function\s+injectAnnotationContext\(\)/)
  assert.match(mainSource, /setPortalContext\(\s*\{/)
  assert.match(mainSource, /getPlatformContext\(\)/)
  // 标注 section 的页内导航完全基于 annotationRoutes 构建（不再有独立侧边栏/布局）
  assert.match(annotationShell.scriptSetup.content, /import\s*\{[^}]*annotationRoutes[^}]*\}\s*from\s*['"]@annotation\/router['"]/s)
})

test('Task 3 sources never persist, log, or URL-encode portal context', () => {
  const combined = `${layoutSource}\n${routeShellSource}\n${annotationShellSource}\n${routerSource}\n${mainSource}`
  assert.doesNotMatch(combined, /localStorage|sessionStorage|document\.cookie/)
  assert.doesNotMatch(combined, /console\.(?:log|info|debug|warn|error)/)
  assert.doesNotMatch(combined, /URLSearchParams|encodeURIComponent/)
})
