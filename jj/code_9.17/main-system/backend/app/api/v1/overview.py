from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.models.fault_type import FaultType
from app.models.log_entry import LogEntry
from app.models.diagnosis_record import DiagnosisRecord
from app.models.prediction_record import PredictionRecord
from app.models.run import Run
from app.models.case import Case
from app.schemas.overview import (
    OverviewOut, StatsOut, RecentDiagnosisItem, RecentPredictionItem,
    DiagnosisChartOut, PredictionChartOut, DatasetStatsOut, FaultTypeCountItem,
)


def _mongo_doc_count(db: Session, collection: str) -> int:
    """统计 JSONB 文档层（mongo_docs）某集合的文档数；表不存在时安全返回 0。"""
    try:
        return db.execute(
            text("SELECT COUNT(*) FROM mongo_docs WHERE collection = :c"),
            {"c": collection},
        ).scalar() or 0
    except Exception:
        return 0


def _build_dataset_stats(db: Session) -> DatasetStatsOut:
    case_total = db.query(Case).count()
    run_total = db.query(Run).count()
    fault_run_count = db.query(Run).filter(Run.is_fault.is_(True)).count()
    normal_run_count = db.query(Run).filter(Run.is_fault.is_(False)).count()

    # 故障类型分布：按故障轮（is_fault=True）所属 case 的 fault_type 聚合
    rows = db.execute(
        text(
            """
            SELECT c.fault_type AS fault_type, COUNT(*) AS cnt
            FROM runs r
            JOIN cases c ON c.case_id = r.case_id
            WHERE r.is_fault IS TRUE AND c.fault_type IS NOT NULL
            GROUP BY c.fault_type
            ORDER BY cnt DESC
            """
        )
    ).all()
    distribution = [FaultTypeCountItem(fault_type=row[0], count=int(row[1])) for row in rows]

    return DatasetStatsOut(
        case_total=case_total,
        run_total=run_total,
        fault_run_count=fault_run_count,
        normal_run_count=normal_run_count,
        log_entry_total=_mongo_doc_count(db, "log_entries"),
        log_window_total=_mongo_doc_count(db, "log_windows"),
        fault_type_distribution=distribution,
    )


router = APIRouter()


@router.get("/overview", response_model=OverviewOut)
def get_overview(db: Session = Depends(get_db)):
    # ── 统计数字 ──────────────────────────────────────
    fault_type_count = db.query(FaultType).count()
    log_total        = db.query(LogEntry).count()
    log_indexed      = db.query(LogEntry).filter(LogEntry.is_indexed == True).count()
    diagnosis_total  = db.query(DiagnosisRecord).count()
    prediction_total = db.query(PredictionRecord).count()

    # ── 最近5条诊断记录 ────────────────────────────────
    recent_diagnoses = (
        db.query(DiagnosisRecord)
        .order_by(DiagnosisRecord.created_at.desc())
        .limit(5)
        .all()
    )

    # ── 最近5条预测记录 ────────────────────────────────
    recent_predictions = (
        db.query(PredictionRecord)
        .order_by(PredictionRecord.created_at.desc())
        .limit(5)
        .all()
    )

    # ── 诊断图表数据（全量聚合，数据量小时直接 Python 计数） ──
    all_diagnoses = db.query(
        DiagnosisRecord.is_fault,
        DiagnosisRecord.channel_used,
    ).all()
    fault_count      = sum(1 for r in all_diagnoses if r.is_fault)
    normal_count     = sum(1 for r in all_diagnoses if not r.is_fault)
    fast_channel_count = sum(1 for r in all_diagnoses if r.channel_used == "fast")
    slow_channel_count = sum(1 for r in all_diagnoses if r.channel_used == "slow")

    # ── 预测图表数据 ────────────────────────────────────
    all_predictions = db.query(PredictionRecord.health_status).all()
    green_count  = sum(1 for r in all_predictions if r.health_status == "green")
    yellow_count = sum(1 for r in all_predictions if r.health_status == "yellow")
    red_count    = sum(1 for r in all_predictions if r.health_status == "red")

    return OverviewOut(
        stats=StatsOut(
            fault_type_count=fault_type_count,
            log_total=log_total,
            log_indexed=log_indexed,
            diagnosis_total=diagnosis_total,
            prediction_total=prediction_total,
        ),
        recent_diagnoses=[RecentDiagnosisItem.model_validate(r) for r in recent_diagnoses],
        recent_predictions=[RecentPredictionItem.model_validate(r) for r in recent_predictions],
        diagnosis_chart=DiagnosisChartOut(
            fault_count=fault_count,
            normal_count=normal_count,
            fast_channel_count=fast_channel_count,
            slow_channel_count=slow_channel_count,
        ),
        prediction_chart=PredictionChartOut(
            green_count=green_count,
            yellow_count=yellow_count,
            red_count=red_count,
        ),
        dataset=_build_dataset_stats(db),
    )
