from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.dashboard import (
    DashboardSummaryResponse,
    RecentAnnotationListResponse,
    RecentPackageListResponse,
    RecentSliceTaskListResponse,
)
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummaryResponse:
    return DashboardService(db).get_summary()


@router.get("/recent-packages", response_model=RecentPackageListResponse)
def get_dashboard_recent_packages(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RecentPackageListResponse:
    return RecentPackageListResponse(items=DashboardService(db).get_recent_packages(limit=limit))


@router.get("/recent-slice-tasks", response_model=RecentSliceTaskListResponse)
def get_dashboard_recent_slice_tasks(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RecentSliceTaskListResponse:
    return RecentSliceTaskListResponse(items=DashboardService(db).get_recent_slice_tasks(limit=limit))


@router.get("/recent-annotations", response_model=RecentAnnotationListResponse)
def get_dashboard_recent_annotations(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RecentAnnotationListResponse:
    return RecentAnnotationListResponse(items=DashboardService(db).get_recent_annotations(limit=limit))
