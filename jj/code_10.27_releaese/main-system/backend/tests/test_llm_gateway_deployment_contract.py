"""Deployment contracts for the zero-YAML LLM design.

历史:旧版要求运营方手工生成 INTERNAL_LLM_GATEWAY_TOKEN 与 MODEL_API_ENCRYPTION_KEY 填入
k8s Secret——这对只会用页面的工作人员是硬门槛。本测试锁定 9.8 的真实交付拓扑
(`631_9.8/docker-compose.yml` + `631_9.8depoly/k8s/`)中的新契约:

- 网关在集群内按服务名直连(LLM_GATEWAY_URL 默认指向 backend:8000,不经对外 NodePort);
- 标注后端不再要求共享 token / 任何 provider 凭据(LLM_BASE_URL/API_KEY/MODEL 全都不注);
- 主系统 LLM 只经「模型 API 管理」页面(DB model_api_configs 行)配置;
- 加密 key / 网关 token 均不要求手工填(yaml 留空即自动生成/不再校验)。
"""
import os
from pathlib import Path

import yaml

# 定位 631_9.8 部署根（含 docker-compose.yml、631_9.8depoly/k8s、main-system/frontend/nginx）。
# 兼容宿主机目录树与 CI/容器内把整个快照拷到任意位置的两种布局。也可用环境变量 FACTS_TEST_ROOT 显式指定。
def _find_root() -> Path:
    override = Path(os.environ.get("FACTS_TEST_ROOT") or "").resolve()
    if override and (override / "docker-compose.yml").exists():
        return override
    for current in [Path(__file__).resolve().parents[3], Path(__file__).resolve().parents[2], Path.cwd()]:
        if (current / "docker-compose.yml").exists():
            return current
    raise RuntimeError("cannot locate 631_9.8 deployment root; set FACTS_TEST_ROOT")


CHANGE_ROOT = _find_root()
COMPOSE_PATH = CHANGE_ROOT / "docker-compose.yml"
K8S_ROOT = CHANGE_ROOT / "631_9.8depoly/k8s"
NGINX_PATH = CHANGE_ROOT / "main-system/frontend/nginx/default.conf"


def _environment(service):
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return environment
    return {
        item.split("=", 1)[0]: item.split("=", 1)[1] if "=" in item else None
        for item in environment
    }


def _object(path, kind, name):
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    return next(
        document
        for document in documents
        if document
        and document.get("kind") == kind
        and document["metadata"]["name"] == name
    )


def _deployment(path, name):
    return _object(path, "Deployment", name)


def _env_names(deployment):
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {entry["name"]: entry for entry in container["env"]}


def test_compose_routes_annotation_llm_only_through_internal_gateway():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    main_env = _environment(compose["services"]["backend"])
    annotation_env = _environment(compose["services"]["backend-annotate"])

    # 网关地址:集群内服务名直连,天然不经对外 NodePort —— 无需运营方填写
    assert annotation_env["LLM_GATEWAY_URL"] == (
        "${LLM_GATEWAY_URL:-http://backend:8000/api/v1/internal/llm/chat}"
    )
    # 标注后端不再注入共享 token / provider 凭据
    assert "INTERNAL_LLM_GATEWAY_TOKEN" not in annotation_env
    assert {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "MODEL_API_ENCRYPTION_KEY",
    }.isdisjoint(annotation_env)


def test_k8s_annotation_llm_uses_only_internal_gateway_without_provider_secrets():
    annotation = _env_names(
        _deployment(
            K8S_ROOT / "04-backend-annotate.yaml",
            "faultdiag-backend-annotate",
        )
    )
    # 网关地址从共享 ConfigMap 注入(默认即内部服务名,无需手改)
    assert annotation["LLM_GATEWAY_URL"]["valueFrom"]["configMapKeyRef"] == {
        "name": "faultdiag-config",
        "key": "LLM_GATEWAY_URL",
    }
    # 零-yaml:标注不要求共享 token,也没有任何 provider 凭据注入
    assert "INTERNAL_LLM_GATEWAY_TOKEN" not in annotation
    assert {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "MODEL_API_ENCRYPTION_KEY",
    }.isdisjoint(annotation)
    assert annotation["LLM_GATEWAY_TIMEOUT_SECONDS"]["valueFrom"][
        "configMapKeyRef"
    ] == {"name": "faultdiag-config", "key": "LLM_GATEWAY_TIMEOUT_SECONDS"}


def test_k8s_main_backend_llm_is_page_configured_and_encryption_is_self_managed():
    main = _env_names(
        _deployment(K8S_ROOT / "03-backend.yaml", "faultdiag-backend")
    )
    config = _object(K8S_ROOT / "01-config.yaml", "ConfigMap", "faultdiag-config")
    secret = _object(K8S_ROOT / "01-config.yaml", "Secret", "faultdiag-secret")

    # 主系统 LLM provider 由「模型 API 管理」页配置(DB model_api_configs active 行优先),
    # 环境回退关闭即可 —— 因此 ConfigMap/Secret 全部留空
    assert config["data"]["ENABLE_LLM"] == "False"
    for key in ("LLM_BASE_URL", "LLM_MODEL"):
        assert config["data"].get(key) in (None, "")
    assert secret["stringData"].get("LLM_API_KEY") in (None, "")
    # 加密 key / 网关 token 均不要求手工填写
    assert secret["stringData"].get("MODEL_API_ENCRYPTION_KEY") in (None, "")
    assert secret["stringData"].get("INTERNAL_LLM_GATEWAY_TOKEN") in (None, "")


def test_nginx_blocks_external_access_to_internal_gateway():
    config = NGINX_PATH.read_text(encoding="utf-8")
    # 外部(浏览器/门户)绝不能到达集群内 LLM 网关 —— 与去掉共享 token 是配套的
    assert "location /api/v1/internal/ {" in config
    assert "return 404;" in config
