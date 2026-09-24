from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"
WORKSPACE_ROOT = COMPOSE_PATH.parents[1]
TRAINING_DOCKERFILE = (
    WORKSPACE_ROOT / "631change/main-system/backend/Dockerfile.training"
)
TRAINING_ENTRYPOINT = (
    WORKSPACE_ROOT / "631change/main-system/backend/training-entrypoint.sh"
)
TRAINING_DOCKERIGNORE = (
    WORKSPACE_ROOT
    / "631change/main-system/backend/Dockerfile.training.dockerignore"
)
MODEL_REQUIREMENTS = WORKSPACE_ROOT / "model_train/requirements.txt"


def _compose():
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _environment(service):
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return environment
    return {
        item.split("=", 1)[0]: item.split("=", 1)[1] if "=" in item else None
        for item in environment
    }


def test_training_worker_has_isolated_gpu_packaging_and_persistent_mounts():
    compose = _compose()
    worker = compose["services"]["training-worker"]

    assert worker["build"] == {
        "context": "..",
        "dockerfile": "631change/main-system/backend/Dockerfile.training",
    }
    assert worker["command"] == ["python", "-m", "app.training_worker.main"]
    assert worker["restart"] == "unless-stopped"
    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert not worker.get("ports")
    assert "training_outputs:/training/outputs" in worker["volumes"]
    assert (
        "${TRAINING_BASE_MODEL_HOST_PATH:-../models/Qwen3.5-9B}:"
        "/models/Qwen3.5-9B:ro"
    ) in worker["volumes"]
    assert "training_outputs" in compose["volumes"]

    devices = worker["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "device_ids": ["1"], "capabilities": ["gpu"]}]
    environment = _environment(worker)
    assert environment["NVIDIA_VISIBLE_DEVICES"] == "1"
    assert environment["CUDA_VISIBLE_DEVICES"] == "0"

    assert TRAINING_DOCKERFILE.is_file()
    assert TRAINING_ENTRYPOINT.is_file()


def test_training_image_copies_package_source_without_local_models_or_secrets():
    dockerfile = TRAINING_DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM pytorch/pytorch:2.9.0-cuda12.8-cudnn9-runtime" in dockerfile
    assert "COPY model_train/requirements.txt" in dockerfile
    assert "COPY model_train/pyproject.toml /opt/model_train/pyproject.toml" in dockerfile
    assert "COPY model_train/src/ /opt/model_train/src/" in dockerfile
    assert "COPY model_train/ /opt/model_train/" not in dockerfile
    assert "COPY 631change/main-system/backend/app/ /app/app/" in dockerfile
    assert "COPY 631change/main-system/backend/ /app/" not in dockerfile
    assert "COPY .env" not in dockerfile
    assert "COPY models/" not in dockerfile


def test_training_model_stack_versions_are_reproducible_for_cpu_and_cuda():
    requirements = MODEL_REQUIREMENTS.read_text(encoding="utf-8").splitlines()

    assert requirements.count("torch==2.9.0") == 1
    assert requirements.count("bitsandbytes==0.48.2") == 1
    assert not any(line.startswith("bitsandbytes>=") for line in requirements)
    assert "transformers>=5.12.1,<6.0" in requirements
    assert "peft==0.18.1" in requirements


def test_training_image_filters_torch_and_extra_index_before_model_install():
    dockerfile = TRAINING_DOCKERFILE.read_text(encoding="utf-8")

    assert "sed '/^torch==/d; /^--extra-index-url/d'" in dockerfile
    assert "> /tmp/model-training-runtime.txt" in dockerfile
    assert "pip install --no-cache-dir -r /tmp/model-training-runtime.txt" in dockerfile
    assert "pip install --no-cache-dir -r /tmp/model-training-requirements.txt" not in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile


def test_training_build_context_is_allowlisted_and_excludes_local_artifacts():
    rules = TRAINING_DOCKERIGNORE.read_text(encoding="utf-8").splitlines()

    assert rules[0] == "**"
    assert "!model_train/requirements.txt" in rules
    assert "!model_train/pyproject.toml" in rules
    assert "!model_train/src/**" in rules
    assert "!631change/main-system/backend/app/**" in rules
    assert "!631change/main-system/backend/training-entrypoint.sh" in rules
    assert all(".env" not in rule for rule in rules if rule.startswith("!"))
    assert all("adapter" not in rule.lower() for rule in rules if rule.startswith("!"))
    assert all("models" not in rule.lower() for rule in rules if rule.startswith("!"))
    assert "**/__pycache__/" in rules
    assert "**/*.py[cod]" in rules


def test_training_worker_receives_only_database_and_training_configuration():
    worker_environment = _environment(_compose()["services"]["training-worker"])

    assert {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    }.issubset(worker_environment)
    forbidden = {
        "DATABASE_URL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "MODEL_API_ENCRYPTION_KEY",
        "ANNOTATE_BACKEND_URL",
        "MAIN_SYSTEM_BACKEND_URL",
        "ANNOTATE_DB",
        "ANNOTATE_USER",
        "ANNOTATE_PASSWORD",
    }
    assert forbidden.isdisjoint(worker_environment)


def test_api_backend_shares_training_outputs_without_gpu_or_model_mount():
    backend = _compose()["services"]["backend"]
    reservations = backend.get("deploy", {}).get("resources", {}).get("reservations", {})

    assert not reservations.get("devices")
    assert not backend.get("gpus")
    mounts = backend.get("volumes", [])
    assert "training_outputs:/training/outputs" in mounts
    assert all("Qwen3.5-9B" not in mount for mount in mounts)
    environment = _environment(backend)
    assert environment["TRAINING_OUTPUT_ROOT"] == "/training/outputs"
    assert "NVIDIA_VISIBLE_DEVICES" not in environment
    assert "CUDA_VISIBLE_DEVICES" not in environment
