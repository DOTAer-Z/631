# 交接文档：wujie 标注仪表盘崩溃修复 + 继续开发

> 写给**下一个 session**。目标有二:
> 1. **验证**本次已完成的「wujie 微前端下数据标注仪表盘崩溃」修复;
> 2. 在此基础上继续**改其它功能**。
>
> 本文件只讲这次修复 + 你接手要遵守的硬约束。要新增/改功能的完整分层地图,配合读同目录
> `HANDOFF-NEW-DEV.md`(目录地图 / 数据模型 / 路由分流 / 跨系统桥接 / 环境变量全清单)。

---

## 0. TL;DR(30 秒版)

- **现象**:真实 K8s 集群、wujie 框架下打开「数据标注」,F12 报
  `TypeError: cannot read properties of undefined (reading 'length')`,切片任务/窗口全显示
  undefined/undefined。而直接开 `:30080` 那个窗口上传+切片是正常的。
- **根因**:标注子应用在 wujie 下跑在**宿主(门户)窗口**里,它算 API base 时只读
  `window.location.href` = 门户 origin `172.30.6.59:30082`,那个 origin 上没有
  `/annotate-api/` 反代 → 请求 404/非 JSON → `data.items` 为 `undefined` → store 存了
  undefined → 视图 `.length` 崩。主系统没这问题,因为它的 `getAppOrigin` 本来就 wujie-aware。
- **修复**:让标注的 `getAnnotationAppOrigin()` 与主系统 `platformRuntime.mjs getAppOrigin`
  **读取顺序完全一致**(wujie 优先 `$wujie` / `__APP_CONFIG__.appOrigin`),再加一层
  仪表盘 `data?.items ?? []` 防御兜底。
- **状态**:代码已改完,`vue-tsc --noEmit` 类型检查**已通过**。**尚未**重建镜像、尚未在
  wujie 环境实机验证 —— 这就是你要做的第一件事。

---

## 1. 改了哪两个文件(本次修复的全部改动)

都在 `631_9.8/`,**没有**碰生产 `631/`。

### 1.1 真正的修复 — `annotation-app/frontend/src/platform/portalContext.ts`

`getAnnotationAppOrigin()`(约 273–306 行)重写为 wujie-aware。新增 `WujieWindowLike` 接口,
读取顺序镜像主系统 `main-system/frontend/src/utils/platformRuntime.mjs` 的 `getAppOrigin`:

```ts
interface WujieWindowLike {
  readonly location?: { readonly href?: unknown }
  readonly __POWERED_BY_WUJIE__?: unknown
  readonly $wujie?: { readonly location?: { readonly href?: unknown } }
  readonly __APP_CONFIG__?: { readonly appOrigin?: unknown }
}

export function getAnnotationAppOrigin(windowRef?: Pick<WindowReference, 'location'>): string {
  const win = (windowRef ?? globalThis.window) as WujieWindowLike | undefined
  const isWujie = Boolean(win?.__POWERED_BY_WUJIE__ || win?.$wujie)
  const readers: Array<() => unknown> = isWujie
    ? [
        () => win?.$wujie?.location?.href,      // wujie: 集成入口 origin (172.30.6.63:30080)
        () => win?.__APP_CONFIG__?.appOrigin,
        () => win?.location?.href,               // 兜底
      ]
    : [
        () => win?.location?.href,               // 非 wujie: 保持原行为
        () => win?.__APP_CONFIG__?.appOrigin,
      ]
  for (const read of readers) {
    let value: unknown
    try { value = read() } catch { continue }
    const origin = safeHttpOrigin(value)   // 已存在的 http/https origin 校验
    if (origin) return origin
  }
  throw new PortalContextOriginError()       // 已存在的错误类型
}
```

**为什么改这里**:`api/http.ts` 在**模块加载期**用 `getAnnotationAppOrigin()` 算 axios
`baseURL`(约第 6 行),`resolveApiUrl(path)` 也用它。两条 API 路径都走这个函数,所以在这一层
改,wujie 下两条路径都会正确解析到集成入口。`safeHttpOrigin` / `PortalContextOriginError` /
`WindowReference` 都是文件里已有的,没新增依赖。

### 1.2 防御兜底 — `annotation-app/frontend/src/stores/dashboardStore.ts`

三个 fetch 函数把 `X.value = data.items` 改成 `X.value = data?.items ?? []`
(`fetchRecentPackages` / `fetchRecentSliceTasks` / `fetchRecentAnnotations`)。
即使将来接口再返回异常结构,也不会把整个仪表盘打崩。

---

## 2. 怎么验证这次修复(你的第一优先级)

### 2.1 类型闸门(已过,重跑确认)

标注前端 `npm run build` 前置 `vue-tsc --noEmit`,**任何类型错误直接 build 失败**。

```bash
cd /home/junjiezuo/631-fault/631_9.8/annotation-app/frontend
npm run type-check          # 应无输出、退出码 0(已验证通过)
```

### 2.2 本地重建主前端镜像(用 jj 测试栈,别碰生产)

修复在**主前端镜像**里 —— 深融合下标注源码经 vite `@annotation` 别名被主前端一起编译,
所以要重建的是 `frontend-main`,不是 `frontend-annotate`。

```bash
cd /home/junjiezuo/631-fault/631_9.8
# 必须同时叠加 override,否则会覆盖生产栈(同名镜像/端口/项目名)
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml build frontend-main
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml up -d frontend-main
```

本地 jj 栈是**非 wujie** 直连(主系统当宿主),走 `else` 分支 `window.location.href` 保持原行为,
所以本地主要验证「没改坏原路径」:开 `http://localhost:8081/#/data-governance/annotation`,
标注仪表盘能正常出数、切片/窗口不再 undefined 即可。

### 2.3 wujie 实机验证(真正证明修复生效)

在真实集群、wujie 门户里打开「数据标注」,F12 → Network:

- **修复前**:`/annotate-api/v1/dashboard/recent-annotations` 请求域名是门户
  `172.30.6.59:30082`(错) ❌
- **修复后**:该请求域名应变成集成入口 `172.30.6.63:30080`(对) ✅,与「系统概览」
  `/api/v1/overview` 同域;切片任务/窗口正常显示,不再 undefined/undefined。

> 上线到 K8s:重建 `faultdiag-frontend` 镜像 → 打 tar 分片(见第 3 节约束)→ load 到目标机 →
> `kubectl rollout restart deploy/faultdiag-frontend`。具体流程见 `631_9.8depoly` 部署手册。

### 2.4 万一 wujie 下还是打到门户 origin(排查方向)

修复依赖 wujie 宿主在挂载标注 bundle **之前**已把 `window.$wujie` 或
`window.__APP_CONFIG__.appOrigin` 注入。若实机仍不对,按顺序查:

1. 在门户里 `console.log(window.$wujie, window.__APP_CONFIG__)` —— 确认这俩到底有没有、
   `appOrigin` 是不是集成入口 `172.30.6.63:30080`。
2. 若两者都缺 → wujie 注入时机/配置问题,需门户侧显式传 `__APP_CONFIG__.appOrigin`。
3. `http.ts` 是**模块加载期**定 baseURL 的;若 bundle 早于宿主注入就 import 了,时机会错。
   届时可考虑把 baseURL 改成惰性求值(每次请求前算),但**先确认第 1 步再动**,别提前改。

---

## 3. 硬约束(接手必须一字不差遵守)

| 约束 | 细则 |
|---|---|
| **只改 `631_9.8/`** | 绝不碰生产 `631/` 栈 |
| **本地 docker 必须叠 override** | 所有 build/up/down 命令都要 `-f docker-compose.yml -f docker-compose.override-jj.yml`(jj-test 栈:主前端 :8081 / 主后端 :5001),绝不用生产 `faultdiag-integrated`(:8080/:5000) |
| **`.env` 是真密钥** | `631_9.8/.env` 含真实 `LLM_API_KEY`/`LLM_BASE_URL`/`MODEL_API_ENCRYPTION_KEY`/`INTERNAL_LLM_GATEWAY_TOKEN`/`POSTGRES_PASSWORD`。**绝不提交/上传 GitHub、绝不打进交付物**;交付只给 `.env.example` 占位符 |
| **镜像 tar 必须分片** | GitHub 100MB 上限,镜像 tarball 用 `.part` 分片,排除大整包 |
| **临时文件目录** | 用 `$CLAUDE_JOB_DIR/tmp`,不要写 `/tmp` |
| **类型必须绿** | 改标注前端后跑 `vue-tsc --noEmit`;build 前置它,红了直接失败 |

---

## 4. 继续改其它功能:最小上手地图

> 完整版看 `HANDOFF-NEW-DEV.md`。这里只给最常踩的三条。

- **改标注前端** → 编辑 `annotation-app/frontend/...`,但**重建 `frontend-main`**
  (@annotation 别名把标注源码编进主 bundle);`frontend-annotate` 镜像不承载融合 UI。
- **改标注后端** → `annotation-app/backend/...`,重建 `backend-annotate`。新表要 `ann_` 前缀,
  且**模型 + Alembic 迁移都要有**(测试用 SQLite `create_all` 不跑 Alembic,两者须一致;
  迁移 `down_revision = "20260905_0001"`)。
- **改主系统** → `main-system/backend` 或 `main-system/frontend`,重建 `backend` / `frontend-main`。

**几条会咬人的事实**(详见 `HANDOFF-NEW-DEV.md` 第 3、6 节):

- 单库 `fault_diagnosis`:主系统表 + 标注 `ann_*` 表共存;两侧 Alembic 版本表分开
  (`alembic_version` vs `alembic_version_annotate`)。
- `fault_types` **主系统所有**,标注只读 + FK 引用;启动顺序 `backend`(healthy,建好
  `fault_types`)必须早于 `backend-annotate`(跑迁移建 FK)。
- 所有时间戳存 **UTC epoch float(秒)**,不是 datetime。
- 标注后端**不直连 LLM**,走主系统内部网关(`LLM_GATEWAY_URL` + 两端一致的
  `INTERNAL_LLM_GATEWAY_TOKEN`);LLM 主系统优先读 DB `model_api_configs` 的 active 行
  (max_tokens 陷阱在那行,不是 env)。
- 标注 LLM 类接口前端要单独设 `timeout=180000`,别用全局 axios 10s。

---

## 5. 相关文件速查

| 关注点 | 文件 |
|---|---|
| 本次修复(真正) | `annotation-app/frontend/src/platform/portalContext.ts` `getAnnotationAppOrigin` |
| 本次修复(兜底) | `annotation-app/frontend/src/stores/dashboardStore.ts` 三个 fetch |
| 消费方(baseURL 在模块加载期算) | `annotation-app/frontend/src/api/http.ts` |
| 修复的「参照标准」(主系统 wujie-aware 原型) | `main-system/frontend/src/utils/platformRuntime.mjs` `getAppOrigin` / `platform.js` 再导出 |
| 崩溃点 | 主前端 bundle 里的 DashboardView(`recent*.length`) |
| 全量开发地图 | `631_9.8/HANDOFF-NEW-DEV.md` |
| 子系统架构 | `main-system/CLAUDE.md`、`annotation-app/CLAUDE.md` |
