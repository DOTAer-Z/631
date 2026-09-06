from pathlib import Path

import yaml


CHANGE_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = CHANGE_ROOT / "docker-compose.yml"
K8S_ROOT = CHANGE_ROOT / "k8s-deploy/k8s"


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


def test_compose_routes_annotation_llm_only_through_main_gateway():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    main_env = _environment(compose["services"]["backend"])
    annotation_env = _environment(compose["services"]["backend-annotate"])

    assert main_env["INTERNAL_LLM_GATEWAY_TOKEN"] == (
        "${INTERNAL_LLM_GATEWAY_TOKEN:-}"
    )
    assert {
        name: main_env[name]
        for name in (
            "ENABLE_LLM",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_TIMEOUT",
            "LLM_MAX_OUTPUT_TOKENS",
        )
    } == {
        "ENABLE_LLM": "${ENABLE_LLM:-False}",
        "LLM_BASE_URL": "${LLM_BASE_URL:-}",
        "LLM_API_KEY": "${LLM_API_KEY:-}",
        "LLM_MODEL": "${LLM_MODEL:-}",
        "LLM_TIMEOUT": "${LLM_TIMEOUT:-60}",
        "LLM_MAX_OUTPUT_TOKENS": "${LLM_MAX_OUTPUT_TOKENS:-1024}",
    }
    assert annotation_env["LLM_GATEWAY_URL"] == (
        "${LLM_GATEWAY_URL:-http://backend:8000/api/v1/internal/llm/chat}"
    )
    assert annotation_env["INTERNAL_LLM_GATEWAY_TOKEN"] == (
        "${INTERNAL_LLM_GATEWAY_TOKEN:-}"
    )
    assert annotation_env["LLM_GATEWAY_TIMEOUT_SECONDS"] == (
        "${LLM_GATEWAY_TIMEOUT_SECONDS:-180}"
    )
    assert annotation_env["MAIN_SYSTEM_BACKEND_URL"] == (
        "${MAIN_SYSTEM_BACKEND_URL:-http://backend:8000/api/v1}"
    )
    assert annotation_env["NO_PROXY"] == (
        "${NO_PROXY:-backend,localhost,127.0.0.1}"
    )
    assert annotation_env["no_proxy"] == (
        "${NO_PROXY:-backend,localhost,127.0.0.1}"
    )
    assert {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "MODEL_API_ENCRYPTION_KEY",
    }.isdisjoint(annotation_env)


def test_k8s_injects_one_shared_service_token_without_provider_secrets():
    main = _env_names(
        _deployment(K8S_ROOT / "04-backend.yaml", "faultdiag-backend")
    )
    annotation = _env_names(
        _deployment(
            K8S_ROOT / "05-backend-annotate.yaml",
            "faultdiag-backend-annotate",
        )
    )
    assert main["INTERNAL_LLM_GATEWAY_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "faultdiag-secret",
        "key": "INTERNAL_LLM_GATEWAY_TOKEN",
    }
    assert annotation["INTERNAL_LLM_GATEWAY_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "faultdiag-secret",
        "key": "INTERNAL_LLM_GATEWAY_TOKEN",
    }
    assert annotation["LLM_GATEWAY_URL"]["valueFrom"]["configMapKeyRef"] == {
        "name": "faultdiag-config",
        "key": "LLM_GATEWAY_URL",
    }
    assert annotation["LLM_GATEWAY_TIMEOUT_SECONDS"]["valueFrom"][
        "configMapKeyRef"
    ] == {
        "name": "faultdiag-config",
        "key": "LLM_GATEWAY_TIMEOUT_SECONDS",
    }
    assert annotation["MAIN_SYSTEM_BACKEND_URL"]["value"] == (
        "http://backend:8000/api/v1"
    )
    assert annotation["NO_PROXY"]["value"] == "backend,localhost,127.0.0.1"
    assert annotation["no_proxy"]["value"] == "backend,localhost,127.0.0.1"
    assert {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "MODEL_API_ENCRYPTION_KEY",
    }.isdisjoint(annotation)


def test_k8s_config_and_secret_declare_exact_gateway_settings():
    config_path = K8S_ROOT / "01-config.yaml"
    config = _object(config_path, "ConfigMap", "faultdiag-config")
    secret = _object(config_path, "Secret", "faultdiag-secret")

    assert config["data"]["LLM_GATEWAY_URL"] == (
        "http://backend:8000/api/v1/internal/llm/chat"
    )
    assert config["data"]["LLM_GATEWAY_TIMEOUT_SECONDS"] == "180"
    assert secret["stringData"]["INTERNAL_LLM_GATEWAY_TOKEN"] == ""
