# 故障定位主系统 + 日志标注子系统 集成系统 · 部署包（631_9.7 6 镜像版）

`631_9.7`（2026-09）新代码重打镜像。在 631(7.6) 集成基线上：修复标注导入二进制/元数据误解析 + 打通 LLM 网关。

两种部署方式，各手册自包含：

| 部署方式 | 手册 | 适用场景 |
|---|---|---|
| **Docker Compose** | [`README-docker.md`](README-docker.md) | 单机 / 联调 |
| **Kubernetes** | [`README-k8s.md`](README-k8s.md) | 集群部署，NodePort 30080 |

> 本包为 **6 镜像精简包**（不含训练镜像）。需要训练时手动构建追加（见 README-k8s.md 附录）。

## 包内容

```
631_9.7/k8s-deploy/
├── docker-compose.yaml         Compose 一键编排（image-only，6 服务）
├── .env.example                环境变量样例（DB / LLM / 网关 token / 加密Key）
├── save-images.sh              在源机打包 6 镜像（dev → tar.gz）
├── load-images.sh              在目标机加载镜像（自动合并分片 → docker）
├── k8s/                        Kubernetes 部署清单（00~07）
├── images-integrated.tar.gz.00.part   6 个镜像的单分片（927M，<1G）
├── images-integrated.tar.gz.sha256    合并后校验用
├── README.md                   本索引
├── README-docker.md            Docker Compose 部署手册
└── README-k8s.md               Kubernetes 部署手册
```

> **镜像分包说明**：6 镜像压缩后约 927M，单分片 `images-integrated.tar.gz.00.part`（<1G）。
> 以后镜像更大时，`split -b 1024m -d -a 2 images-integrated.tar.gz images-integrated.tar.gz.` 重切，
> `load-images.sh` 自动合并 `.*.part` 并校验。
> 拷贝到目标机时，**把全部分片 + `.sha256` 放进同一目录**。

## 服务拓扑（6 个服务）

```
浏览器
  ├─ frontend-main (nginx) ─┬─ /                主系统 SPA
  │   Compose :8080         ├─ /api/             backend (主)
  │   K8s   :30080          ├─ /annotate/        frontend-annotate
  │                         └─ /annotate-api/v1/ backend-annotate

数据库（独立两套）：postgres (fault_diagnosis) / postgres-annotate (data_bj)
```

| 服务 | 镜像 |
|---|---|
| postgres | `faultdiag-postgres:16` |
| backend | `faultdiag-backend:latest` |
| frontend-main | `faultdiag-frontend:integrated` |
| postgres-annotate | `faultdiag-postgres-annotate:16-alpine` |
| backend-annotate | `faultdiag-backend-annotate:latest` |
| frontend-annotate | `faultdiag-frontend-annotate:latest` |

## 本版要点

- **标注导入修复**：排除二进制/元数据文件（`__MACOSX` / `._*` / `fip_info.data` / `ground_truth.json` / 含 NUL），避免整包导入失败。
- **LLM 网关打通**：主/标注共用 `INTERNAL_LLM_GATEWAY_TOKEN`，标注侧大模型推荐可用。
- **6 镜像精简包**：移除训练镜像；整包 927M 单分片 <1G。
