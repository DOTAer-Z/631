import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml


CHANGE_ROOT = Path(__file__).resolve().parents[3]
K8S_ROOT = CHANGE_ROOT / "k8s-deploy/k8s"
SCRIPT_ROOT = CHANGE_ROOT / "k8s-deploy/scripts"
CONFIG_PATH = K8S_ROOT / "01-config.yaml"
BACKEND_PATH = K8S_ROOT / "04-backend.yaml"
GPU_PATH = K8S_ROOT / "08-training-worker-gpu.yaml"
CPU_PATH = K8S_ROOT / "09-training-worker-cpu.yaml"
LEGACY_PATH = K8S_ROOT / "08-training-worker.yaml"
SELECTOR_PATH = SCRIPT_ROOT / "select-training-worker.sh"
SEED_PATH = SCRIPT_ROOT / "seed-base-model-pvc.sh"


def _documents(path: Path) -> list[dict]:
    return [
        document
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if document is not None
    ]


def _document(path: Path, kind: str, name: str) -> dict:
    return next(
        document
        for document in _documents(path)
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    )


def _worker(path: Path) -> tuple[dict, dict, dict]:
    deployment = _document(path, "Deployment", "faultdiag-training-worker")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    return deployment, pod_spec, container


def _environment(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container["env"]}


def _claims(pod_spec: dict) -> dict[str, str]:
    return {
        volume["name"]: volume["persistentVolumeClaim"]["claimName"]
        for volume in pod_spec["volumes"]
    }


def _mounts(container: dict) -> dict[str, dict]:
    return {mount["name"]: mount for mount in container["volumeMounts"]}


def _selector_source() -> str:
    return SELECTOR_PATH.read_text(encoding="utf-8")


def _seed_source() -> str:
    return SEED_PATH.read_text(encoding="utf-8")


def _seed_embedded_python(name: str) -> str:
    match = re.search(
        rf"^{name}='\n(?P<source>.*?)\n'$",
        _seed_source(),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing embedded seed program: {name}"
    return match.group("source")


def _write_synthetic_qwen_model(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "config.json").write_text('{"model_type":"qwen"}\n', encoding="utf-8")
    (root / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    (root / "tokenizer_config.json").write_text("{}\n", encoding="utf-8")
    (root / "processor_config.json").write_text("{}\n", encoding="utf-8")
    (root / "model-00001-of-00002.safetensors").write_bytes(b"first-shard")
    (root / "model-00002-of-00002.safetensors").write_bytes(b"second-shard")
    (root / "model.safetensors.index.json").write_text(
        json.dumps({
            "weight_map": {
                "layer.0": "model-00001-of-00002.safetensors",
                "layer.1": "model-00002-of-00002.safetensors",
            }
        }),
        encoding="utf-8",
    )


def _run_seed_program(source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update({"SOURCE_ROOT": str(source), "TARGET_ROOT": str(target)})
    return subprocess.run(
        [sys.executable, "-B", "-c", _seed_embedded_python("SEED_PROGRAM")],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def _embedded_python(name: str) -> str:
    match = re.search(
        rf"^{name}='\n(?P<source>.*?)\n'$",
        _selector_source(),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing embedded evaluator: {name}"
    return match.group("source")


def _evaluate(name: str, payload: dict, *arguments: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _embedded_python(name), *arguments],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def test_base_model_pvc_and_non_secret_device_defaults_are_explicit():
    pvc = _document(BACKEND_PATH, "PersistentVolumeClaim", "faultdiag-base-model")
    assert pvc["metadata"]["namespace"] == "faultdiag-integrated"
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]
    assert pvc["spec"]["resources"]["requests"]["storage"] == "40Gi"
    assert pvc["spec"]["storageClassName"] == "nfs-client"

    training = _document(
        BACKEND_PATH, "PersistentVolumeClaim", "faultdiag-training-outputs"
    )
    assert training["spec"]["accessModes"] == ["ReadWriteMany"]
    assert training["spec"]["resources"]["requests"]["storage"] == "50Gi"

    config = _document(CONFIG_PATH, "ConfigMap", "faultdiag-config")["data"]
    assert config["TRAINING_DEVICE"] == "auto"
    assert config["TRAINING_CPU_PROFILE"] == "auto_low_memory"
    secret = _document(CONFIG_PATH, "Secret", "faultdiag-secret")["stringData"]
    assert "TRAINING_DEVICE" not in secret
    assert "TRAINING_CPU_PROFILE" not in secret


def test_gpu_and_cpu_workers_share_identity_image_claims_and_serial_strategy():
    gpu, gpu_pod, gpu_container = _worker(GPU_PATH)
    cpu, cpu_pod, cpu_container = _worker(CPU_PATH)

    for deployment in (gpu, cpu):
        assert deployment["metadata"] == {
            "name": "faultdiag-training-worker",
            "namespace": "faultdiag-integrated",
        }
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        assert deployment["spec"]["selector"] == {
            "matchLabels": {"app": "faultdiag-training-worker"}
        }

    assert gpu_container["image"] == cpu_container["image"]
    assert gpu_container["image"] == (
        "REGISTRY_PLACEHOLDER/faultdiag-training-worker:latest"
    )
    assert gpu_container["command"] == cpu_container["command"] == [
        "python",
        "-m",
        "app.training_worker.main",
    ]
    assert _claims(gpu_pod) == _claims(cpu_pod) == {
        "training": "faultdiag-training-outputs",
        "basemodel": "faultdiag-base-model",
    }
    for container in (gpu_container, cpu_container):
        mounts = _mounts(container)
        assert mounts["training"] == {
            "name": "training",
            "mountPath": "/training/outputs",
        }
        assert mounts["basemodel"] == {
            "name": "basemodel",
            "mountPath": "/models/Qwen3.5-9B",
            "readOnly": True,
        }

    assert "hostPath" not in GPU_PATH.read_text(encoding="utf-8")
    assert "hostPath" not in CPU_PATH.read_text(encoding="utf-8")


def test_gpu_worker_alone_has_nvidia_resources_selectors_and_tolerations():
    _gpu, gpu_pod, gpu_container = _worker(GPU_PATH)
    _cpu, cpu_pod, cpu_container = _worker(CPU_PATH)

    assert gpu_container["resources"] == {"limits": {"nvidia.com/gpu": 1}}
    assert gpu_pod["nodeSelector"] == {"nvidia.com/gpu.present": "true"}
    assert gpu_pod["tolerations"] == [
        {
            "key": "nvidia.com/gpu",
            "operator": "Equal",
            "value": "present",
            "effect": "NoSchedule",
        }
    ]
    assert _environment(gpu_container)["TRAINING_DEVICE"]["value"] == "auto"

    assert "resources" not in cpu_container
    assert "nodeSelector" not in cpu_pod
    assert "tolerations" not in cpu_pod
    assert _environment(cpu_container)["TRAINING_DEVICE"]["value"] == "cpu"
    cpu_text = CPU_PATH.read_text(encoding="utf-8").lower()
    assert "nvidia" not in cpu_text
    assert "nvidia.com/gpu" not in cpu_text


def test_replacement_manifests_exist_only_under_distinct_filenames():
    assert GPU_PATH.is_file()
    assert CPU_PATH.is_file()
    assert not LEGACY_PATH.exists()


def test_selector_is_idle_only_bounded_and_fallbacks_only_for_gpu_capacity():
    script = _selector_source()
    assert script.startswith("#!/bin/sh\nset -eu\n")
    assert 'ROLLOUT_TIMEOUT_SECONDS="${ROLLOUT_TIMEOUT_SECONDS:-300}"' in script
    assert "faultdiag-base-model" in script
    assert "faultdiag-training-outputs" in script
    assert "deployment/faultdiag-backend" in script
    assert "deployment/postgres" in script
    assert "training_tasks" in script
    assert "current_task_id" in script
    assert "kubectl get nodes -o json" in script

    first_apply = script.index("kubectl apply")
    for read_check in (
        "kubectl get pvc",
        "deployment/faultdiag-backend",
        "deployment/postgres",
        "kubectl get nodes -o json",
    ):
        assert script.index(read_check) < first_apply

    assert 'rollout status "deployment/${DEPLOYMENT_NAME}"' in script
    assert '--timeout="${ROLLOUT_TIMEOUT_SECONDS}s"' in script
    assert r"Insufficient nvidia\.com/gpu" in script
    assert 'fallback_reason = "gpu-capacity"' in script
    assert 'if [ "$fallback_reason" != "gpu-capacity" ]; then' in script
    assert 'kubectl apply -f "$CPU_MANIFEST"' in script


def test_selector_rejects_a_namespace_override_before_calling_kubectl(tmp_path):
    marker = tmp_path / "kubectl-called"
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        '#!/bin/sh\n: > "$MOCK_KUBECTL_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    kubectl.chmod(0o700)
    environment = dict(os.environ)
    environment.update({
        "NAMESPACE": "other-namespace",
        "MOCK_KUBECTL_MARKER": str(marker),
        "PATH": f"{tmp_path}:{environment['PATH']}",
    })

    completed = subprocess.run(
        [str(SELECTOR_PATH)],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert completed.returncode != 0
    assert "namespace" in completed.stderr.lower()
    assert not marker.exists()


def test_gpu_discovery_requires_capacity_readiness_schedulability_and_selector():
    def node(*, ready=True, unschedulable=False, gpu="1", label="true"):
        return {
            "metadata": {
                "labels": {"nvidia.com/gpu.present": label}
                if label is not None
                else {}
            },
            "spec": {"unschedulable": unschedulable},
            "status": {
                "allocatable": {"nvidia.com/gpu": gpu},
                "conditions": [
                    {"type": "Ready", "status": "True" if ready else "False"}
                ],
            },
        }

    assert _evaluate("NODE_EVALUATOR", {"items": [node()]}) == "yes"
    for rejected in (
        node(ready=False),
        node(unschedulable=True),
        node(gpu="0"),
        node(label=None),
        node(label="false"),
    ):
        assert _evaluate("NODE_EVALUATOR", {"items": [rejected]}) == "no"


def test_failed_rollout_resolves_the_exact_deployment_revision_and_owned_rs_identity():
    deployment = {
        "metadata": {
            "uid": "deployment-uid",
            "annotations": {"deployment.kubernetes.io/revision": "7"},
        }
    }
    assert (
        _evaluate("DEPLOYMENT_IDENTITY_EVALUATOR", deployment)
        == "deployment-uid|7"
    )
    replica_sets = {
        "items": [
            {
                "metadata": {
                    "uid": "stale-rs-uid",
                    "annotations": {"deployment.kubernetes.io/revision": "6"},
                    "ownerReferences": [
                            {
                                "uid": "deployment-uid",
                                "kind": "Deployment",
                                "controller": True,
                            }
                    ],
                },
                "spec": {"template": {"metadata": {"labels": {
                    "pod-template-hash": "stale-hash"
                }}}},
            },
            {
                "metadata": {
                    "uid": "foreign-rs-uid",
                    "annotations": {"deployment.kubernetes.io/revision": "7"},
                    "ownerReferences": [
                            {
                                "uid": "different-deployment",
                                "kind": "Deployment",
                                "controller": True,
                            }
                    ],
                },
                "spec": {"template": {"metadata": {"labels": {
                    "pod-template-hash": "foreign-hash"
                }}}},
            },
            {
                "metadata": {
                    "uid": "current-rs-uid",
                    "annotations": {"deployment.kubernetes.io/revision": "7"},
                    "ownerReferences": [
                            {
                                "uid": "deployment-uid",
                                "kind": "Deployment",
                                "controller": True,
                            }
                    ],
                },
                "spec": {"template": {"metadata": {"labels": {
                    "pod-template-hash": "current-hash"
                }}}},
            },
        ]
    }
    assert (
        _evaluate(
            "REPLICA_SET_HASH_EVALUATOR",
            replica_sets,
            "deployment-uid",
            "7",
        )
        == "current-rs-uid|current-hash"
    )
    assert (
        '-l "app=faultdiag-training-worker,pod-template-hash=${current_hash}"'
        in _selector_source()
    )


def test_fallback_evaluator_rejects_non_gpu_and_mixed_current_pod_failures():
    current_rs_uid = "current-rs-uid"
    current_hash = "current-hash"
    gpu_condition = {
        "type": "PodScheduled",
        "status": "False",
        "reason": "Unschedulable",
        "message": "0/2 nodes are available: 2 Insufficient nvidia.com/gpu.",
    }

    def pod(
        condition,
        waiting_reason=None,
        *,
        owner_uid=current_rs_uid,
        owner_kind="ReplicaSet",
        owner_controller=True,
        pod_hash=current_hash,
        deletion_timestamp=None,
        phase="Pending",
    ):
        status = {"conditions": [condition], "phase": phase}
        if waiting_reason is not None:
            status["containerStatuses"] = [
                {"state": {"waiting": {"reason": waiting_reason}}}
            ]
        metadata = {
            "labels": {"pod-template-hash": pod_hash},
            "ownerReferences": [{
                "uid": owner_uid,
                "kind": owner_kind,
                "controller": owner_controller,
            }],
        }
        if deletion_timestamp is not None:
            metadata["deletionTimestamp"] = deletion_timestamp
        return {"metadata": metadata, "status": status}

    assert (
        _evaluate(
            "CURRENT_POD_EVALUATOR",
            {"items": [pod(gpu_condition)]},
            current_rs_uid,
            current_hash,
        )
        == "gpu-capacity"
    )
    preemption_condition = {
        **gpu_condition,
        "message": (
            "0/2 nodes are available: 2 Insufficient nvidia.com/gpu. "
            "preemption: 0/2 nodes are available: 2 No preemption victims "
            "found for incoming pod."
        ),
    }
    assert _evaluate(
        "CURRENT_POD_EVALUATOR",
        {"items": [pod(preemption_condition)]},
        current_rs_uid,
        current_hash,
    ) == "gpu-capacity"
    blocked_conditions = (
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 2 Insufficient nvidia.com/gpu, "
                "pod has unbound immediate PersistentVolumeClaims."
            ),
        },
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 2 Insufficient nvidia.com/gpu, "
                "2 Insufficient cpu."
            ),
        },
        {
            **gpu_condition,
            "message": "0/2 nodes did not match Pod node affinity/selector.",
        },
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 1 Insufficient nvidia.com/gpu, "
                "1 Too many pods."
            ),
        },
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 1 Insufficient nvidia.com/gpu, "
                "1 node(s) had topology conflict."
            ),
        },
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 1 Insufficient nvidia.com/gpu, "
                "1 node(s) had untolerated taint."
            ),
        },
        {
            **gpu_condition,
            "message": (
                "0/2 nodes are available: 1 Insufficient nvidia.com/gpu, "
                "1 unknown scheduler reason."
            ),
        },
    )
    for condition in blocked_conditions:
        assert _evaluate(
            "CURRENT_POD_EVALUATOR",
            {"items": [pod(condition)]},
            current_rs_uid,
            current_hash,
        ) == ""
    for reason in ("ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"):
        assert _evaluate(
            "CURRENT_POD_EVALUATOR",
            {"items": [pod(gpu_condition), pod(gpu_condition, reason)]},
            current_rs_uid,
            current_hash,
        ) == ""
    scheduled = {"type": "PodScheduled", "status": "True"}
    assert _evaluate(
        "CURRENT_POD_EVALUATOR",
        {"items": [pod(gpu_condition), pod(scheduled)]},
        current_rs_uid,
        current_hash,
    ) == ""
    assert _evaluate(
        "CURRENT_POD_EVALUATOR", {"items": []}, current_rs_uid, current_hash
    ) == ""


def test_fallback_requires_current_owned_live_pending_pods_only():
    current_rs_uid = "current-rs-uid"
    current_hash = "current-hash"
    gpu_condition = {
        "type": "PodScheduled",
        "status": "False",
        "reason": "Unschedulable",
        "message": "0/1 nodes are available: 1 Insufficient nvidia.com/gpu.",
    }

    def candidate(**metadata_changes):
        metadata = {
            "labels": {"pod-template-hash": current_hash},
            "ownerReferences": [{
                "uid": current_rs_uid,
                "kind": "ReplicaSet",
                "controller": True,
            }],
        }
        status = {"phase": "Pending", "conditions": [gpu_condition]}
        for key, value in metadata_changes.items():
            if key == "phase":
                status["phase"] = value
            elif key == "owner_uid":
                metadata["ownerReferences"][0]["uid"] = value
            elif key == "owner_kind":
                metadata["ownerReferences"][0]["kind"] = value
            elif key == "owner_controller":
                metadata["ownerReferences"][0]["controller"] = value
            elif key == "pod_hash":
                metadata["labels"]["pod-template-hash"] = value
            else:
                metadata[key] = value
        return {"metadata": metadata, "status": status}

    assert _evaluate(
        "CURRENT_POD_EVALUATOR",
        {"items": [candidate()]},
        current_rs_uid,
        current_hash,
    ) == "gpu-capacity"
    invalid_candidates = (
        candidate(owner_uid="stale-rs-uid"),
        candidate(owner_kind="Deployment"),
        candidate(owner_controller=False),
        candidate(pod_hash="manually-labeled-hash"),
        candidate(deletionTimestamp=None),
        candidate(deletionTimestamp="2026-07-26T00:00:00Z"),
        candidate(phase="Running"),
        candidate(phase="Succeeded"),
        candidate(phase="Failed"),
    )
    for invalid in invalid_candidates:
        assert _evaluate(
            "CURRENT_POD_EVALUATOR",
            {"items": [invalid]},
            current_rs_uid,
            current_hash,
        ) == ""
        assert _evaluate(
            "CURRENT_POD_EVALUATOR",
            {"items": [candidate(), invalid]},
            current_rs_uid,
            current_hash,
        ) == ""


def test_seed_dry_run_reports_resource_identities_without_calling_kubectl(tmp_path):
    marker = tmp_path / "kubectl-called"
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        '#!/bin/sh\n: > "$MOCK_KUBECTL_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    kubectl.chmod(0o700)
    environment = dict(os.environ)
    environment.update({
        "MOCK_KUBECTL_MARKER": str(marker),
        "PATH": f"{tmp_path}:{environment['PATH']}",
    })

    completed = subprocess.run(
        [
            str(SEED_PATH),
            "--namespace", "faultdiag-integrated",
            "--pvc", "faultdiag-base-model",
            "--source-node", "worker-01.example.internal",
            "--source-path", "/data/models/Qwen3.5-9B",
            "--timeout-seconds", "900",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    for expected in (
        "mode=dry-run",
        "namespace=faultdiag-integrated",
        "pvc=faultdiag-base-model",
        "source_node=worker-01.example.internal",
        "source_path=/data/models/Qwen3.5-9B",
        "staging=/target/.Qwen3.5-9B.staging",
        "destination=/target/Qwen3.5-9B",
        "timeout_seconds=900",
        "pod=seed-base-model-pvc-",
    ):
        assert expected in completed.stdout


def test_seed_rejects_invalid_inputs_before_calling_kubectl(tmp_path):
    marker = tmp_path / "kubectl-called"
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        '#!/bin/sh\n: > "$MOCK_KUBECTL_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    kubectl.chmod(0o700)
    environment = dict(os.environ)
    environment.update({
        "MOCK_KUBECTL_MARKER": str(marker),
        "PATH": f"{tmp_path}:{environment['PATH']}",
    })
    valid = {
        "--namespace": "faultdiag-integrated",
        "--pvc": "faultdiag-base-model",
        "--source-node": "worker-01.example.internal",
        "--source-path": "/data/models/Qwen3.5-9B",
        "--timeout-seconds": "900",
    }
    invalid_overrides = (
        {"--namespace": "another-namespace"},
        {"--pvc": "Faultdiag_Base_Model"},
        {"--pvc": "a..b"},
        {"--pvc": "a.-b"},
        {"--pvc": "a-.b"},
        {"--source-node": "worker-01;id"},
        {"--source-node": "a..b"},
        {"--source-node": "a.-b"},
        {"--source-node": "a-.b"},
        {"--source-path": "data/models/Qwen3.5-9B"},
        {"--source-path": "//data/models/Qwen3.5-9B"},
        {"--source-path": "/data/../models/Qwen3.5-9B"},
        {"--source-path": "/data/models//Qwen3.5-9B"},
        {"--source-path": "/data/models/Qwen3.5-9B\nkind: Pod"},
        {"--source-path": "/data/models/${HOME}/Qwen3.5-9B"},
        {"--timeout-seconds": "0"},
        {"--timeout-seconds": "86401"},
        {"--timeout-seconds": "9m"},
    )

    for override in invalid_overrides:
        arguments = []
        for flag, value in (valid | override).items():
            arguments.extend((flag, value))
        completed = subprocess.run(
            [str(SEED_PATH), *arguments, "--dry-run"],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )
        assert completed.returncode != 0, override
        assert not marker.exists(), override


def test_seed_pod_is_pinned_and_mounts_only_the_source_and_target():
    program = _seed_embedded_python("POD_MANIFEST_PROGRAM")
    rendered = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            program,
            "faultdiag-integrated",
            "faultdiag-base-model",
            "worker-01.example.internal",
            "/data/models/Qwen3.5-9B",
            "seed-base-model-pvc-1234",
            "900",
            _seed_embedded_python("SEED_PROGRAM"),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    pod = json.loads(rendered.stdout)

    assert pod["kind"] == "Pod"
    assert pod["metadata"] == {
        "name": "seed-base-model-pvc-1234",
        "namespace": "faultdiag-integrated",
        "labels": {"app.kubernetes.io/name": "base-model-pvc-seeder"},
    }
    spec = pod["spec"]
    assert spec["nodeName"] == "worker-01.example.internal"
    assert spec["restartPolicy"] == "Never"
    assert spec["activeDeadlineSeconds"] == 900
    assert spec["automountServiceAccountToken"] is False
    assert spec["volumes"] == [
        {
            "name": "source-model",
            "hostPath": {
                "path": "/data/models/Qwen3.5-9B",
                "type": "Directory",
            },
        },
        {
            "name": "target-pvc",
            "persistentVolumeClaim": {"claimName": "faultdiag-base-model"},
        },
    ]
    container = spec["containers"][0]
    assert container["volumeMounts"] == [
        {
            "name": "source-model",
            "mountPath": "/source/Qwen3.5-9B",
            "readOnly": True,
        },
        {"name": "target-pvc", "mountPath": "/target", "readOnly": False},
    ]
    assert {entry["name"]: entry["value"] for entry in container["env"]} == {
        "SOURCE_ROOT": "/source/Qwen3.5-9B",
        "TARGET_ROOT": "/target",
    }


def test_seed_source_contract_is_bounded_and_cleans_up_only_its_pod():
    source = _seed_source()
    seed_program = _seed_embedded_python("SEED_PROGRAM")

    assert source.startswith("#!/bin/sh\nset -eu\n")
    assert 'trap cleanup EXIT HUP INT TERM' in source
    assert 'created_pod="yes"' in source
    assert 'kubectl delete pod "$POD_NAME"' in source
    assert 'kubectl wait --for=jsonpath=' in source
    assert '--timeout="${TIMEOUT_SECONDS}s"' in source
    assert "kubectl logs" not in source
    assert "kubectl exec" not in source
    assert "rm -rf" not in source
    assert "/target/*" not in source
    assert "/target/.Qwen3.5-9B.staging" in seed_program
    assert "/target/Qwen3.5-9B" in seed_program
    assert "model.safetensors.index.json" in seed_program
    assert "weight_map" in seed_program
    assert "hashlib.sha256" in seed_program
    assert "sorted(" in seed_program
    assert "os.replace" in seed_program
    assert "stat.S_ISREG" in seed_program
    assert "is_symlink" in seed_program
    assert ".Qwen3.5-9B.staging.claim" in seed_program
    assert "os.O_EXCL" in seed_program
    assert "claim_is_owned" in seed_program


def test_seed_program_is_idempotent_and_refuses_a_changed_destination(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.mkdir()
    _write_synthetic_qwen_model(source)

    first = _run_seed_program(source, target)
    assert first.returncode == 0, first.stderr
    destination = target / "Qwen3.5-9B"
    assert destination.is_dir()
    assert not (target / ".Qwen3.5-9B.staging").exists()

    second = _run_seed_program(source, target)
    assert second.returncode == 0, second.stderr
    assert "already matches" in second.stdout

    (source / "config.json").write_text('{"changed":true}\n', encoding="utf-8")
    changed = _run_seed_program(source, target)
    assert changed.returncode != 0
    assert "refusing to overwrite" in changed.stderr.lower()
    assert (destination / "config.json").read_text(encoding="utf-8") == (
        '{"model_type":"qwen"}\n'
    )


def test_seed_program_rejects_incomplete_destination_and_missing_index_shard(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.mkdir()
    _write_synthetic_qwen_model(source)
    incomplete = target / "Qwen3.5-9B"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}\n", encoding="utf-8")

    existing = _run_seed_program(source, target)
    assert existing.returncode != 0
    assert "incomplete" in existing.stderr.lower()
    assert incomplete.is_dir()

    for child in incomplete.iterdir():
        child.unlink()
    incomplete.rmdir()
    (source / "model-00002-of-00002.safetensors").unlink()
    missing = _run_seed_program(source, target)
    assert missing.returncode != 0
    assert "referenced shard" in missing.stderr.lower()
    assert not (target / ".Qwen3.5-9B.staging").exists()


def test_seed_program_rejects_symlinks_and_special_files(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    symlink_source = tmp_path / "symlink-source"
    _write_synthetic_qwen_model(symlink_source)
    (symlink_source / "linked-config").symlink_to("config.json")

    symlinked = _run_seed_program(symlink_source, target)
    assert symlinked.returncode != 0
    assert "symlink" in symlinked.stderr.lower()

    fifo_source = tmp_path / "fifo-source"
    _write_synthetic_qwen_model(fifo_source)
    os.mkfifo(fifo_source / "unexpected.fifo")
    special = _run_seed_program(fifo_source, target)
    assert special.returncode != 0
    assert "regular file" in special.stderr.lower()
    assert not (target / ".Qwen3.5-9B.staging").exists()


def test_seed_program_respects_a_concurrent_staging_ownership_claim(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.mkdir()
    _write_synthetic_qwen_model(source)
    claim = target / ".Qwen3.5-9B.staging.claim"
    claim.write_text("winner-token\n", encoding="utf-8")

    loser = _run_seed_program(source, target)

    assert loser.returncode != 0
    assert "another invocation" in loser.stderr.lower()
    assert claim.read_text(encoding="utf-8") == "winner-token\n"
    assert not (target / "Qwen3.5-9B").exists()


def test_seed_program_never_removes_another_claims_staging_directory(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.mkdir()
    _write_synthetic_qwen_model(source)
    claim = target / ".Qwen3.5-9B.staging.claim"
    claim.write_text("winner-token\n", encoding="utf-8")
    staging = target / ".Qwen3.5-9B.staging"
    staging.mkdir()
    sentinel = staging / "winner-sentinel"
    sentinel.write_text("owned by winner\n", encoding="utf-8")

    loser = _run_seed_program(source, target)

    assert loser.returncode != 0
    assert claim.read_text(encoding="utf-8") == "winner-token\n"
    assert sentinel.read_text(encoding="utf-8") == "owned by winner\n"
