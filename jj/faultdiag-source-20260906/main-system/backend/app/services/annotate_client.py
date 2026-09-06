"""annotate_client.py — 跨系统只读客户端：从标注子系统拉取「典型异常标注案例」。

仅用于主系统知识库的「从标注典型案例导入」。标注后端不可达 / 报错时抛异常，
由调用方转成导入失败（不影响其它功能）。
"""
from __future__ import annotations

import httpx

from app.config import settings


def fetch_export_cases(
    *,
    anomaly_type: str | None = None,
    package_id: int | None = None,
    limit: int = 200,
) -> dict:
    """调标注后端 GET /annotations/export-cases，取典型异常标注案例。

    返回 {items:[{window_id, anomaly_type, note, fault_type_description,
    package_name, external_run_id, log_text}], total}。
    """
    base = settings.ANNOTATE_BACKEND_URL.rstrip("/")
    params: dict = {"limit": limit}
    if anomaly_type:
        params["anomaly_type"] = anomaly_type
    if package_id:
        params["package_id"] = package_id
    with httpx.Client(timeout=settings.ANNOTATE_TIMEOUT) as client:
        resp = client.get(f"{base}/annotations/export-cases", params=params)
        resp.raise_for_status()
        return resp.json()


def fetch_dashboard_summary() -> dict:
    """调标注后端 GET /dashboard/summary，取标注库(data_bj)的全局计数。

    返回 {total_packages, total_slice_tasks, total_windows, annotated_windows,
    pending_windows, normal_count, abnormal_count}。标注后端不可达/报错时抛异常，
    由调用方转成可读错误（不影响主页面其它功能）。
    """
    base = settings.ANNOTATE_BACKEND_URL.rstrip("/")
    with httpx.Client(timeout=settings.ANNOTATE_TIMEOUT) as client:
        resp = client.get(f"{base}/dashboard/summary")
        resp.raise_for_status()
        return resp.json()
