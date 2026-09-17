# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

数据库驱动的日志数据标注平台 (database-driven log-annotation platform). A user uploads a
compressed log archive; the platform imports the raw logs into the database, slices them into
time windows, lets a user browse and annotate each window, and exports the annotations. The
defining design constraint: **the database is the single source of truth for queries** — disk
holds only the original archive; everything else (raw lines, slices, windows, annotations) lives
in Postgres. README.md (in Chinese) is the authoritative spec.

## Commands

Backend (run from `backend/`):
```bash
pip install -e '.[dev]'         # dev extras add pytest/httpx/requests
python -m pytest -q             # all tests          (or `make backend-test` from root)
python -m pytest tests/test_health.py::test_health -q   # single test
alembic upgrade head            # apply migrations (head: see README "当前 head revision")
uvicorn app.main:app --reload   # serve on :8000, OpenAPI at /docs
```

Frontend (run from `frontend/`):
```bash
npm ci
npm run dev          # vite dev server on :5173
npm test             # vitest run (specs are src/**/*.spec.ts)
npm run build        # vue-tsc --noEmit THEN vite build  (or `make frontend-build`)
npm run type-check   # vue-tsc only
```

Docker / deploy (from root):
```bash
cp -n deploy/.env.example deploy/.env
bash scripts/init_storage.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
make docker-build / make compose-config    # wrappers that auto-create deploy/.env
```

## The data pipeline (read this before touching backend logic)

One linear chain of tables, each stage materialized into the next. Models live in
`backend/app/db/models/`, one file per table:

```
dataset_packages → import_tasks → source_log_files → source_log_lines
   → slice_tasks → slice_windows → slice_window_lines → annotations
```

- **Import** (`services/import_worker.py`): extracts the archive to a temp dir, walks
  `data/` recursively, parses each line into `(timestamp, content)`, and bulk-inserts
  `source_log_files` + `source_log_lines`. Directory layout maps to columns:
  `parts[0]` → `cpu_name`, middle dirs → `module_path`, leaf → `filename`. The temp extraction
  dir is always `rmtree`'d in `finally`; the original archive stays on disk.
- **Timestamp parsing** is best-effort (`_parse_source_line`): tries epoch float, then ISO,
  then several `strftime` formats; naive datetimes get `import_default_timezone`
  (Asia/Shanghai) and are stored as **UTC epoch floats**. A line with no parseable timestamp
  has `timestamp=None` and is **excluded from slicing**.
- **Slicing** (`services/slice_engine/`): `windowing.aggregate_source_lines_by_window` buckets
  lines by `floor(ts / window_seconds) * window_seconds`. `window_seconds` is a free-form
  duration in seconds, range-validated to `[1, 3600]` (default 300). `writer.materialize_windows`
  writes `slice_windows` + `slice_window_lines`. Re-running a slice task first clears its prior
  windows.
- **Annotations**: a window may carry **multiple** annotations — at most one whole-window
  row (`source_log_file_id IS NULL`) plus at most one per bound source file. Uniqueness is
  enforced by two *partial* unique indexes (`uq_annotations_window_whole` /
  `uq_annotations_window_file`), each declared with both `postgresql_where` and `sqlite_where`
  so `create_all` (tests) and Alembic (Postgres) agree. `POST .../annotation` upserts on the
  `(slice_window_id, source_log_file_id)` key. Latest state only — no history/versioning.
- **Window subdivision**: a leaf window can be subdivided into finer sub-windows
  (`SliceWindowService.subdivide`); children carry `parent_window_id` and the parent is flagged
  `has_children=True`. The original window and its annotations are preserved. Counting is
  **leaf-scoped** (`has_children == False`) everywhere it matters: dashboard summary, pending
  list, navigation prev/next, and the package "有异常" badge. Re-subdivision is refused if any
  child is already annotated. The projection writer (`writer.py`) must NOT `rmtree` the whole
  task dir on the subdivision path — only `materialize_windows(..., wipe_task_dir=False)`.

## Backend conventions

- **Layering**: `api/v1/<resource>.py` routes are thin — they construct a `*Service` (or the
  slice engine) and translate to/from Pydantic schemas. Business logic lives in `services/`.
  Routes get a `Session` via `Depends(get_db)` (`api/v1/deps.py`); services take that session
  in `__init__` and own commits.
- **Async work runs on a daemon `threading.Thread`, not FastAPI BackgroundTasks.** See
  `ImportService.start_import_task_async`: it opens a *new* session bound to the same engine
  inside the thread. Slicing has the analogous pattern. Two consequences:
  (1) **for in-memory SQLite the async path is skipped** — a per-connection in-memory DB isn't
  visible to another thread, so the dispatcher returns early and tests run the worker
  synchronously instead; (2) status is tracked by the `import_tasks` / `slice_tasks` rows
  (`pending → running → completed/failed`), which the frontend polls.
- **Config** is a single cached `Settings` (`core/config.py`, `get_settings()` is
  `lru_cache`'d). `DATABASE_URL` accepts aliases `DB_URL`/`db_url`; default is local
  `sqlite:///./app.db`. `storage_root` derives `packages_dir`/`extracted_dir`/`slices_dir`/
  `annotations_dir`. Tests mutate this singleton's attributes in place (see `phase2_settings`),
  so changing config shape can ripple into fixtures.
- **Tests** (`backend/tests/`) build a fresh in-memory SQLite via
  `Base.metadata.create_all` — they do **not** run Alembic. So a new model must be imported into
  `app/db/base.py`'s metadata (via `db/models/__init__.py`) *and* given a migration in
  `alembic/versions/` for it to exist in both test and Postgres worlds.
- Errors: domain failures raise `AppError` (`core/errors.py`); handlers there normalize all
  responses to `{error_code, message, detail}`.

## Frontend conventions

Vue 3 + TS + Pinia + Vue Router + Element Plus. Per-domain triads keep parallel names:
`api/<domain>.ts` (axios calls), `stores/<domain>Store.ts` (Pinia), `types/<domain>.ts`,
and `views/<domain>/`. `@` aliases `src/`.

- All HTTP goes through `src/api/http.ts` (axios instance). Base URL is `VITE_API_BASE_URL`
  (falls back to `/api`); in Docker it's set to the backend's `/api/v1`.
- **Navigation is fixed to four top-level pages** (首页 / 数据包管理 / 数据切片 / 数据标注);
  everything else is a `meta.hidden` route with `meta.activeMenu` pointing back to its parent
  (`router/index.ts`). Don't add top-level nav items without checking the README's 页面说明.
- Tests are colocated `__tests__/*.spec.ts` using `@vue/test-utils` + jsdom; Element Plus
  components are stubbed via `src/test-utils/elementStubs.ts`.

## Gotchas

- `npm run build` fails on any type error (`vue-tsc --noEmit` runs first) — keep types green.
- All stored timestamps are UTC epoch **floats**, not datetimes. Window math depends on this.
- Deleting a slice task removes its windows but preserves `source_log_*`; deleting a package
  cascades and also removes the on-disk archive. Verified end-to-end (README Phase7).

## LLM-assisted features (OpenAI-compatible)

`services/llm/` holds a provider-agnostic `LLMClient` (`get_llm_client()` reads `LLM_BASE_URL`/
`LLM_API_KEY`/`LLM_MODEL` from `Settings`). `settings.llm_enabled` gates everything; when unset
all three features **degrade silently** (no errors). Tests inject `tests/fakes.FakeLLMClient` —
never hit the network. Design doc: `docs/design_llm_features.md`.

- **Import dir parsing** no longer requires `cpuN` names: `archive_service.find_data_root` finds
  the root by name marker (`import_data_root_markers`, matches `data`/`data_*`) then peels
  single-child wrappers. Mapping is still parts[0]=cpu, parts[1:-1]=module (multi-level), leaf=file.
- **Adaptive timestamps** (`services/llm/timestamp_inference.py` + `import_worker`): samples per
  cpu, LLM infers a `TimestampFormatSpec` (locally re-validated), converges to a package-wide spec
  after 3 matching cpus, applies deterministically, falls back to the rule parser per line.
- **Fault recommendation** (`recommendation_service.py`, table `annotation_recommendations`):
  on-demand + batch (batch via daemon thread, sync for in-memory SQLite like import). Pure
  suggestion cache — never auto-writes `annotations`. **Two-step**: pass 1 enumerates the
  allowed `fault_types` and snaps the LLM's anomaly_type to that set; if the LLM returns a name
  that doesn't match, pass 2 asks once whether this is a genuinely new fault type and persists
  it to `fault_type_suggestions` (status=`pending`). The recommendation succeeds with
  `recommended_anomaly_type=None` when a suggestion is recorded; reason text is appended with
  `[已生成新故障类型建议: <name> ...]`. An unmatched name without a confirmed suggestion still
  yields `status=failed`.
- **Fault type management** (`fault_type_service.py`, table `fault_types`): user-defined
  `name + description` dictionary that drives both annotation `anomaly_type` selection
  (`AnnotationService._normalize_anomaly_type` rejects values not in the table) and the
  recommendation hard constraint above. Frontend lives at `views/faultTypes/` and is reachable
  as a hidden subpage of 数据标注 (`name: 'fault-type-manage'`).
- **Fault type suggestions** (`fault_type_suggestion_service.py`, table `fault_type_suggestions`):
  pending queue of LLM-proposed new fault types. Routes at `/fault-type-suggestions` (list /
  detail / accept / reject / delete). Accepting calls `FaultTypeService.create()` and links
  back via `accepted_fault_type_id`. Frontend at `views/faultTypes/FaultTypeSuggestionListView.vue`,
  hidden route `name: 'fault-type-suggestion-list'`. The fault-type management page shows the
  pending count as a badge; the workbench recommendation card surfaces a "去审核" button when
  `recommendation.pending_suggestion_id` is set.
- **Annotation records management**: 已标注记录区每行支持「编辑 / 删除 / 查看窗口」——前两者复用现
  有 `PATCH/DELETE /annotations/{id}`；「查看窗口」直接 `router.push` 到既有的
  `slice-window-browser` 路由（`/packages/:id/slice-tasks/:taskId/windows?window_id=...`）。
  待标注列表行内同样有「查看 / 删除」：删除走 `DELETE /slice-windows/{id}`（已带 annotation 的
  窗口返回 `WINDOW_HAS_ANNOTATION` 409）；列表上方有筛选条（min/max line_count / keyword /
  sort_by=window_start_ts|line_count），对应 service 的 `AnnotationQueryFilters` 三个 pending-only
  字段。
- Alembic head is now `20260905_0001` (plan 3 single-DB merge). The entire 17-migration chain
  was collapsed into **one** consolidated initial migration
  `alembic/versions/20260905_0001_merged_annotation_schema.py` (`down_revision=None`), which
  creates all 11 `ann_*` tables (`ann_dataset_packages` → `ann_import_tasks` →
  `ann_source_log_files` → `ann_source_log_lines` → `ann_slice_tasks` → `ann_slice_windows` →
  `ann_slice_window_lines` → `ann_annotations` → `ann_annotation_recommendations` →
  `ann_fault_type_suggestions` → `ann_window_analyses`) plus the `ann_fault_type_suggestions`
  FK `accepted_fault_type_id → fault_types(id) ON DELETE SET NULL`. It does **not** create
  `fault_types` — that table is main-owned (see below). The version table is isolated to
  `alembic_version_annotate` (`env.py` `version_table`), so it never collides with the main
  system's `alembic_version`. Note: the old migration `0007` used batch ops that fail under
  SQLite; the consolidated chain is validated on Postgres (the deploy target).
- **`fault_types` is main-owned, not annotation-owned.** It lives in the same `fault_diagnosis`
  database but is built + seeded by the main system (`create_tables.py` / `seed.py`). The
  annotation `FaultType` model (`db/models/fault_type.py`) is a read-side mapping to that shared
  table — it must match the main system's superset (nullable `description`, `color_tag`,
  unique `name`). When both subsystems run in the merged stack, start order matters:
  `backend` (healthy, runs `create_all` + seed) must finish before `backend-annotate` runs
  `alembic upgrade head`, or the FK to `fault_types` cannot be created.
- **AI 多错误分析** (`recommendation_service.analyze_window_multi`, route
  `POST /slice-windows/{id}/recommendation/analyze`): on-demand, **never persists** —按文件分组
  取日志喂 LLM，返回 `{multiple_faults, suggest_subdivide, suggested_window_seconds, per_file:
  [{source_log_file_id, label, anomaly_type, reason}]}`，anomaly_type 吸附到已定义故障类型(未匹配
  降级为 None)。`suggested_window_seconds` 是建议的子窗口长度(秒)，由 `_validate_suggested_seconds`
  本地校验：必须是正整数、严格小于窗口跨度、落在 `[MIN_WINDOW_SECONDS, MAX_WINDOW_SECONDS]`，否则
  降级为 None；`suggest_subdivide=false` 时恒为 None。前端工作台推荐卡有「智能分析(多错误)」按钮，
  卡片显示建议子窗口秒数，可逐文件「采纳为该文件标注」(走文件级标注)或「去细分该窗口」(把建议秒数经
  `query.suggest_seconds` 带到浏览页预填细分对话框并自动打开)。**所有 per-file 选项都采纳后**该窗口
  即视为标注完成，前端重载待标注列表把它移除(逐次采纳期间刻意不重载，否则窗口会中途消失)。
- **单错误推荐采纳**：`requestWindowRecommendation` 的「采纳到表单」会把 `recommendation.reason`
  一并填入备注(`form.note`)，随标注保存。
- **LLM 接口前端超时**：`api/http.ts` 全局 axios `timeout=10s` 对 LLM 调用太短；
  `api/recommendations.ts` 给 `requestWindowRecommendation` / `analyzeWindowMulti` 单独设
  `timeout=180000`(`LLM_REQUEST_TIMEOUT_MS`)，需 ≥ 后端 `LLM_TIMEOUT_SECONDS`。
- **窗口浏览导航**：四处「浏览/查看窗口」入口改为同页 `router.push`(带 `query.from`)，
  `SliceWindowBrowserView.goBack` 按 `from`(slicing/annotation/annotation-list) 返回来源页；
  浏览页展示该窗口的多条标注，并提供「细分窗口」对话框。
- **日志分页 (Load More)**：游标分页 `GET /slice-windows/{id}/logs`(cursor=`(timestamp,id)` 复合，
  `has_more=len(rows)>limit`，每页 200)已验证正确；前端按 `store.hasMore` 渲染——有更多时显示可点
  的「加载更多」按钮(带 loading)，无更多时显示「已显示全部 N 行」灰字提示，不再用易误解的禁用按钮。
- **代理隔离 (docker)**：Docker Desktop 会把 Windows 系统代理(如 `127.0.0.1:7892`)注入容器，
  导致容器内 LLM 调用走死代理而超时(容器内 `127.0.0.1` 指容器自身)。`deploy/docker-compose.yml`
  backend 服务把 `HTTP(S)_PROXY/ALL_PROXY` 默认置空、`NO_PROXY=*`(均可经 .env 覆盖)，强制直连。
