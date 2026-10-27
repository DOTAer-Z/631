# 新版数据处理脚本（MySQL + MongoDB + FAISS + KG）

这套脚本是在你原来的 5 个脚本基础上重构的，目标是适配新的分层：

- **MySQL**：元数据层 / 任务层 / 结果层
- **MongoDB**：证据层
- **FAISS**：独立向量库
- **知识图谱**：导出为节点/边 JSONL

## 推荐执行顺序

```bash
python3 00_init_storage.py
python3 01_scan_dataset.py --dataset-root data/dataset --out artifacts/manifest.jsonl --system openstack --include-system-in-ids
python3 02_parse_fip_info_to_mysql.py --manifest artifacts/manifest.jsonl
python3 03_ingest_logs_to_mongo_v2.py --manifest artifacts/manifest.jsonl
python3 04_build_log_windows_v2.py --strategy error
python3 05_build_vector_index_faiss.py --model-path /models/all-MiniLM-L6-v2 --out-dir artifacts/vector_store
python3 06_build_knowledge_graph.py --out-dir artifacts/kg
```

## 脚本职责

### 00_init_storage.py
初始化：
- MySQL 表结构
- MongoDB 集合索引

### 01_scan_dataset.py
沿用旧 manifest 设计；它和新结构兼容。

### 02_parse_fip_info_to_mysql.py
读取 `fip_info.data`，写入：
- systems / subsystems / components
- cases / runs
- labels / object_labels
- diagnosis_tasks / diagnosis_results（黄金结果）

### 03_ingest_logs_to_mongo_v2.py
逐行解析日志，写入：
- MongoDB `log_entries`
- MongoDB `evidence_inputs`
- MySQL `runs.stats_json` 与时间范围统计

### 04_build_log_windows_v2.py
从 `log_entries` 聚合窗口到 `log_windows`，并同步更新：
- MySQL `runs.window_count`
- MySQL `task_feature_summaries`

### 05_build_vector_index_faiss.py
从 `log_windows.text` 构建独立向量库：
- `faiss.index`
- `metadata.jsonl`
- `build_info.json`

### 06_build_knowledge_graph.py
从 MySQL + MongoDB 导出一份可落盘的知识图谱：
- `kg_nodes.jsonl`
- `kg_edges.jsonl`
- `kg_summary.json`

## 依赖

```bash
pip install pymysql pymongo python-dateutil sentence-transformers torch faiss-cpu
```

## 备注

1. 我保留了你的 `manifest.jsonl` 入口设计，这样迁移成本最低。
2. 向量库这里选了 **FAISS**，因为它最适合你现在这种离线、本地、可部署到内网的场景。
3. 知识图谱脚本先输出 **节点/边 JSONL**，后面如果你确定要上 Neo4j，我再帮你补一版 `Neo4j import CSV` 脚本会更稳。
