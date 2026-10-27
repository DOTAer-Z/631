"""Validate direct execution against the selected fault's capabilities."""


def _supported(checks):
    return bool(checks) and all(item.get("status") == "supported" for item in checks)


def select_execution(code, catalog):
    from app.schemas.virtual_prototype import FAULT_TYPES
    if code not in FAULT_TYPES:
        raise ValueError("新任务不支持该故障类型")
    capabilities = catalog.get("capabilities") or {}
    checks = capabilities.get(code) or []
    if _supported(checks):
        return code, {}
    if not checks or any(item.get("status") not in {"supported", "unsupported"} for item in checks):
        raise ValueError("故障能力未知，请刷新后重试；未提交生成任务")
    raise ValueError("所选故障能力不可用，未提交生成任务")
