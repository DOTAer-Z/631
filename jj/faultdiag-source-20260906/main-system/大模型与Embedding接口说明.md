# 大模型 (LLM) 与 Embedding 接口说明

> 配合本轮改动：系统更名「故障定位系统」、前端白底主题、**预留 LLM/Embedding 接口**（默认不强启真实模型）。
> 更新时间：2026-06-14

---

## 一、你给的接口资料核对结论

**`大模型API_key.txt`**（OpenAI 兼容端点）：
```
LLM_BASE_URL = http://172.30.4.43:8001/v1
LLM_MODEL    = dsqwen32b
LLM_API_KEY  = dummy   (随意)
LLM_TIMEOUT_SECONDS    = 60
LLM_MAX_OUTPUT_TOKENS  = 1024
```

**`data_bj_6.13.zip`**（你后续微调用的**日志标注平台**，即"数据处理里的一个小栏"）：
- 其 `backend/app/services/llm/client.py` 是 **httpx 版 OpenAI 兼容** chat 客户端
  （`POST {base}/chat/completions` + Bearer token + JSON 模式）。
- 配置字段 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TIMEOUT_SECONDS / LLM_MAX_OUTPUT_TOKENS`
  与 txt **完全一致**。
- **没有 embedding 相关代码**（只有 LLM）。

**结论：txt 与 zip 的 LLM 接口一致**；embedding 接口由我方按相同风格（OpenAI 兼容）新建，端点留空待你填。

---

## 二、本轮已做（留出接口，未强启模型）

| 改动 | 文件 |
|---|---|
| LLM 客户端优雅降级 + 支持 `max_tokens` | `backend/app/services/llm_service.py` |
| 新增 OpenAI 兼容 Embedding 客户端（预留，未接入业务） | `backend/app/services/embedding_client.py` |
| 配置项：`LLM_MAX_OUTPUT_TOKENS`、`EMBEDDING_API_BASE/API_KEY/MODEL` | `backend/app/config.py` |
| 编排预留 env（注释，便于启用） | `deploy/docker-compose.prod.yaml`、`dist/docker-compose.yaml`、`deploy/k8s/02-backend.yaml` |

- LLM 现有调用入口不变：`LLMService.diagnose() / predict() / locate() / preprocess()`；
  故障诊断服务 `diagnosis_service.py` 已通过 `LLMService` 调用，启用后即可工作。
- Embedding 客户端**仅作接口**，暂未接入 `vector_store.py` / `vector_retrieval_service.py`。

---

## 三、如何启用大模型（指向你 txt 的端点）

### Docker Compose
编辑 `docker-compose.yaml` 的 backend → environment，取消注释并设值：
```yaml
      ENABLE_LLM: "True"
      LLM_BASE_URL: "http://172.30.4.43:8001/v1"
      LLM_MODEL: "dsqwen32b"
      LLM_API_KEY: "dummy"
      LLM_MAX_OUTPUT_TOKENS: "1024"
```
```sh
docker compose up -d        # 或 docker compose restart backend
```

### Kubernetes
在 `k8s/02-backend.yaml` 取消注释对应 env，`kubectl apply -f k8s/02-backend.yaml -n faultdiagnosis` 后 `rollout restart`。

> 注意：端点是**内网地址** `172.30.4.43:8001`，容器/Pod 所在网络必须能访问到它。
> 自测：`curl http://172.30.4.43:8001/v1/models`（在部署机上）能通即可。

---

## 四、Embedding 接口形状与待填项

`embedding_client.py` 走 OpenAI 兼容 `POST {base}/embeddings`：
```python
from app.services.embedding_client import get_embedding_client
c = get_embedding_client()
if c.is_configured():
    vecs = c.embed(["文本1", "文本2"])   # -> List[List[float]]
```
启用只需填（端点你后续给我）：
```yaml
      EMBEDDING_API_BASE: "http://<embedding服务>/v1"
      EMBEDDING_MODEL: "<模型名，如 bge-m3>"
      EMBEDDING_API_KEY: "dummy"
```
> 若 embedding 与 LLM 是**同一台推理服务**且也暴露 `/v1/embeddings`，`EMBEDDING_API_BASE` 填同样的
> `http://172.30.4.43:8001/v1` 即可——这点需要你确认该服务是否提供 embeddings 接口。

---

## 五、下一步（按"一步一步来"，本轮未做）

目标业务流（你的描述）：
```
故障预测：对上传日志做分析
   ├─ 命中 RAG/向量库 → 快速判定（正常/异常）
   └─ 未命中 → 交给大模型解析
故障诊断：大模型给出 分析 / 具体原因 / 解决方法
知识图谱：服务于向量数据库的知识图谱
```
对接位置（已就绪的接口）：
- 向量检索/RAG：`vector_retrieval_service.py`（本地FAISS）或新 `embedding_client.py`（远程）二选一接入。
- 大模型解析与诊断展示：`llm_service.diagnose()/locate()` → `diagnosis` 接口 → 前端「故障诊断」页。

待你确认：① embedding 服务端点是否就用 `172.30.4.43:8001/v1`；② RAG 向量库用现有 FAISS 产物还是重建。
确认后我再把"预测→RAG→大模型→诊断展示"这条流水线接起来。
