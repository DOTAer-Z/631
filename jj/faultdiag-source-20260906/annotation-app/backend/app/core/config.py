from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "data-bj-backend"
    app_env: str = "dev"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str = Field(
        default="sqlite:///./app.db",
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL", "db_url"),
    )

    storage_root: Path = Path("/data/storage")
    # 上传体积上限。默认 20GB（可经环境变量 MAX_UPLOAD_BYTES 覆盖）。
    # 支持上传整包压缩日志或整文件夹时单次体积可能很大，故放宽默认值。
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024
    import_line_batch_size: int = Field(default=5000, ge=1)
    import_default_timezone: str = "Asia/Shanghai"
    manifest_cache_ttl_seconds: int = Field(default=30, ge=1)
    log_page_default_limit: int = Field(default=100, ge=1)
    log_page_max_limit: int = Field(default=500, ge=1)
    window_browser_logs_max_limit: int = 500
    window_browser_manifest_cache_ttl_seconds: int = 5

    # Parse-root detection: directory whose lowercased name equals a marker, or
    # starts with "<marker>_" / "<marker>-" (e.g. data, data_sample, data-2024).
    import_data_root_markers: list[str] = Field(default_factory=lambda: ["data"])

    llm_gateway_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_GATEWAY_URL", "llm_gateway_url"),
    )
    internal_llm_gateway_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "INTERNAL_LLM_GATEWAY_TOKEN",
            "internal_llm_gateway_token",
        ),
    )
    llm_gateway_timeout_seconds: int = Field(default=180, ge=1, le=600)

    # 主系统（故障诊断主系统）后端地址，用于「从主系统导入」跨系统拉取 DB1 的 run 日志。
    # docker 网络内默认指向主后端；未配置/不可达时「从主系统导入」失败，其它功能不受影响。
    main_system_backend_url: str = Field(
        default="http://backend:8000/api/v1",
        validation_alias=AliasChoices("MAIN_SYSTEM_BACKEND_URL", "main_system_backend_url"),
    )
    main_system_timeout_seconds: int = Field(default=60, ge=1)

    # Timestamp inference (task 2) sampling / convergence knobs.
    sample_lines_per_file: int = Field(default=5, ge=1)
    sample_lines_per_cpu_cap: int = Field(default=50, ge=1)
    timestamp_convergence_threshold: int = Field(default=3, ge=1)
    timestamp_infer_min_valid_ratio: float = Field(default=0.6, ge=0.0, le=1.0)

    # Fault-type recommendation (task 3).
    recommendation_max_log_lines: int = Field(default=200, ge=1)
    recommendation_reference_fault_types: list[str] = Field(
        default_factory=lambda: [
            "内存泄漏",
            "缓冲区溢出/越界",
            "栈溢出",
            "空指针/野指针",
            "内存耗尽",
            "内存损坏",
            "死锁",
            "竞态条件",
            "优先级反转",
            "任务饥饿/阻塞",
            "死循环/系统挂起",
            "通信超时",
            "总线锁死",
            "丢包/断连",
            "CRC/校验错误",
            "外设无响应",
            "传感器数据异常",
            "设备初始化失败",
            "时钟异常",
            "电压/电源异常",
            "过温",
            "Flash/存储读写错误",
            "文件系统错误",
            "存储空间满",
            "整数溢出/下溢",
            "断言失败",
            "状态机异常",
            "配置错误",
            "看门狗复位/超时",
            "系统重启/复位",
            "进程崩溃",
            "启动失败",
            "固件/OTA升级失败",
        ]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("storage_root", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        raise TypeError("path settings must be str or pathlib.Path")

    @field_validator("import_default_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def packages_dir(self) -> Path:
        return self.storage_root / "packages"

    @property
    def extracted_dir(self) -> Path:
        return self.storage_root / "extracted"

    @property
    def slices_dir(self) -> Path:
        return self.storage_root / "slices"

    @property
    def annotations_dir(self) -> Path:
        return self.storage_root / "annotations"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_gateway_url and self.internal_llm_gateway_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
