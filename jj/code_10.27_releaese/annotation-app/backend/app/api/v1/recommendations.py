from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.recommendation import (
    RecommendationBatchProgressResponse,
    RecommendationResponse,
    WindowMultiAnalysisResponse,
)
from app.services.fault_type_suggestion_service import FaultTypeSuggestionService
from app.services.recommendation_service import RecommendationService

router = APIRouter(tags=["recommendations"])


def _to_response(row, *, db: Session) -> RecommendationResponse:
    pending_suggestion = FaultTypeSuggestionService(db).find_pending_by_window(
        slice_window_id=row.slice_window_id
    )
    return RecommendationResponse(
        window_id=row.slice_window_id,
        status=row.status,
        recommended_label=row.recommended_label,
        recommended_anomaly_type=row.recommended_anomaly_type,
        reason=row.reason,
        model=row.model,
        error_message=row.error_message,
        pending_suggestion_id=pending_suggestion.id if pending_suggestion else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/slice-windows/{window_id}/recommendation", response_model=RecommendationResponse)
def create_recommendation(
    window_id: int,
    force_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    service = RecommendationService(db)
    row = service.recommend_for_window(window_id=window_id, force_refresh=force_refresh)
    return _to_response(row, db=db)


@router.get("/slice-windows/{window_id}/recommendation", response_model=RecommendationResponse)
def get_recommendation(window_id: int, db: Session = Depends(get_db)) -> RecommendationResponse:
    service = RecommendationService(db)
    row = service.get_for_window(window_id=window_id)
    return _to_response(row, db=db)


@router.post(
    "/slice-windows/{window_id}/recommendation/analyze",
    response_model=WindowMultiAnalysisResponse,
)
def analyze_recommendation(window_id: int, db: Session = Depends(get_db)) -> WindowMultiAnalysisResponse:
    """On-demand multi-fault analysis: suggests per-file labels and whether the
    window should be subdivided. A successful result is persisted so the window
    can be re-opened later without re-running the LLM. Never writes annotations."""
    service = RecommendationService(db)
    return WindowMultiAnalysisResponse(**service.analyze_and_save_window_multi(window_id=window_id))


@router.get(
    "/slice-windows/{window_id}/recommendation/analysis",
    response_model=WindowMultiAnalysisResponse,
)
def get_window_analysis(window_id: int, db: Session = Depends(get_db)) -> WindowMultiAnalysisResponse:
    """Return the last persisted multi-fault analysis for a window (404 if none)."""
    service = RecommendationService(db)
    stored = service.get_stored_analysis(window_id=window_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="该窗口尚无分析记录")
    return WindowMultiAnalysisResponse(**stored)


@router.post(
    "/slice-tasks/{task_id}/recommendations/batch",
    response_model=RecommendationBatchProgressResponse,
)
def start_recommendation_batch(
    task_id: int,
    db: Session = Depends(get_db),
) -> RecommendationBatchProgressResponse:
    service = RecommendationService(db)
    progress = service.start_batch(task_id=task_id, bind=db.get_bind())
    return RecommendationBatchProgressResponse(**progress)


@router.get(
    "/slice-tasks/{task_id}/recommendations/batch",
    response_model=RecommendationBatchProgressResponse,
)
def get_recommendation_batch_progress(
    task_id: int,
    db: Session = Depends(get_db),
) -> RecommendationBatchProgressResponse:
    service = RecommendationService(db)
    return RecommendationBatchProgressResponse(**service.batch_progress(task_id=task_id))
