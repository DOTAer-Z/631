from __future__ import annotations

import httpx

from app.core.config import Settings


class MainSystemClient:
    """跨系统只读客户端：从故障诊断主系统（DB1）拉取「数据导入」列表并下载归档。

    仅用于标注子系统的「从主系统数据导入」。主系统不可达 / 报错时抛异常，由调用方转成
    导入失败（不影响标注其它功能）。
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.main_system_backend_url.rstrip("/")
        self._timeout = settings.main_system_timeout_seconds

    def list_data_imports(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        status: str | None = None,
    ) -> dict:
        """调用主系统 GET /data-imports，透传「数据导入」列表供前端选择。"""
        params: dict = {"page": page, "page_size": page_size}
        if keyword:
            params["keyword"] = keyword
        if status:
            params["status"] = status
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(f"{self._base_url}/data-imports", params=params)
            resp.raise_for_status()
            return resp.json()

    def get_data_import(self, import_id: str | int) -> dict:
        """调用主系统 GET /data-imports/{import_id}，返回单条数据导入详情（含 original_filename 等）。"""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(f"{self._base_url}/data-imports/{import_id}")
            if resp.status_code == 404:
                raise ValueError(f"主系统中不存在数据导入 {import_id}")
            resp.raise_for_status()
            return resp.json()

    def download_data_import(self, import_id: str | int) -> bytes:
        """调用主系统 GET /data-imports/{import_id}/download，取回该数据导入的归档字节。

        返回原始字节（.zip / .tar / .tar.gz）。主系统以 404 表示导入不存在或归档文件缺失，
        这里抛出 ValueError 由调用方转成明确的业务错误；其余异常直接上抛。
        """
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(f"{self._base_url}/data-imports/{import_id}/download")
            if resp.status_code == 404:
                raise ValueError(f"主系统中不存在数据导入 {import_id} 或其归档文件已缺失")
            resp.raise_for_status()
            return resp.content

    def trigger_data_import_ingest(self, import_id: str | int) -> dict:
        """调用主系统 POST /data-imports/{import_id}/ingest，触发「知识库入库」。

        「从主系统导入 → 入知识库」走主系统既有 run_ingest_pipeline（写 runs/cases / RAG），
        主系统侧幂等 upsert，重复调用不产生重复行。ingest 为后台异步执行，
        返回的 DataImportItem 含 ingest_status，调用方应轮询状态而非等待同步完成。
        """
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{self._base_url}/data-imports/{import_id}/ingest")
            if resp.status_code == 404:
                raise ValueError(f"主系统中不存在数据导入 {import_id} 或其归档已缺失")
            resp.raise_for_status()
            return resp.json()

    def push_annotated_run(self, payload: dict, *, timeout: float | None = None) -> dict:
        """把标注窗口推送到主系统，POST /log-analysis/annotated-run，落成一条 run + log_entries。

        反向桥（标注子系统 → 主系统）：主系统侧 import_annotated_run 负责幂等 upsert
        （按 run_id 先删后写），重复推送不产生重复行。这里抛出 ValueError 让调用方转成
        明确的业务错误，其余异常直接上抛。

        timeout：可选覆盖超时秒数。自动推送（annotation_auto_push_*）传短超时，
        避免主系统短暂不可达时拖慢标注保存；手动推送沿用 settings 默认。
        """
        effective_timeout = timeout if timeout is not None else self._timeout
        with httpx.Client(timeout=effective_timeout) as client:
            resp = client.post(
                f"{self._base_url}/log-analysis/annotated-run", json=payload
            )
            if resp.status_code == 422:
                raise ValueError(f"推送被主系统校验拒绝: {resp.text}")
            resp.raise_for_status()
            return resp.json()
