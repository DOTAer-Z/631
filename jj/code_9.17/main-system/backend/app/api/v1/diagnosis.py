from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from typing import Optional
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.services.fault_location_service import fault_location_service
from app.models.diagnosis_record import DiagnosisRecord
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisOut, DiagnosisListOut

router = APIRouter()


@router.post("/diagnosis", response_model=DiagnosisOut)
async def diagnose(
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    """支持文件上传方式提交日志（故障定位）"""
    if file is None:
        raise HTTPException(status_code=400, detail="请上传日志文件")
    content_bytes = await file.read()
    try:
        log_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        log_text = content_bytes.decode("gbk", errors="ignore")

    return fault_location_service.locate(
        log_text=log_text,
        db=db,
        filename=file.filename,
    )


@router.post("/diagnosis/text", response_model=DiagnosisOut)
def diagnose_text(body: DiagnosisRequest, db: Session = Depends(get_db)):
    """支持 JSON 文本方式提交日志（故障定位）"""
    return fault_location_service.locate(
        log_text=body.log_text,
        db=db,
        run_id=body.run_id,
        top_k=body.top_k,
        force_refresh=body.force_refresh,
        skip_gate=body.skip_gate,
    )


@router.get("/diagnosis/by-run/{run_id}", response_model=Optional[DiagnosisOut])
def diagnosis_by_run(run_id: str, db: Session = Depends(get_db)):
    """读取某日志(run_id)最近一次的诊断结果；无记录返回 null（前端据此决定是否显示）。"""
    return fault_location_service.latest_record_out(run_id, db)


@router.get("/diagnosis/history", response_model=DiagnosisListOut)
def diagnosis_history(
    page: int = 1,
    page_size: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    # 故障诊断的历史只保留「判定为故障」的记录；「正常」记录由诊断页的历史里剔除，
    # 避免与预测预警页（只列正常文件）出现同一 run 两个入口的歧义。
    q = (
        db.query(DiagnosisRecord)
        .filter(DiagnosisRecord.is_fault == True)  # noqa: E712
        .order_by(DiagnosisRecord.created_at.desc())
    )
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    # 显示用：按 run_id 反查「文件名 + 上传时间」，前端优先展示（取代长 run_id）
    from app.services.log_label_service import resolve_upload_labels
    labels = resolve_upload_labels([r.run_id for r in items if r.run_id])
    for r in items:
        lb = labels.get(r.run_id) or {}
        r.filename = lb.get("filename")
        r.uploaded_at = lb.get("uploaded_at")
        r.source = "upload" if (r.run_id in labels) else "dataset"
    return DiagnosisListOut(total=total, items=items)


@router.delete("/diagnosis/history/normal")
def delete_normal_diagnoses(db: Session = Depends(get_db)):
    """清除全部「正常 / 非故障」诊断记录(is_fault=False)。

    故障诊断只对「软件状态预测」分级为 严重/紧急 的报告做根因分析，结论必为故障；
    历史里的「正常」记录是修复前/旧门控期遗留的脏数据，一键清除。
    注意：本路由必须注册在 /diagnosis/history/{record_id} 之前，否则 "normal"
    会被当作 record_id(int) 解析。
    """
    n = (
        db.query(DiagnosisRecord)
        .filter(DiagnosisRecord.is_fault == False)  # noqa: E712
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "deleted": int(n or 0)}


@router.delete("/diagnosis/history/{record_id}")
def delete_diagnosis(record_id: int, db: Session = Depends(get_db)):
    """删除一条故障诊断记录（诊断历史可复查后删除）。"""
    rec = db.query(DiagnosisRecord).filter(DiagnosisRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    db.delete(rec)
    db.commit()
    return {"ok": True}
