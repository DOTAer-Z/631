from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.graph_service import GraphService
from app.utils.cache import cache_get, cache_set, GRAPH_CACHE_KEY, GRAPH_CACHE_TTL

router = APIRouter()


@router.get("/graph")
def get_graph(
    db: Session = Depends(get_db),
    view: str = Query("overview", description="overview | fault_type | root_cause | subsystem"),
    focus: Optional[str] = Query(None, description="焦点节点名称或 ID，用于 fault_type / root_cause / subsystem 视图"),
    center_id: Optional[str] = Query(None, description="1-跳子图（向后兼容，非主路径）"),
    max_nodes: int = Query(80, ge=1, le=500),
    max_edges: int = Query(120, ge=1, le=1000),
):
    # 只对默认参数组合走缓存，避免不同视图互相污染
    is_default = (
        view == "overview"
        and focus is None
        and center_id is None
        and max_nodes == 80
        and max_edges == 120
    )
    if is_default:
        cached = cache_get(GRAPH_CACHE_KEY)
        if cached:
            return cached

    svc = GraphService()
    data = svc.build_graph(
        db,
        view=view,
        focus=focus,
        center_id=center_id,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )

    if is_default:
        cache_set(GRAPH_CACHE_KEY, data, ttl=GRAPH_CACHE_TTL)
    return data
