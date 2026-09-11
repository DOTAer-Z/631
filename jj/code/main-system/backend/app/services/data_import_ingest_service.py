"""
data_import_ingest_service.py — 数据导入压缩包的「自动摄入」管线（方案 1）。

负责：
    1. 解压 zip / tar / tar.gz / tgz 到工作目录（防 zip-slip / 路径穿越）
    2. 在解压结果中嗅探 Test_*** 目录（递归一层、两层均可）
    3. 调用 backend/ingest_dataset.py 的同名函数 ingest_test_dir，
       写入 cases / runs / log_entries / log_windows 四张表
    4. 清理临时解压目录（保留原压缩包）

设计原则：
    - 完全复用 ingest_dataset.py 既有函数，不重写日志解析逻辑
    - 解压目录写到 settings.DATA_IMPORT_EXTRACT_ROOT，独立于压缩包存储目录
    - 出错只抛 IngestPipelineError，由调用方记录到 dataset_imports 表
"""
from __future__ import annotations

import logging
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.run import Run
from app.models.training_data import TrainingImportItem, TrainingTestVersion
from app.services.training_data_service import import_test_version

logger = logging.getLogger(__name__)

# 预期的 Test 目录前缀（与 ingest_dataset.py 保持一致）
_TEST_PREFIX = "Test_"
# 解压时允许的最大单文件字节数（防 zip-bomb），默认 1GiB
_MAX_MEMBER_SIZE = 1 * 1024 * 1024 * 1024
# 解压时允许的最大总字节数（防 zip-bomb），默认 4GiB
_MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
_TRAINING_PLATFORM = "NuttX"


class IngestPipelineError(RuntimeError):
    """摄入管线任意环节失败时抛出。"""


@dataclass
class IngestResult:
    case_ids: List[str]
    run_count: int
    entry_count: int
    window_count: int
    new_case_count: int = 0
    updated_case_count: int = 0
    new_run_count: int = 0
    updated_run_count: int = 0
    training_complete_count: int = 0
    training_incomplete_count: int = 0
    training_duplicate_count: int = 0
    training_failed_count: int = 0
    training_parse_failed_count: int = 0
    test_results: List[dict[str, Any]] = field(default_factory=list)

    @property
    def preserved_training_count(self) -> int:
        return (
            self.training_complete_count
            + self.training_incomplete_count
            + self.training_duplicate_count
        )

    @property
    def archive_status(self) -> str:
        if self.preserved_training_count == 0:
            return "failed"
        if self.training_failed_count or self.training_parse_failed_count:
            return "partial_success"
        return "success"


# ---------------------------------------------------------------------------
# 解压（防穿越）
# ---------------------------------------------------------------------------
def _is_within_directory(directory: Path, target: Path) -> bool:
    try:
        directory_abs = directory.resolve()
        target_abs = target.resolve()
        return str(target_abs).startswith(str(directory_abs) + str(directory_abs.anchor and "/" or ""))
    except Exception:
        return False


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    total = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_mode = member.external_attr >> 16
            if stat.S_ISLNK(member_mode):
                raise IngestPipelineError(f"压缩包包含符号链接: {member.filename}")
            # 跳过目录条目本身
            if member.is_dir():
                continue
            # 防 zip-slip
            target = (dest / member.filename).resolve()
            try:
                target.relative_to(dest.resolve())
            except ValueError as exc:
                raise IngestPipelineError(f"压缩包包含非法路径: {member.filename}") from exc
            # 防 zip-bomb
            if member.file_size > _MAX_MEMBER_SIZE:
                raise IngestPipelineError(f"压缩包内单文件过大: {member.filename}")
            total += member.file_size
            if total > _MAX_TOTAL_SIZE:
                raise IngestPipelineError("压缩包解压后总大小超过 4GB 限制")
        # 通过校验后再实际解压
        zf.extractall(dest)


def _safe_extract_tar(tar_path: Path, dest: Path) -> None:
    total = 0
    mode = "r:gz" if str(tar_path).endswith((".tar.gz", ".tgz")) else "r:*"
    with tarfile.open(tar_path, mode) as tf:
        members = tf.getmembers()
        for m in members:
            target = (dest / m.name).resolve()
            try:
                target.relative_to(dest.resolve())
            except ValueError as exc:
                raise IngestPipelineError(f"压缩包包含非法路径: {m.name}") from exc
            if not (m.isdir() or m.isreg()):
                if m.islnk() or m.issym():
                    raise IngestPipelineError(f"压缩包包含符号链接: {m.name}")
                raise IngestPipelineError(f"压缩包包含特殊文件: {m.name}")
            if m.isdir():
                continue
            if m.size > _MAX_MEMBER_SIZE:
                raise IngestPipelineError(f"压缩包内单文件过大: {m.name}")
            total += m.size
            if total > _MAX_TOTAL_SIZE:
                raise IngestPipelineError("压缩包解压后总大小超过 4GB 限制")
        tf.extractall(dest, members=members)


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """根据扩展名安全解压压缩包到 dest_dir。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        _safe_extract_zip(archive_path, dest_dir)
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        _safe_extract_tar(archive_path, dest_dir)
    else:
        raise IngestPipelineError(f"不支持的压缩格式: {archive_path.name}")


# ---------------------------------------------------------------------------
# 嗅探 Test_*** 目录
# ---------------------------------------------------------------------------
def find_test_dirs(root: Path, max_depth: int = 3) -> List[Path]:
    """在 root 下递归查找所有 Test_*** 目录，最多深入 max_depth 层。

    排除：
      - __MACOSX（macOS 压缩包垃圾目录，其下 Test_* 是空的、只含 ._ 元数据文件）
      - 以 . 开头的隐藏目录
      - 空 Test 目录（不含 fip_info.data / ground_truth.json 的虚目录 —— 否则会生成
        内容为空的 incomplete TrainingTestVersion，并被误设为 latest，导致训练页
        该 Test 显示 skipped_incomplete 不可选）
    """
    found: List[Path] = []

    def _is_junk_dir(d: Path) -> bool:
        if d.name == "__MACOSX" or (d.name.startswith(".") and d.name != "."):
            return True
        # 空 Test 目录：无 fip_info.data 且无 ground_truth.json → 视为虚目录
        if d.name.startswith(_TEST_PREFIX):
            has_label = (d / "fip_info.data").exists() or (d / "ground_truth.json").exists()
            if not has_label:
                return True
        return False

    if not root.is_dir():
        return found

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = list(d.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if _is_junk_dir(child):
                continue
            if child.name.startswith(_TEST_PREFIX):
                found.append(child)
            else:
                _walk(child, depth + 1)

    _walk(root, 0)
    return sorted(found, key=lambda p: p.name)


def _commit_or_rollback(db) -> None:
    """Commit an explicit pipeline boundary without masking database failures."""
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _persist_parse_outcome(
    db,
    *,
    import_id: str,
    test_name: str,
    test_version_id: int,
    parse_status: str,
    error_message: str | None,
    round_statuses: tuple[str, str],
) -> None:
    """Persist the Test-level parse result after the version boundary."""
    try:
        item = (
            db.query(TrainingImportItem)
            .filter_by(
                import_id=import_id,
                platform=_TRAINING_PLATFORM,
                test_name=test_name,
            )
            .one_or_none()
        )
        version = db.get(TrainingTestVersion, test_version_id)
        if item is None or version is None:
            raise IngestPipelineError(
                f"训练版本解析结果缺少持久化记录: {test_name} (version={test_version_id})"
            )

        item.parse_status = parse_status
        item.error_message = error_message
        version.round_1_parse_status, version.round_2_parse_status = round_statuses
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _round_statuses_after_parse_failure(db, test_version_id: int) -> tuple[str, str]:
    """Keep already committed legacy parser rounds visible; mark all others failed."""
    try:
        completed_rounds = {
            round_no
            for (round_no,) in (
                db.query(Run.round_no)
                .filter(Run.test_version_id == test_version_id)
                .all()
            )
            if round_no in (1, 2)
        }
    except SQLAlchemyError:
        db.rollback()
        raise
    return tuple("parsed" if round_no in completed_rounds else "failed" for round_no in (1, 2))


# ---------------------------------------------------------------------------
# 摄入管线主入口
# ---------------------------------------------------------------------------
def run_ingest_pipeline(
    archive_path: Path,
    extract_root: Path,
    import_id: str,
    *,
    window_size_s: int = 30,
    stride_s: int = 15,
    only: Optional[Iterable[str]] = None,
    prepared_work_dir: Optional[Path] = None,
) -> IngestResult:
    """
    解压 archive_path 到 extract_root/<import_id>/，嗅探 Test_*** 目录，
    逐个调用 ingest_dataset.ingest_test_dir 写入数据库。

    prepared_work_dir: 调用方已解压并探测过的目录（用于 jsonl 分支先行判断）。
                      传入时不再重复解压。
    """
    # 延迟导入：避免在不需要 ingest 时拉起 sentence-transformers / faiss 等重依赖
    import sys
    import os
    sys.path.insert(0, "/app")  # 让 `import ingest_dataset` 可用（容器内绝对路径）
    try:
        import ingest_dataset  # type: ignore
    except ImportError as exc:
        raise IngestPipelineError("缺少 ingest_dataset 模块；该脚本应位于 /app/ingest_dataset.py") from exc

    from app.database import SessionLocal, get_mongo_db

    # 解压目录隔离到 import_id 子目录
    work_dir = (prepared_work_dir or (extract_root / import_id)).resolve()
    if work_dir.exists() and prepared_work_dir is None:
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_ids: List[str] = []
    run_count = 0
    entry_count = 0
    window_count = 0
    new_case_count = 0
    updated_case_count = 0
    new_run_count = 0
    updated_run_count = 0
    training_complete_count = 0
    training_incomplete_count = 0
    training_duplicate_count = 0
    training_failed_count = 0
    training_parse_failed_count = 0
    test_results: List[dict[str, Any]] = []

    try:
        # Step 1：解压
        logger.info("[ingest_pipeline] 解压 %s -> %s", archive_path, work_dir)
        extract_archive(archive_path, work_dir)

        # Step 2：嗅探 Test_*** 目录
        test_dirs = find_test_dirs(work_dir)
        if only:
            keep = set(only)
            test_dirs = [d for d in test_dirs if d.name in keep]
        if not test_dirs:
            raise IngestPipelineError(
                "压缩包中未发现任何 Test_*** 目录；请确认数据集结构（应为 Test_<id>/{logs,fip_info.data,ground_truth.json}）"
            )

        # Step 3：逐个摄入
        db = SessionLocal()
        mongo_db = get_mongo_db()
        try:
            ingest_dataset.ensure_system(db)
            for td in test_dirs:
                test_result: dict[str, Any] = {
                    "test_name": td.name,
                    "test_version_id": None,
                    "version_status": None,
                    "parse_status": "not_started",
                    "error": None,
                }
                try:
                    # The version service owns its nested transaction. This outer savepoint
                    # isolates a failed Test import without rolling back earlier Tests.
                    with db.begin_nested():
                        imported = import_test_version(
                            db,
                            test_dir=td,
                            import_id=import_id,
                            platform=_TRAINING_PLATFORM,
                        )
                except SQLAlchemyError:
                    db.rollback()
                    raise
                except Exception as exc:
                    # Validation/import failures may have recorded an import item. Keep that
                    # per-Test record while still allowing later Tests to proceed.
                    _commit_or_rollback(db)
                    training_failed_count += 1
                    test_result["version_status"] = "failed"
                    test_result["parse_status"] = "skipped_version_failed"
                    test_result["error"] = str(exc)
                    test_results.append(test_result)
                    logger.exception("[ingest_pipeline] %s 训练版本导入失败", td.name)
                    continue

                # ingest_dataset commits Case/Run records itself. Persist the immutable
                # version and import item before either skipping or invoking that parser.
                _commit_or_rollback(db)

                test_result["test_version_id"] = imported.version_id
                test_result["version_status"] = imported.status
                if imported.status == "imported":
                    training_complete_count += 1
                elif imported.status == "incomplete":
                    training_incomplete_count += 1
                elif imported.status == "duplicate":
                    training_duplicate_count += 1
                else:
                    training_failed_count += 1
                    test_result["version_status"] = "failed"
                    test_result["parse_status"] = "skipped_version_failed"
                    test_result["error"] = f"unexpected version status: {imported.status}"
                    test_results.append(test_result)
                    continue

                if imported.completeness != "complete":
                    test_result["parse_status"] = "skipped_incomplete"
                    _persist_parse_outcome(
                        db,
                        import_id=import_id,
                        test_name=td.name,
                        test_version_id=imported.version_id,
                        parse_status="skipped_incomplete",
                        error_message=None,
                        round_statuses=("skipped_incomplete", "skipped_incomplete"),
                    )
                    test_results.append(test_result)
                    continue

                logger.info("[ingest_pipeline] 解析 %s", td.name)
                try:
                    summary = ingest_dataset.ingest_test_dir(
                        db, mongo_db, test_dir=td,
                        window_size_s=window_size_s, stride_s=stride_s,
                        test_version_id=imported.version_id,
                    )
                except SQLAlchemyError:
                    db.rollback()
                    raise
                except Exception as exc:
                    # The immutable version is committed above. The legacy parser may have
                    # committed one round before failing, so derive those statuses from Run.
                    round_statuses = _round_statuses_after_parse_failure(db, imported.version_id)
                    _persist_parse_outcome(
                        db,
                        import_id=import_id,
                        test_name=td.name,
                        test_version_id=imported.version_id,
                        parse_status="failed",
                        error_message=str(exc),
                        round_statuses=round_statuses,
                    )
                    training_parse_failed_count += 1
                    test_result["parse_status"] = "failed"
                    test_result["error"] = str(exc)
                    test_results.append(test_result)
                    logger.exception("[ingest_pipeline] %s 日志解析失败", td.name)
                    continue

                _persist_parse_outcome(
                    db,
                    import_id=import_id,
                    test_name=td.name,
                    test_version_id=imported.version_id,
                    parse_status="parsed",
                    error_message=None,
                    round_statuses=("parsed", "parsed"),
                )
                test_result["parse_status"] = "parsed"
                test_results.append(test_result)
                case_ids.append(summary["case_id"])
                if summary.get("case_is_new"):
                    new_case_count += 1
                else:
                    updated_case_count += 1
                runs_ok = [r for r in summary["runs"] if "skipped" not in r]
                run_count += len(runs_ok)
                entry_count += sum(r.get("entries", 0) for r in runs_ok)
                window_count += sum(r.get("windows", 0) for r in runs_ok)
                new_run_count += sum(1 for r in runs_ok if r.get("is_new"))
                updated_run_count += sum(1 for r in runs_ok if not r.get("is_new"))
        finally:
            db.close()

        return IngestResult(
            case_ids=case_ids,
            run_count=run_count,
            entry_count=entry_count,
            window_count=window_count,
            new_case_count=new_case_count,
            updated_case_count=updated_case_count,
            new_run_count=new_run_count,
            updated_run_count=updated_run_count,
            training_complete_count=training_complete_count,
            training_incomplete_count=training_incomplete_count,
            training_duplicate_count=training_duplicate_count,
            training_failed_count=training_failed_count,
            training_parse_failed_count=training_parse_failed_count,
            test_results=test_results,
        )
    finally:
        # 解压目录非常占盘，无论成功失败都清理
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:  # pragma: no cover
            logger.warning("[ingest_pipeline] 清理解压目录失败: %s", work_dir)
