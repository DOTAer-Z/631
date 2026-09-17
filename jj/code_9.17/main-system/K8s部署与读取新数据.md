# K8s 部署 & 后端读取新数据 —— 操作文档

> 适用于已迁移为 **PostgreSQL 单库** 的故障诊断平台。
> 系统是**三个独立镜像**：`faultdiag-backend`、`faultdiag-frontend`、`postgres:16`
>（仅为搬运方便打进同一个 tar.gz，运行时是三个容器/三个 Deployment）。
> 更新时间：2026-06-06

---

# 一、把镜像推到 Kubernetes 并部署

## 0. 前置
- 一套可用的 K8s 集群 + `kubectl`
- 一个镜像仓库（私有/公有均可）。下面以旧文档里的私有仓库为例：`easzlab.io.local:5000`
- 已经有三个镜像（本机 `docker images` 能看到 `faultdiag-backend:latest`、`faultdiag-frontend:latest`、`postgres:16`）。
  - 若是从离线包来：先 `cd dist && sh load-images.sh` 加载。

## 1. 变量
```sh
REGISTRY=easzlab.io.local:5000
NS=faultdiagnosis
TAG=release-2026-06-06
```

## 2. 打 tag
```sh
docker tag faultdiag-backend:latest  $REGISTRY/faultdiag-backend:$TAG
docker tag faultdiag-frontend:latest $REGISTRY/faultdiag-frontend:$TAG
docker tag postgres:16               $REGISTRY/postgres:16            # 可选：内网无法拉官方镜像时也推一份
```

## 3. 登录并推送
```sh
docker login $REGISTRY
docker push $REGISTRY/faultdiag-backend:$TAG
docker push $REGISTRY/faultdiag-frontend:$TAG
docker push $REGISTRY/postgres:16          # 可选
```

## 4. 把清单里的镜像地址替换成你的仓库/标签
清单在 `deploy/k8s/`。批量替换：
```sh
cd deploy/k8s
sed -i "s|easzlab.io.local:5000/faultdiag-backend:.*|$REGISTRY/faultdiag-backend:$TAG|g"  02-backend.yaml
sed -i "s|easzlab.io.local:5000/faultdiag-frontend:.*|$REGISTRY/faultdiag-frontend:$TAG|g" 03-frontend.yaml
# 如果 postgres 也推到了私有仓库：
# sed -i "s|image: postgres:16|image: $REGISTRY/postgres:16|g" 01-postgres.yaml
grep -n "image:" *.yaml      # 核对替换结果
```

## 5. 应用（apply）
```sh
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-postgres.yaml
kubectl apply -f 02-backend.yaml
kubectl apply -f 03-frontend.yaml
# 或一次性：kubectl apply -f .
```

## 6. 看状态 / 滚动
```sh
kubectl get pods -n $NS -o wide
kubectl rollout status deployment/postgres          -n $NS
kubectl rollout status deployment/faultdiag-backend -n $NS
kubectl rollout status deployment/faultdiag-frontend -n $NS

# 看后端初始化日志（建表 → seed → 导入内置数据集）
kubectl logs -n $NS deploy/faultdiag-backend -f      # 看到 "[ingest] 完成：cases=79 ..." 即就绪
```

## 7. 访问
- 前端 UI：`http://<任意节点IP>:30080/`（frontend Service 是 NodePort 30080）
- 后端 Swagger（如需对外，给 backend 也加个 NodePort，或用 `kubectl port-forward`）：
  ```sh
  kubectl port-forward -n $NS svc/backend 5000:8000
  # 然后本地 http://localhost:5000/api/docs
  ```

## 8. 更新镜像（发新版本时）
```sh
docker tag faultdiag-backend:latest $REGISTRY/faultdiag-backend:$TAG && docker push $REGISTRY/faultdiag-backend:$TAG
sed -i "s|image: .*faultdiag-backend:.*|image: $REGISTRY/faultdiag-backend:$TAG|g" 02-backend.yaml
kubectl apply -n $NS -f 02-backend.yaml
kubectl rollout restart deployment/faultdiag-backend -n $NS
kubectl rollout status  deployment/faultdiag-backend -n $NS
```

## 关键说明
- **backend 的 Service 必须叫 `backend`、端口 8000**——前端 nginx 写死了 `proxy_pass http://backend:8000`，改名会导致前端调不到接口。
- **数据库密码**在 `01-postgres.yaml` 的 Secret `faultdiag-db` 里（`POSTGRES_PASSWORD`），正式环境务必改强密码；backend 通过 `envFrom: secretRef` 自动读到同一套凭据。
- **PVC**：`faultdiag-pgdata` 默认用集群默认 StorageClass。若集群没有默认 SC，去 `01-postgres.yaml` 里取消注释并填 `storageClassName`。
- **数据初始化**：backend 镜像入口会在启动时自动 建表→seed→导入内置数据集（79 个 Test），所以 `replicas: 1`。如果要扩多副本，建议把初始化抽成一次性 `Job`（跑 `create_tables.py + seed.py + ingest_dataset.py`），backend 容器只跑 `uvicorn`，避免多副本并发初始化。

---

# 二、让后端读取/分析新数据

> 原理：把包含若干 `Test_***/` 子目录的文件夹放进后端容器，再运行内置脚本
> `ingest_dataset.py`（幂等：同名 Test 重复跑只覆盖更新、不翻倍）。导入完成后刷新前端「数据处理 → 日志列表」即可看到。
>
> 每个 `Test_***` 会自动按 `fault_events.log` 判定 round_1=故障轮 / round_2=正常轮，
> 解析出故障类型、错误事件、日志窗口；正常轮 0 报错作为对照。

## 场景 A：docker compose 部署（dist 包方式）

```sh
cd dist                       # docker-compose.yaml 所在目录

# 1) 容器内建接收目录
docker compose exec backend mkdir -p /app/incoming

# 2) 把宿主机的新数据拷进去（目录里应是 Test_xxxx 子文件夹）
docker cp ./新数据/. faultdiag-backend:/app/incoming/

# 3) 导入分析
docker compose exec backend python ingest_dataset.py --data-root /app/incoming
```

只想重导内置数据集里的某个 Test：
```sh
docker compose exec backend python ingest_dataset.py --only Test_1001
```

Windows/WSL 下数据在 Windows 盘：
```sh
cp -r /mnt/c/Users/你的用户名/Desktop/新数据 ~/new_tests
docker cp ~/new_tests/. faultdiag-backend:/app/incoming/
docker compose exec backend python ingest_dataset.py --data-root /app/incoming
```

## 场景 B：Kubernetes 部署

```sh
NS=faultdiagnosis
# 取后端 Pod 名
POD=$(kubectl get pod -n $NS -l app=faultdiag-backend -o jsonpath='{.items[0].metadata.name}')

# 1) Pod 内建接收目录
kubectl exec -n $NS $POD -- mkdir -p /app/incoming

# 2) 把本地新数据拷进 Pod（目录里应是 Test_xxxx 子文件夹）
kubectl cp ./新数据/. $NS/$POD:/app/incoming/

# 3) 导入分析
kubectl exec -n $NS $POD -- python ingest_dataset.py --data-root /app/incoming
```

只重导某个 Test：
```sh
kubectl exec -n $NS $POD -- python ingest_dataset.py --only Test_1001
```

## 让新数据“永久内置”进镜像（可选）
把数据放进项目 `data/` 后重建后端镜像并重新推送/滚动更新：
```sh
cd deploy && docker compose -f docker-compose.prod.yaml build backend
# 然后按「一、8. 更新镜像」推送 + rollout
```

## 验证
- docker compose：`curl http://localhost:5000/api/v1/log-analysis/logs?page=1&page_size=5`
- K8s：`kubectl port-forward -n $NS svc/backend 5000:8000` 后同上
- 或直接刷新前端「数据处理 → 日志列表」，按 `test_name` / 故障·正常 筛选查看新 run。

---

## 附：清单文件清单（`deploy/k8s/`）
| 文件 | 内容 |
|---|---|
| `00-namespace.yaml` | 命名空间 `faultdiagnosis` |
| `01-postgres.yaml` | DB 密码 Secret + PVC + Deployment + Service |
| `02-backend.yaml` | 后端 Deployment + Service（名为 `backend:8000`） |
| `03-frontend.yaml` | 前端 Deployment + Service（NodePort 30080） |
