# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

故障诊断平台 (Fault Diagnosis Platform) — a web app for ingesting, browsing, and analyzing embedded-system / software fault logs, with RAG-based diagnosis, fault prediction, a knowledge base, and a knowledge graph. Backend is FastAPI + PostgreSQL; frontend is Vue 3 + Element Plus. UI and most code comments are in Chinese.

This directory (`fault-6.5/`) is the extracted source tree from the `faultdiag-source-20260615.tar.gz` release bundle. The bundle parent dir also contains prebuilt Docker images (`faultdiag-images-*.tar.gz`) and an image-only deploy directory; for code work, operate inside `fault-6.5/`.

## Architecture

Three services, orchestrated by `deploy/docker-compose.prod.yaml`:

- **postgres** (postgres:16) — the *only* datastore. See storage note below.
- **backend** (`backend/`, FastAPI on :8000, mapped to host :5000)
- **frontend** (`frontend/`, Vue 3 SPA served by nginx on :80, mapped to host :8080)

### Single-database design (important)

Despite names and history, **everything lives in one PostgreSQL database**:

- **Relational data** (`runs`, `cases`, `fault_types`, `diagnosis_records`, `prediction_records`, `dataset_imports`, …) via SQLAlchemy 2.0 models in `backend/app/models/`. Session via `get_db()` in `backend/app/database.py`.
- **"MongoDB" document collections** (`log_entries`, `log_windows`, upload/analysis docs) are **not** in real Mongo. `backend/app/mongo_compat.py` implements a pymongo-compatible shim (`PgMongoClient`) backed by a single JSONB table `mongo_docs`. Call sites use `get_mongo_db()` and keep pymongo-style `db["coll"].find/insert_one/...`. Datetimes are tagged `{"__dt__": iso}` in JSONB and restored on read. When touching document storage, work through this shim — do not add a real Mongo dependency.
- **Vector store**: local Chroma `PersistentClient` persisting to `CHROMA_PERSIST_PATH` (`backend/app/services/vector_store.py`), embedding with the local `LocalEmbeddingService` (bge-small-zh-v1.5, packed into the image at `/models`). No external Chroma server, no network calls — older `CHROMA_HOST/PORT` + DashScope code is gone (it used to 500 by hitting FastAPI's own :8000).

> **handoff.md is historical and partly stale.** It describes an earlier MySQL + real-MongoDB + `/home/yenan/...` setup and a separate `data_pipeline/scripts` ingestion flow. The current release migrated to single-PostgreSQL with the JSONB shim. Trust `config.py` / `database.py` / `docker-compose.prod.yaml` over handoff.md for the present architecture.

### Backend layout (`backend/app/`)

- `main.py` — FastAPI app, mounts all routers under `/api/v1` (plus `/api` debug routers). `/health` and `/api/docs` (Swagger).
- `api/v1/*.py` — routers per domain: `knowledge_base`, `diagnosis`, `prediction`, `graph`, `overview`, `log_analysis`, `data_processing`, `data_import`, `embedding`. Debug: `test_data_flow`, `log_pipeline`.
- `services/*.py` — business logic (e.g. `diagnosis_service`, `vector_store`, `log_dataset_service`, `data_import_ingest_service`, `kg_service_v2`, `local_embedding_service`, `llm_service`).
- `models/*.py` — SQLAlchemy ORM. `schemas/*.py` — Pydantic request/response models. `config.py` — `Settings` (pydantic-settings, env-driven). `utils/`.

Diagnosis flow (`services/diagnosis_service.py`): query vector store → if top similarity ≥ `SIMILARITY_THRESHOLD` take a "fast" channel returning the matched fault type; otherwise fall through to the LLM channel (`llm_service.py`). Records persist to `diagnosis_records`.

### Frontend layout (`frontend/src/`)

- `router/index.js` — hash-history routes. Note: "日志列表/详情" entries were re-homed under `data-processing` (`#/data-processing/logs`, `#/data-processing/logs/:runId`); old `#/log-analysis/analysis*` URLs redirect for compatibility. Menu modules: Overview, KnowledgeBase, FineTuning, KnowledgeGraph, Diagnosis, Prediction, DataImport, DataProcessing, LogAnalysis.
- `api/*.js` — axios wrappers per domain; all go through `utils/request.js`.
- `utils/request.js` — axios instance; `baseURL = <basePath>/api/v1`. `basePath` resolves at runtime from `window.__APP_CONFIG__.basePath` (written into `public/config.js` by the container entrypoint), Wujie micro-frontend props, or empty for standalone. Auth token sent as `X-Token` header.
- In dev, Vite proxies `/api` → `127.0.0.1:8000` (`vite.config.js`); in prod, nginx proxies `/api/` → `backend:8000` (`nginx/default.conf`).

### Backend startup sequence

`backend/docker-entrypoint.sh`: wait for Postgres → `create_tables.py` → `seed.py` (preset fault types + sample logs, idempotent) → if `AUTO_INGEST=1`, `ingest_dataset.py --data-root /app/data` (bundled dataset) → `uvicorn app.main:app`. Schema is created via `create_tables.py`; Alembic migrations also exist in `backend/alembic/versions/` (`alembic.ini`).

## Common commands

### Run the full stack (Docker, from source)

```bash
cd deploy
docker compose -f docker-compose.prod.yaml up -d --build   # builds backend+frontend images
# Frontend  http://<host>:8080
# API/Swagger http://<host>:5000/api/docs
# Health    http://<host>:5000/health
docker compose -f docker-compose.prod.yaml ps               # wait for (healthy)
docker compose -f docker-compose.prod.yaml logs backend     # debug
docker compose -f docker-compose.prod.yaml down             # stop (keeps pgdata volume)
docker compose -f docker-compose.prod.yaml down -v          # stop + WIPE postgres volume
```

Backend image rebuilds reinstall torch/sentence-transformers and can be slow/network-fragile. For a quick single-file fix during dev, `docker cp` the file into `faultdiag-backend` and `docker restart` it (temporary only, not a release path).

### Backend (local dev)

```bash
cd backend
pip install -r requirements.txt          # requirements.prod.txt is used for the image
uvicorn app.main:app --reload --port 8000
python -m pytest tests/                   # or: python -m pytest tests/test_log_dataset_service.py
python -m pytest tests/test_log_dataset_service.py::test_name -q   # single test
```

Backend requires a reachable PostgreSQL; configure via env (see Configuration). Helper scripts at `backend/`: `create_tables.py`, `drop_tables.py`, `seed.py`, `ingest_dataset.py`, plus `check_*.py` inspection scripts.

### Frontend (local dev)

```bash
cd frontend
npm install
npm run dev        # Vite dev server :5173, proxies /api to :8000
npm run build      # production build (output consumed by Dockerfile/nginx)
node --test tests/logAnalysisTransforms.test.mjs   # frontend unit test
```

## Configuration

Backend settings are env-driven via `backend/app/config.py` (`Settings`). Override with a `.env` in the deploy directory (compose uses `${VAR:-default}`). Key vars:

- **Postgres**: `POSTGRES_HOST/PORT/USER/PASSWORD/DB` (or full `DATABASE_URL`). `MONGO_DB` is just a logical name for the JSONB shim — point it at the same DB.
- **Embedding** (local, on by default): `ENABLE_EMBEDDING=True`, `LOCAL_MODEL_PATH=/models/bge-small-zh-v1.5`, `EMBEDDING_DEVICE=cpu`. Remote OpenAI-compatible alternative: `EMBEDDING_API_BASE/MODEL/API_KEY` (`services/embedding_client.py`).
- **LLM** (off by default in prod compose): set `ENABLE_LLM=True` and `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY` (OpenAI-compatible / DashScope). Note `config.py` ships with a non-empty default LLM key/URL — compose explicitly sets `ENABLE_LLM=False`.
- **Vector store**: `CHROMA_PERSIST_PATH` (default `/app/outputs/vector_store/chroma`).
- **Data import**: `DATA_IMPORT_ROOT`, `DATA_IMPORT_AUTO_INGEST`, `AUTO_INGEST` (entrypoint), `DATA_IMPORT_ALLOWED_EXTS` (zip/tar/tar.gz/tgz).

## Data model & ingestion concepts

- A **run** (`runs` table) is one test execution; runs belong to a **case** (`cases`). `test_name` lives on `cases`, **not** `runs` — list queries must `LEFT JOIN cases` for it (see `services/log_dataset_service.py`).
- Dataset import upserts by `run_id`: re-uploading the same `Test_XXX` updates content rather than adding rows. Ingestion reports `new_run_count` / `updated_run_count` and records to `dataset_imports`; import metadata is threaded into `runs.stats_json["dataset_import_meta"]`.
- Log-list CRUD: `PATCH /log-analysis/logs/{run_id}`, `DELETE /log-analysis/logs/{run_id}` (cascades PG runs + mongo_docs + Chroma), `POST /log-analysis/logs/batch-delete`.
- `data_pipeline/scripts/` holds the older standalone numbered ETL (`01_scan` → `02_parse` → `03_ingest` → `04` windows → `05` FAISS → `06` KG). The current in-app path is the upload-driven `data_import` flow (`services/data_import_ingest_service.py`); the numbered scripts predate it and reference the old MySQL+Mongo setup.

## Additional docs (in source bundle)

`部署说明-v2.md` (detailed deploy + K8s), `K8s部署与读取新数据.md`, `大模型与Embedding接口说明.md` (wiring real LLM/embedding), `handoff.md` (historical), `dist/k8s/*.yaml` (K8s manifests).
