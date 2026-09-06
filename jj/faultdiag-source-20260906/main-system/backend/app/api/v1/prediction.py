from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from typing import Optional
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.services.prediction_service import PredictionService
from app.models.prediction_record import PredictionRecord
from app.schemas.prediction import PredictionRequest, PredictionOut, PredictionListOut, GradeRequest

router = APIRouter()
_service = None


def get_service() -> PredictionService:
    global _service
    if _service is None:
        _service = PredictionService()
    return _service


@router.post("/prediction", response_model=PredictionOut)
async def predict(
    file: UploadFile = File(None),
    cpu_usage: Optional[float] = Form(None),
    memory_usage: Optional[float] = Form(None),
    disk_usage: Optional[float] = Form(None),
    temperature: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    if file is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="请上传日志文件")
    content_bytes = await file.read()
    try:
        log_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        log_text = content_bytes.decode("gbk", errors="ignore")

    metrics = {
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "disk_usage": disk_usage,
        "temperature": temperature,
    }
    svc = get_service()
    return svc.predict(log_text, metrics, db)


@router.post("/prediction/text", response_model=PredictionOut)
def predict_text(body: PredictionRequest, db: Session = Depends(get_db)):
    metrics = {
        "cpu_usage": body.cpu_usage,
        "memory_usage": body.memory_usage,
        "disk_usage": body.disk_usage,
        "temperature": body.temperature,
    }
    svc = get_service()
    return svc.predict(body.log_text, metrics, db)


@router.get("/prediction/history", response_model=PredictionListOut)
def prediction_history(page: int = 1, page_size: int = 20, db: Session = Depends(get_db)):
    q = db.query(PredictionRecord).order_by(PredictionRecord.created_at.desc())
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    from app.services.log_label_service import resolve_upload_labels
    labels = resolve_upload_labels([r.run_id for r in items if r.run_id])
    for r in items:
        lb = labels.get(r.run_id) or {}
        r.filename = lb.get("filename")
        r.uploaded_at = lb.get("uploaded_at")
        r.source = "upload" if (r.run_id in labels) else "dataset"
    return PredictionListOut(total=total, items=items)


# ── POST /prediction/access/logs（关键日志接入） ────────────────────────────
@router.post("/prediction/access/logs")
async def access_key_logs(
    strategy: str = Form(...),
    timeRange: Optional[str] = Form(None),
    scoreThreshold: Optional[float] = Form(None),
    tagTypes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """关键日志接入，支持实时接入、时间段接入，从日志分析结果库抽取"""
    svc = get_service()
    
    # 准备接入配置
    config = {
        "strategy": strategy,
        "timeRange": timeRange,
        "scoreThreshold": scoreThreshold,
        "tagTypes": tagTypes
    }
    
    # 从日志分析结果库抽取
    result = svc.extract_from_log_analysis(config, db)
    
    return result


# ── POST /prediction/grade（按接入报告/已接入日志做大模型软件状态分级） ──────────
@router.post("/prediction/grade", response_model=PredictionOut)
def grade_run(body: GradeRequest, db: Session = Depends(get_db)):
    """对接入报告(report_id)整体或已接入日志(run_id)做软件状态分级。

    返回 green/yellow/red + 风险说明 + run_id。优先按 report_id 对报告整体分级。
    """
    svc = get_service()
    if body.report_id:
        return svc.grade_report(body.report_id, db)
    if body.run_id:
        return svc.grade_run(body.run_id, db)
    raise HTTPException(status_code=400, detail="report_id 与 run_id 不能同时为空")


# ── DELETE /prediction/history/{record_id}（删除一条预测记录） ─────────────────
@router.delete("/prediction/history/{record_id}")
def delete_prediction(record_id: int, db: Session = Depends(get_db)):
    """删除一条软件状态预测记录（分级预警可复查后删除）。"""
    rec = db.query(PredictionRecord).filter(PredictionRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="预测记录不存在")
    db.delete(rec)
    db.commit()
    return {"ok": True}


# ── GET /prediction/by-run/{run_id}（读取某报告最近一次分级，供回显/回放） ────────
@router.get("/prediction/by-run/{run_id}", response_model=Optional[PredictionOut])
def prediction_by_run(run_id: str, db: Session = Depends(get_db)):
    """读取某接入报告/日志(run_id)最近一次的分级结果；无记录返回 null。

    「软件状态预测」列表据此回显已存分级、点行回放分级详情，不必每次重新调大模型。
    """
    return get_service().latest_prediction_out(run_id, db)


# ── DELETE /prediction/report/{report_id}（级联删除整份接入报告） ─────────────────
@router.delete("/prediction/report/{report_id}")
def delete_report(report_id: str, db: Session = Depends(get_db)):
    """级联删除整份接入报告：接入记录 + 其分级记录 + 其诊断记录。

    三个页面（多源接入 / 软件状态预测 / 分级预警）共享同一份报告，删除后同时消失。
    """
    return get_service().delete_report(report_id, db)


# ── GET /prediction/access/results（获取接入结果列表） ────────────────────────
@router.get("/prediction/access/results")
async def get_access_results(
    page: int = 1,
    pageSize: int = 10,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    source: Optional[str] = None,
    severity: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取接入结果列表，支持分页和查询条件"""
    svc = get_service()
    
    # 准备查询参数
    params = {
        "page": page,
        "pageSize": pageSize,
        "startDate": startDate,
        "endDate": endDate,
        "source": source,
        "severity": severity
    }
    
    # 调用服务获取接入结果列表
    result = svc.get_access_results(params, db)
    
    return result
