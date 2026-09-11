# HANDOFF — 631(625) 集成版改造进度

> 约束：**只改 `631(625)/`**，不动 `源码-6.24`（上游参考）与 `631/`（旧集成）。
> 所有大模型相关功能必须**优雅降级**（未配 LLM 时不报错、回退规则）。
> Docker 由用户用 `sudo` 运行并在 `:8080` 验证；助手无 docker 权限。
> 最近更新：2026-07-02（第九轮：左侧导航 IA + 数据流重构 第一阶段）。

本文件记录历轮改造的全部改动、验证结果与下一步。新会话接手时先读本文件；最新进度见**第十二节（第九轮 IA 重构）**，其余见第十节（第八轮）、第十一节（部署包）。

---

## 一、需求与现状脉络（为什么这么改）

- **日志解析**核心原是纯规则/关键词启发式，LLM 仅可选增强；中文/非英文关键词命中不到 → `fault_score=0`、置信度恒 0。已改为「有 LLM→大模型按 severity 主判；无 LLM→改进规则降级」。
- **故障诊断**相似度/置信度依赖 Chroma 向量库；库内无已向量化案例 → 检索为空 → 相似度 "-"。需配合「知识库→新增知识案例」录入案例才有相似度。
- **故障诊断（按 run_id）**调用方 `log_text` 为空，旧代码把空文本直接喂大模型 → "未提供日志内容，无法分析"、置信度 0、两轮 round 结果不一致、`is_fault` 写死 True 恒显示"故障"。已修复（见第三轮）。
- **软件状态预测/分级预警**两页原为假数据；已接真实大模型分级。
- **多源接入**原含「基础监控数据接入/数据标准化」，已删；标签映射缺 `warning/info` 导致"未找到有效数据"，已修。
- **标注子系统侧边栏**深色 → 已改主系统浅色系。

---

## 二、改动文件清单（全部在 `631(625)/`）

### 第一轮（6 项需求）

后端 `main-system/backend/`：
- `app/services/log_analysis_service.py` — FaultScorer 补中文关键词；置信度加「级别占比基线」`max(评分/N, error率*0.6+warn率*0.2)`。
- `app/api/v1/log_analysis.py` — Step 8.6：LLM `severity` 映射覆盖 `has_fault/fault_score/confidence/summary`（与 LLM 自报置信度融合）。
- `app/api/v1/knowledge_base.py` — 新增 `POST /knowledge-cases`（结构化案例：fault_type_id/root_cause/solution/sample_log → 写 LogEntry + 向量化进**与诊断同一 Chroma 库**）。
- `app/models/diagnosis_record.py` — 增列 `root_cause / root_cause_type / recovery_hint`。
- `app/models/prediction_record.py` — 增列 `run_id`。
- `create_tables.py` — `_PATCH_COLUMNS` 增上述 4 列（幂等 ALTER，旧库自动补齐）。
- `app/services/fault_location_service.py` — fallback 用 LLM 置信度(≤0.6)+根因；`latest_record_out` 复读带根因字段。
- `app/services/llm_service.py` — `locate` fallback 提示词增 `confidence/root_cause`。
- `app/schemas/prediction.py` — `GradeRequest{run_id}`；`PredictionOut.run_id`。
- `app/services/prediction_service.py` — `predict(...,run_id=None)` 落库；新增 `grade_run(run_id, db)`。
- `app/api/v1/prediction.py` — 删 `POST /prediction/access/monitor`、`/standardization`；增 `POST /prediction/grade`。

前端 `main-system/frontend/src/`：
- `api/knowledgeBase.js`（`createKnowledgeCase`）、`views/KnowledgeBase/index.vue`（新增案例弹窗）。
- `views/Diagnosis/index.vue`（去文件上传，改「选已接入日志 run_id / 粘贴文本」，结果展示根因/推理路径，支持 `?run_id=` 自动诊断）、`api/diagnosis.js`。
- `api/prediction.js`（删假接口，增 `gradeRun`）、`views/Prediction/DataAccess.vue`（瘦身为日志接入）、`SoftwarePrediction.vue`（真实大模型分级+跳转诊断）、`LevelAlarm.vue`（真实预警记录）。

标注 `annotation-app/frontend/src/`：
- `layouts/AppLayout.vue` — 侧边栏深色→浅色（白底 `#ffffff`、字 `#475569`、选中 `#2563eb`+左条）。

### 第二轮（7 个反馈）

- `app/api/v1/log_analysis.py` — `list_logs` 的 `page_size` 上限 `le=100→le=500`（**修 422**）；Step 8.6 `_sev_map` 重标定到 10 分制（critical 9.5 / high 8.0 / medium 5.5 / low 2.0）；返回前 `final_fault_score=min(...,10.0)`。
- `app/api/v1/diagnosis.py` — `diagnosis_history` 的 `page_size` 改 `Query(20, ge=1, le=500)`，补 `Query` import。
- `app/services/log_analysis_service.py` — `fault_score=round(min(total,10.0),2)`；置信度除数 `12.0→10.0`；`_build_summary` 文案 `/12→/10`（**评分封顶 10**）。
- `app/services/llm_service.py` — `parse_log` 给每条 `parsed_events` 注入 1-based `id`（**解析详情加 ID**）。
- `app/services/prediction_service.py` — 标签映射补全 `fault/abnormal/warning/info/normal`；`scoreThreshold` 空值按 0；`log_analysis_results` 为空时回退读 `log_entries`（新增 `_extract_from_log_entries`，时间过滤在该回退路径跳过）；`_llm_polish` 改为**大模型可用时其 health_status 权威**（去掉 `_max_status` 规则封顶）。
- `app/services/fault_location_service.py` — 新增 `_llm_available()`；`_llm_summary` 在 LLM 可用时统一走完整推断；`_build_output` 在 LLM 可用且有 fault_type 时以**大模型 fault_type/confidence/root_cause 为权威**，FAISS `top_sim` 仍作 similarity_score 佐证。
- `app/schemas/diagnosis.py` — 核对：`DiagnosisOut` 已含 root_cause 等全部字段，**无需改**。
- 前端：`views/LogAnalysis/LogParse.vue`（关键事件表加 **ID 列**）、`views/Diagnosis/index.vue`（历史表补 关联日志/根因 列，`loadRuns`/`loadHistory` 解耦）、`views/Prediction/DataAccess.vue`（`scoreThreshold` 默认 0）。

### 第三轮（按 run_id 诊断的核心修复）

- `app/services/fault_location_service.py`：
  - `locate()` 新增 `effective_log_text` —— 调用方文本为空时用该 run 窗口文本重建（**修"未提供日志内容"/置信度 0/两轮不一致**）；`_llm_summary` 与 `_build_output` 改用 `effective_log_text`。
  - **故障预测前置门控**：诊断前调 `PredictionService.assess()`（不落库）；预测 green→返回**非故障**结果（`_build_no_fault_output`，is_fault=False/通道 none/写库可在历史查看），yellow/red 才进完整诊断；异常时降级"继续诊断"。
  - 新增 `_windows_to_text` / `_assess_fault` / `_build_no_fault_output`。
  - `is_fault` 真实化（门控拦下=正常，进入诊断=故障）。前端早已按 `is_fault` 渲染 故障/正常，无需改。
- `app/services/prediction_service.py` — 新增 `assess(log_text, metrics=None)`：与 predict 共用打分+大模型润色但**不写库**，供诊断门控复用。

---

## 三、验证结果

- 后端：每轮对改动 `.py` 跑 `python3 -m py_compile` 全部 **PASS**（含第三轮 `fault_location_service.py`、`prediction_service.py`）。
- 前端 main-system：第一、二轮 `npm run build` **exit 0**，改动视图均编译通过；产物 node_modules/dist 已清理。第三轮未改前端。
- 前端 annotation-app：第一轮 `npm run build`（含 `vue-tsc --noEmit`）**exit 0**。
- 仅做了静态/编译验证，**未做端到端运行验证**（无 docker 权限）。

---

## 四、重新部署（用户执行）

```bash
cd /home/junjiezuo/631-fault/'631(625)'
# 第三轮只改后端 → 最低限度只重建 backend：
sudo docker compose build backend
sudo docker compose up -d --force-recreate backend
# 若前端(主/标注)尚未随前两轮重建过，则一并：
# sudo docker compose build backend frontend-main frontend-annotate
# sudo docker compose up -d --force-recreate backend frontend-main frontend-annotate
```
重建后强刷浏览器 `Ctrl+Shift+R`。旧诊断记录是修复前生成的，需**重新诊断**才会看到正确结果。

---

## 五、下一步 / 待办 / 注意事项

1. **端到端验证**（用户在 :8080）：
   - 日志解析：配 LLM 时置信度随 severity 变化、评分 ≤10/10；未配 LLM 时有 ERROR/WARNING 也非 0；解析详情每条带 ID。
   - 多源接入：选时间段+任意标签（含"警告"）能成功接入。
   - 软件状态预测/分级预警：选 run 大模型分级出 一般/严重/紧急；严重/紧急可跳诊断。
   - 故障诊断：选已接入日志诊断 → 有真实根因/置信度；预测为一般的日志显示"正常"而非"故障"；同一 run 两轮结果一致；诊断历史可查看。
   - 标注子系统侧边栏为浅色。
2. **相似度仍为 "-"**：向量库需先经「知识库→新增知识案例」录入案例才有值；这是独立于置信度/根因的一条线。
3. **LLM 依赖**：门控/分级/解析主判/诊断根因要走大模型，需 `.env` 配 `ENABLE_LLM=True` + 有效 `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY`；否则按规则降级（精度较低）。用户当前正在编辑 `631(625)/.env`。
4. **已知取舍**：`_extract_from_log_entries` 回退路径**未做时间过滤**（log_entries 时间字段为 JSONB 标记 datetime，非已知 epoch 配置键，宁可不过滤也不误判为空）。如需精确时间过滤，需确认该集合的时间字段名后再补。
5. `_max_status` 在 prediction_service 中已**保留但不再使用**（无害）。
6. 数据库新增列由 `create_tables.py` 幂等 `ALTER` 自动补齐，旧库无需手动迁移。

---

## 六、第四轮（预测分级 → 诊断根因 链路收口）

> 用户确认两条目标逻辑均正确：① 软件状态预测对**融合多源数据整体判断**（当前单源=日志，其他源预留）→ 三级 一般/严重/紧急；一般不进诊断，严重/紧急才给诊断入口。② 故障诊断针对**分级后的等级**做根因、给"问题本质"，**置信度 ≥50%**。
> 两项决策：诊断入口**只能从预测页进入**；从预测页进入时**信任等级、跳过诊断内部二次门控**（省一次大模型调用，消除"同一 run 两轮结果不一致"）。

### 现状结论（无需重做）
- 开 LLM 时诊断确由大模型主导：门控 `_assess_fault` / 根因 `_llm_summary` / 权威决策 `_build_output` 三处都走 LLM，FAISS/KG 仅作相似度佐证 + 候选上下文。
- 预测页 `SoftwarePrediction.vue` 已做 green/yellow/red→一般/严重/紧急 映射且仅 严重/紧急 给诊断按钮；`LevelAlarm.vue` 已读真实 `/prediction/history`。

### 改动文件（全部 `631(625)/`）
后端 `main-system/backend/`：
- `app/services/fault_location_service.py` — `locate()` 加 `skip_gate: bool=False`（True 时跳过 `_assess_fault` 直接诊断）；`_build_output()` 故障路径置信度加下限 `round(min(max(conf,0.5),1.0),4)`（**≥50%**）。
- `app/services/llm_service.py` — `locate(fallback)` prompt 要求 `root_cause`=**问题本质/根本原因**（为什么发生、非症状）且 fault_type/root_cause 必填；`predict()` prompt 改为对**整体融合数据**综合判断 + 明确写出 一般/严重/紧急(green/yellow/red) 三级判据。
- `app/schemas/diagnosis.py` — `DiagnosisRequest` 加 `skip_gate: bool=True`（入口已收口，默认信任等级）。
- `app/api/v1/diagnosis.py` — `diagnose_text` 透传 `skip_gate`。

前端 `main-system/frontend/src/`：
- `views/Diagnosis/index.vue` — **入口收口**：删除"选日志/粘贴文本"输入（含 `inputMode/selectedRunId/logText/runs/loadRuns`）；仅接受预测页带来的 `route.query.run_id + level`，进入即自动诊断并显示等级上下文条；无 run_id 显示 `el-empty` 引导（"前往软件状态预测"）；结果区根因标题改为"问题本质（根因）"；保留结果卡片 + 诊断历史。
- `views/Prediction/SoftwarePrediction.vue` & `LevelAlarm.vue` — `gotoDiagnosis` 跳转时带 `query.level`（= health_status）供诊断页展示等级。

### 验证结果
- 后端 4 文件 `python3 -m py_compile` 全 **PASS**。
- 主前端 `npm install` + `npm run build` **EXIT 0**（Diagnosis/SoftwarePrediction/LevelAlarm 编译通过）；构建产物 + py_compile 的 `__pycache__` 已清理。
- 仅静态/编译验证，**未端到端运行**（无 docker 权限）。

### 重新部署（用户执行）
```bash
cd /home/junjiezuo/631-fault/'631(625)'
sudo docker compose build backend frontend-main
sudo docker compose up -d --force-recreate backend frontend-main
```
强刷浏览器 `Ctrl+Shift+R`；旧诊断记录需**重新走 预测→诊断** 才反映新逻辑。

### 下一步 / 端到端验证清单
1. 软件状态预测：选 run → 大模型分级出 一般/严重/紧急；"一般"无诊断按钮，"严重/紧急"出现「跳转故障诊断」。
2. 点跳转 → 诊断页自动开诊，顶部显示等级上下文；结果含**问题本质根因**、**置信度 ≥50%**、推理路径。
3. 直接打开诊断页（无 run_id）→ 显示引导空状态，无独立输入入口。
4. 同一 run 两轮诊断结果一致（已跳过二次门控，不再随机）。
5. 诊断历史可查看；预警记录 严重/紧急 可跳诊断。
- 注意：第三轮的 `_assess_fault`/`_build_no_fault_output` 门控仍保留（`skip_gate=False` 时生效，如文件上传 `/diagnosis` 路径），收口后主链路不再触达，无害。
- 相似度仍依赖知识库案例（见第五节第 2 条），与本轮置信度下限无关。

---

## 七、第五轮（报告为单元 + 相似度根因修复 + 记录可删）

> 用户确认 4 点目标逻辑均正确，按"最好的修改方法"全量实施：
> ① 软件状态预测的对象应是「多源状态数据接入」后的**接入报告整体**（当前单源=接入日志，其他源预留），而非数据库里一条条日志（那些是训练/微调语料）。
> ② 故障诊断针对**分级后的报告**做根因，报告可只含数据库日志但不强制只能选一条。
> ③ 预测/诊断记录都要落库、可查看、**可删除**。
> ④ 相似度不能为横线，必须跟「知识库管理」记录匹配得出；置信度必须 ≥50%。

### 关键根因定位（相似度横线）
诊断检索原走**离线 FAISS 索引**（`vector_retrieval_service`，读磁盘 `VECTOR_INDEX_PATH`，部署镜像通常无此文件 → 检索恒空 → 相似度"-"）；而「知识库管理」录入的案例写的是 **Chroma**（`VectorStoreService`，collection `fault_logs`）。两套库不连通即为根因。

### 改动文件（全部 `631(625)/`）
后端 `main-system/backend/`：
- `app/services/fault_location_service.py`
  - **P4**：新增 `_query_knowledge_base()` —— `_retrieve_and_aggregate` 改查 `get_vector_store()`（知识库 Chroma），`similarity=1-distance`、映射 `fault_type_name→fault_type`、`log_entry_id→case_id`、`root_cause`，喂给原聚合/KG/路由（下游不变）。相似度/置信度从此与知识库真实案例挂钩。FAISS import 保留但不再使用。
  - **P1/P2**：`locate()` 开头，run_id 进入且无文本时 `_load_report_text(run_id)` 取该接入报告聚合文本作诊断输入；新增静态方法 `_load_report_text`。置信度下限 0.5 仍在 `_build_output`。
- `app/services/prediction_service.py`
  - `_store_access_record(...,aggregated_text)` 改为生成 `report_id(=run_id)`、存 `aggregated_text`（≤20000）、返回 report_id；新增 `_aggregate_entries_text`、`_load_report`、`grade_report(report_id)`（取报告聚合文本→`predict`，无文本回退 `grade_run`）。
  - `extract_from_log_analysis` / `process_log_file`：接入后写 report_id+run_id+聚合文本，并回填进返回 report。
  - `get_access_results` item 暴露 `report_id`/`run_id`。
- `app/api/v1/prediction.py` — `/prediction/grade` 优先 `report_id`（调 grade_report）否则 run_id；新增 `DELETE /prediction/history/{id}`。
- `app/api/v1/diagnosis.py` — 新增 `DELETE /diagnosis/history/{id}`。
- `app/schemas/prediction.py` — `GradeRequest{report_id?, run_id?}`。
- `app/services/llm_service.py` — **未改**（第四轮的 predict 三级判据 + locate 问题本质 prompt 已满足）。

前端 `main-system/frontend/src/`：
- `views/Prediction/SoftwarePrediction.vue` — **整页重写**：列表数据源由 `listDatasetRuns` 换成接入报告 `getAccessResults`（带分页）；行=报告；「大模型分级」调 `gradeReport({report_id})`；严重/紧急跳诊断带 `run_id(=report_id)+level`；文案改"报告"。
- `views/Diagnosis/index.vue` — 文案"日志"→"接入报告"；诊断历史表加**删除**列（`deleteDiagnosis`+二次确认）。
- `views/Prediction/LevelAlarm.vue` — "关联日志"→"关联报告"；操作列加**删除**（`deletePrediction`+二次确认）。
- `api/prediction.js` — 加 `gradeReport`、`deletePrediction`（`gradeRun` 保留为别名）。
- `api/diagnosis.js` — 加 `deleteDiagnosis`。
- `views/Prediction/DataAccess.vue` — 未改（本就产出并列出接入报告）。

### 验证结果
- 后端 7 文件 `python3 -m py_compile` 全 **PASS**；`__pycache__` 已清理。
- 主前端 `npm install` + `npm run build` **EXIT 0**；`node_modules`/`dist` 已清理。
- 仅静态/编译验证，**未端到端运行**（无 docker 权限）。

### 重新部署（用户执行）
```bash
cd /home/junjiezuo/631-fault/'631(625)'
sudo docker compose build backend frontend-main
sudo docker compose up -d --force-recreate backend frontend-main
```
强刷浏览器 `Ctrl+Shift+R`。

### 端到端验证清单
1. 多源接入产生报告 → 「软件状态预测」列表显示**报告**（非单条日志），分级出 一般/严重/紧急。
2. 报告分级 严重/紧急 → 跳故障诊断 → 对报告整体给**问题本质根因**、**置信度 ≥50%**。
3. **先在「知识库管理」录入若干案例**（新增知识案例 / 上传日志 + 一键入库）→ 再诊断 → **相似度为真实数值（不再横线）**、置信度随相似度变化。
4. 预测记录（分级预警）、诊断记录均可**删除**（二次确认）、删除后列表刷新。
- 注意：相似度依赖知识库 Chroma 非空。知识库为空时仍无候选→相似度可能为空，此为数据前提（已接通代码通路），录入案例后即生效。
- 旧报告（本轮之前接入的）无 `aggregated_text`/`report_id`，分级会回退 `grade_run` 按窗口；建议重新接入生成新报告以走完整报告链路。

---

## 八、第六轮（工程瑕疵收口：分级回显落库 + 三页可删/级联 + 诊断历史回放）

> 用户确认 4 点工程瑕疵均成立，第 3 点删除语义由用户选定为**「级联删整份报告」**：
> ① 多源接入「查看报告」后加「跳转软件状态预测」按钮。
> ② 软件状态预测分级结果要落库**可回显**：分级后跳走再回来不能丢、不能每次手点；点报告行能看其已存分级详情；加报告 ID 列。
> ③ 删除按钮在「故障预测」下三页都要有；三页共享同一份报告，删除按 ID 且**级联**（报告 + 其分级 + 其诊断一起删，三页同时消失）。
> ④ 故障诊断历史点行要能**回放**之前的诊断结果（记录本就在 postgres）。

### 关键结论（瑕疵根因）
- ②④ 都是「数据其实已在库里、前端没读回 / 没回放」：分级写 `PredictionRecord(run_id=report_id)`、诊断写 `DiagnosisRecord`，但 `SoftwarePrediction.vue` 只把分级存前端内存 `grades`，诊断历史表无行点击回放。
- 三页的「报告」= `access_records`（key `report_id`，mongo-shim）；「分级预警」列的是 `PredictionRecord`（每次分级一条，`run_id=report_id`）。级联删除以 `report_id` 为轴。

### 改动文件（全部 `631(625)/`）
后端 `main-system/backend/`：
- `app/services/prediction_service.py`
  - 新增 `latest_prediction_out(run_id, db)` —— 取该报告最近一次 `PredictionRecord` 转 dict（无则 None），供列表回显/回放。
  - 新增 `delete_report(report_id, db)` —— **级联删**：`access_records.delete_one({report_id})` + `PredictionRecord` + `DiagnosisRecord`（`run_id==report_id`），一次 commit，子步骤失败不阻断。
- `app/api/v1/prediction.py`
  - 新增 `GET /prediction/by-run/{run_id}` → `Optional[PredictionOut]`（回显/回放）。
  - 新增 `DELETE /prediction/report/{report_id}`（级联删整份报告）；保留旧 `DELETE /prediction/history/{id}` 作孤立分级兜底。

前端 `main-system/frontend/src/`：
- `api/prediction.js` — 加 `getPredictionByRun(runId)`、`deleteReport(reportId)`。
- `views/Prediction/DataAccess.vue` — 操作列加「软件状态预测」跳转（`router.push({name:'SoftwarePrediction'})`）与「删除」（`deleteReport(row.report_id)` + 二次确认）。
- `views/Prediction/SoftwarePrediction.vue` — `loadReports` 后 `loadGrades()` 批量 `getPredictionByRun` 回显分级；加「报告ID」列；`@row-click=viewGradeDetail` 回放已存分级详情（无则提示先分级）；操作列加「删除」(级联)，所有操作按钮 `@click.stop` 防误触发行点击。
- `views/Prediction/LevelAlarm.vue` — `removeRecord` 改为 `row.run_id ? deleteReport(row.run_id) : deletePrediction(row.id)`（级联删整份报告，孤立分级兜底）。
- `views/Diagnosis/index.vue` — 诊断历史表 `@row-click=replay`（`result.value = row` 直接回放，历史项已带全字段）；删除按钮改 `@click.stop`；加 `.clickable-row` 光标样式。

### 验证结果
- 后端 2 文件 `python3 -m py_compile` **PASS**；`__pycache__` 已清理。
- 主前端 `npm install` + `npm run build` **EXIT 0**（DataAccess/SoftwarePrediction/LevelAlarm/Diagnosis 编译通过）；`node_modules`/`dist` 已清理。
- 仅静态/编译验证，**未端到端运行**（无 docker 权限）。

### 重新部署（用户执行）
```bash
cd /home/junjiezuo/631-fault/'631(625)'
sudo docker compose build backend frontend-main
sudo docker compose up -d --force-recreate backend frontend-main
```
强刷浏览器 `Ctrl+Shift+R`。

### 端到端验证清单
1. 多源接入列表每行「软件状态预测」可跳转到预测页；「删除」二次确认后该报告从三页消失。
2. 软件状态预测：分级后离开再回来，「分级结果」列仍显示（已落库回显）；「报告ID」列可见；点任意报告行弹出其分级详情；删除级联生效。
3. 分级预警：删除一条 → 对应报告及其分级/诊断一并消失（级联）。
4. 故障诊断历史：点某一行 → 上方结果卡片回放该条诊断（根因/置信度/推理路径），不重新调用大模型。
- 注意：删除按 `report_id` 级联；旧报告无 `report_id` 时删除按钮会提示无法删除（建议重新接入）。其余数据前提同第七节（相似度需知识库非空）。

---

## 九、第七轮（工程瑕疵第二批：ID 统一 / 分级反映错误 / 诊断不出正常 / 相似度 / 解析置信度）

> 用户复核后提 5 点小瑕疵，逐条核对结论：①②③⑤成立，④部分成立（代码已正确接知识库，横线=知识库为空，非 Bug）。按"如正确给 plan 后执行"全量实施。

### 关键根因定位
1. **多源接入 ID 与预测/预警对不上**：多源接入列表 ID 列显示 mongo 文档 `_id`，预测页/预警页用 `report_id`(uuid)。→ 多源接入改显示 `report_id`，三页一致。
2. **选全部标签报告却分级"一般"**：两处丢级别——`_annotate_logs` 用正文正则重测级别，把本是 ERROR 的中文日志误判 INFO；`_aggregate_entries_text` 只拼 `content` 不带级别 → 规则评分 `_LEVEL_RE` 扫不到 → green。
3. **故障诊断回放出"正常"**：`locate` 顶部命中旧的 `is_fault=False` 缓存会直接回放成"正常"；且历史里有修复前遗留的正常记录。
4. **相似度横线**：`get_vector_store()`(诊断读) 与 `VectorStoreService()`(知识库写) 是**同一 Chroma collection `fault_logs`**，已正确接通；横线 = 知识库当前没有案例（数据前提），非代码 Bug。
5. **解析置信度 <50%**：`_analyze_text` 返回的 `confidence`（LogParse 页"置信度"）规则/低 severity 路径可 <0.5。

### 改动文件（全部 `631(625)/`）
后端 `main-system/backend/`：
- `app/services/prediction_service.py`
  - 新增常量 `_VALID_LEVELS`。
  - `_annotate_logs`：优先保留来源 `level`（含 WARNING→WARN 归一），仅缺失/非法时才正文正则推断 → **修 ②**。
  - `_aggregate_entries_text`：每行加 `[LEVEL]` 前缀，使规则评分扫得到 ERROR/FATAL/WARN、大模型也看清严重度 → **修 ②**。
- `app/services/fault_location_service.py`
  - `locate` 缓存复用门槛：`cached.is_fault or not skip_gate` —— 从预测页 严重/紧急 跳入(skip_gate=True)时**不复用旧"非故障"记录**，强制重诊（结论必为故障）→ **修 ③**。
  - `_build_output`：相似度 `sim_display = round(top_sim,4) if (candidate_list and top_sim is not None) else None` —— 知识库有候选即给真实百分比，空库才为「-」→ **④ surfacing**。
- `app/api/v1/diagnosis.py`：新增 `DELETE /diagnosis/history/normal`（清除全部 `is_fault=False`），**注册在 `/{record_id}` 之前**避免 "normal" 撞 int 路由 → **修 ③**。
- `app/api/v1/log_analysis.py`：`_analyze_text` 加 `final_confidence = min(max(conf,0.5),1.0)` → **修 ⑤**。

前端 `main-system/frontend/src/`：
- `views/Prediction/DataAccess.vue`：接入结果列表 ID 列 → 显示 `report_id || run_id || id`，标签「报告ID」→ **修 ①**。
- `api/diagnosis.js`：新增 `deleteNormalDiagnoses()`。
- `views/Diagnosis/index.vue`：诊断历史卡片头加「清除正常记录」按钮 + `clearNormal()` 二次确认 → **修 ③**。

### 验证结果
- 后端 4 文件 `python3 -m py_compile` **PASS**；`__pycache__` 已清理。
- 主前端 `npm install` + `npm run build` **EXIT 0**（DataAccess/Diagnosis 等编译通过）；`node_modules`/`dist` 已清理。
- 仅静态/编译验证，**未端到端运行**（无 docker 权限）。

### 重新部署（用户执行）
```bash
cd /home/junjiezuo/631-fault/'631(625)'
sudo docker compose build backend frontend-main
sudo docker compose up -d --force-recreate backend frontend-main
```
强刷浏览器 `Ctrl+Shift+R`。

### 端到端验证清单
1. **①** 多源接入列表 ID = `report_id`，与「软件状态预测」报告ID、「分级预警」关联报告一致。
2. **②** 选含 故障/异常 标签接入新报告 → 软件状态预测分级为 严重/紧急（不再误判"一般"）。⚠️ 需**重新接入**才会带 `[LEVEL]` 聚合文本；旧报告聚合文本无级别前缀。
3. **③** 在诊断历史点「清除正常记录」→ 正常记录清空；之后从预测页 严重/紧急 跳诊断，回放/重诊**只出故障**，不再出现"正常"。
4. **④** 相似度：**先在「知识库管理 → 新增知识案例」录入案例**（写入 Chroma `fault_logs`）→ 再诊断 → 相似度显示真实百分比；知识库为空时仍为「-」（数据前提，非 Bug）。
5. **⑤** 日志解析「置信度」恒 ≥ 50%。
- 注意：② 仅对**本轮之后新接入**的报告生效（聚合文本需带级别前缀）；旧报告建议重新接入。④ 的横线只能靠录入知识库案例消除。

---

## 十、第八轮（软件状态预测「有异常却判一般」根因修复）

> 用户场景：接入 2026.5.1–6.25 全部 4 个标签，接入报告 `total_logs:471 / abnormal_logs:18`（内存故障3、未知故障15），但软件状态预测分级却是「一般」，`risk_summary` 为"系统运行正常，无异常日志，所有请求成功处理"。用户判定逻辑错误，要求修复。选定**全量稳健修复**。

### 关键根因（三个叠加 bug，决定性在分级打分链路，非聚合文本）
`risk_summary` 的措辞是 **LLM 输出**（规则路径只会写"系统运行正常，未发现明显风险"或"发现 N 个风险项…"），说明 LLM 启用且把报告判成了 green：
1. **LLM 只看前 3000 字符**：`_llm_polish` 调 `self.llm.predict(log_text[:3000], ...)`。471 条日志聚合 ~2万字符，18 条异常(4%)散落其后，头部 3000 字符大概率全是 INFO → LLM 只看到正常日志 → 判 green、"无异常日志"。
2. **LLM 判定无条件覆盖规则**（第二轮去掉了 `_max_status` 不降级逻辑）：规则即便扫到那 18 条 ERROR/WARN 给 yellow/red，也被 LLM 的 green 直接覆盖。
3. **规则阈值偏松**：`ERROR>3` 才 yellow，少量错误判 green，不符合"有错误就不该是一般"。

### 改动文件（全部 `631(625)/`，本轮纯后端）
`main-system/backend/app/services/prediction_service.py`：
- 新增 `_build_llm_input(log_text)` —— 交给大模型分级的输入改为**异常行(CRITICAL/FATAL/PANIC/ERROR/WARN)优先 + 级别计数摘要置顶**，替代原 `log_text[:3000]` 头部截断 → **修 bug1**。
- `_llm_polish`：① 输入走 `_build_llm_input`；② **规则作为下限**——`final_status = _max_status(llm_status, rule_status)`，LLM 可升级不可把规则已检出异常降级；③ 被规则抬升时改用规则摘要（避免等级是严重却显示"无异常日志"）→ **修 bug2**。
- `_rule_score`：ERROR 阈值 `>3 → ≥1` 即「严重」（>10 仍紧急）→ **修 bug3**。
- `predict(...)` 新增 `floor_status`/`floor_summary` 参数：**报告级统计下限**——`grade_report` 传入报告自带的 `abnormal_logs`，>0 时分级至少「严重」，并用规则/统计摘要替换可能"正常"的 LLM 摘要。
- `grade_report`：从 `rec["report"]["abnormal_logs"]` 算 `floor_status`(异常>0→yellow)+`floor_summary`(含 total/异常数/故障分布)，传给 predict / grade_run。**该下限取自报告自身统计，对已接入的旧报告也立即生效，无需重新接入**。
- `grade_run(...)`：同步加 `floor_status`/`floor_summary` 形参并透传 predict。

四处叠加，保证「报告含异常 → 分级绝不再判一般」，且真正正常的报告(abnormal=0)仍为 green，不过度告警。LLM 不可用时全链路回退规则评分，优雅降级不变。

### 验证结果
- 后端 `python3 -m py_compile app/services/prediction_service.py` **PASS**；`__pycache__` 已清理。
- 用户已实际 `sudo docker compose build` 成功（`faultdiag-backend:latest` 重建/缓存命中，含本轮代码）；端到端业务验证由用户在 :8080 完成。

### 端到端验证清单
1. 对**用户已接入的那份报告**（abnormal_logs:18）重新点「大模型分级」→ 分级 ≥ **严重(yellow)**，不再「一般」；风险说明改为"接入报告共 471 条日志，检出异常 18 条（内存故障 3 条、未知故障 15 条）…"或规则风险项，不再出现"无异常日志"。
2. 接入一份**纯正常日志**(abnormal_logs:0) → 分级仍为 **一般(green)**，未过度告警。
3. 含 >10 条 ERROR 或 CRITICAL/FATAL 的报告 → 分级 **紧急(red)**。
4. 关闭 LLM（`ENABLE_LLM=False`）→ 走规则评分，有 ERROR 仍判 严重/紧急，不报错（优雅降级）。
- 注意：本轮统计下限取自报告 `abnormal_logs`，**旧报告也立即生效**（不像第七轮 ② 需重新接入）；但若旧报告 `report` 字段缺 `abnormal_logs`，则仅靠规则/LLM 评分（仍较第七轮前准确）。

---

## 十一、部署包生成（631(625deploy)，Docker + K8s 交付物）

> 用户要求：把部署镜像与部署手册单独生成在 `631(625deploy)` 文件夹，Docker 与 K8s 两套都要；手册模板原指向 `/home/nanye/jj/dist-deploy`（**无读取权限**），改用仓库内同类模板 `631-fault/dist-deploy/` 为基底，针对 625 版更新。

### 生成位置与内容
新建 `631-fault/631(625deploy)/`（与 `631(625)` 同级的**独立目录**，未触碰源码三处约束）：
```
631(625deploy)/
├── docker-compose.yaml     image-only（无 build context），服务/镜像名对齐 631(625) 构建产物
├── .env.example            LLM 默认关闭 + DashScope/DeepSeek/vLLM 三示例
├── save-images.sh          源机打包 6 镜像 → images-integrated.tar.gz（含 build 前置说明）
├── load-images.sh          目标机加载
├── README.md               部署手册：零(生成镜像)/一(Docker)/二(K8s)/三(排查)/四(修订记录含八轮)
├── k8s/                    7 清单（00 ns / 01 config / 02-03 两库 / 04-05 两后端 / 06-07 两前端，NodePort 30080）
└── images-integrated.tar.gz  ← 用户已生成（954M gzip，内含正好 6 个镜像）
```
镜像名：`faultdiag-backend:latest` / `faultdiag-frontend:integrated` / `faultdiag-backend-annotate:latest` / `faultdiag-frontend-annotate:latest` / `faultdiag-postgres:16` / `faultdiag-postgres-annotate:16-alpine`。

### 验证结果
- `sh -n` 校验两脚本语法 **PASS**；目录结构、k8s 7 文件齐全。
- 用户已执行 `sudo docker compose build`（4 业务镜像）+ `pull postgres:16/16-alpine` + `sudo sh save-images.sh`：**`images-integrated.tar.gz` 954M 生成成功**，`tar -tzf` 校验内含 6 个 RepoTags 全对。
- 954M 为 `gzip -1` 压缩后体积（手册"~3GB"是松散估算），正常。

### 下一步（用户执行，交付目标机）
- Docker：拷整个目录 → `sh load-images.sh` → `cp -n .env.example .env`（按需开 LLM）→ `docker compose up -d`。
- K8s：`tag → push 私仓 → sed 替换 REGISTRY_PLACEHOLDER → kubectl apply -f k8s/`（详见 README 二章）。
- 可选自检镜像内是否含第八轮逻辑：
  `docker compose exec backend grep -n "_build_llm_input\|floor_status" app/services/prediction_service.py`
- 待办（用户未决）：是否把上述自检命令补进 README 排查表（已提议，未执行）。

---

## 十二、第九轮：左侧导航 IA + 数据流重构（第一阶段，2026-07-02）

### 背景
旧导航散乱（3 个独立上传入口），且"多源接入"因 mongo 兼容层忽略库名而退化成扫原始日志。改为线性血缘：**数据治理（入库唯一入口）→ 在线处理（分析唯一入口）→ 日志分析分叉 → 预测预警 / 故障诊断**。DB1–DB5 为逻辑阶段（均落现有单库/集合）：DB1=runs/log_entries；DB2=标注库 data_bj/annotations；DB3=Chroma fault_logs；DB4=KG；**DB5=log_analysis_results（键 run_id，含 has_fault）**。

### 新导航（7 组）
系统概览 / 数据治理(数据导入·数据标注·知识库·知识图谱) / 模型管理 / 在线处理(上传文件·文件选择) / 日志分析 / 故障诊断 / 预测预警。菜单由 `AppLayout.vue` 从 router children 自动派生（未改）。

### 改动文件（全部 631(625)/main-system/）
- 后端 `backend/app/api/v1/log_analysis.py` — 新增 `GET /log-analysis/analysis/results?verdict=normal|abnormal&page&page_size`（DB5 列表，读 log_analysis_results，返回 {items,total}）。`py_compile` PASS。
- 前端 `frontend/src/router/index.js` — 重构 7 组 + 旧路径隐藏重定向（data-processing/*、prediction/*、log-analysis/*、knowledge-base、knowledge-graph）。`node --check` PASS。
- 前端 `frontend/src/api/logAnalysis.js` — 加 `listAnalysisResults()`。
- 前端 `frontend/src/views/LogAnalysis/LogAnalysis.vue`（文件选择）— 行操作加「分析」→ 跳日志分析带 run_id。
- 前端 `frontend/src/views/LogAnalysis/LogParse.vue`（日志分析）— onMounted 读 run_id 自动解析；判定卡加分叉 CTA（异常→故障诊断 / 正常→预测预警）。
- 前端 `frontend/src/views/Diagnosis/index.vue` — 改读 DB5 异常列表选文件诊断；保留 run_id 直达。
- 前端 `frontend/src/views/Prediction/index.vue` — 由壳+3子页收敛为单页，读 DB5 正常列表分级 + 预警记录；移除多源接入入口（旧子页 DataAccess/SoftwarePrediction/LevelAlarm.vue 留盘但不再路由/打包）。

### id 空间
三处 id 统一为 `run_id`：`parse/{log_id}` 实为按 run_id 查 log_entries；诊断/分级均按 run_id。文件选择→日志分析→诊断/预测全链路用 run_id 贯通。

### 验证
- 后端 `py_compile` PASS；前端 `node --check` router/api PASS；无悬空引用；旧子页无其它 import（不打包）。
- **端到端未测**（node_modules 不在本机，前端在 docker 构建）。用户需 `sudo docker compose build backend frontend-main && up -d --force-recreate` 后在 :8080 强刷验证。

### 第二阶段（第九轮后用户已拍板、待 plan+实现，产物将放入新目录 `631change`）
1. **预测预警/故障诊断语义**：正常文件→预测预警（大模型做"未来风险/预警"分析），异常文件→故障诊断（大模型根因）。
2. **数据标注从 DB1 取数**：打通主库 fault_diagnosis→标注子系统 data_bj（导出/导入桥 + 标注前端「选择已有数据集」）。
3. **DB2→DB3/DB4 策展**：从标注结果挑典型案例入知识库(RAG)/知识图谱(KG)，新增 UI+后端流程。
4. 复核大模型置信度与日志解析 prompt（`llm_service.parse_log`）。

