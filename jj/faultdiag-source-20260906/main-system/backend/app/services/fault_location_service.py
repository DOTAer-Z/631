"""
fault_location_service.py

职责：
    故障定位主编排器。默认输入为故障日志，不再判断 is_fault。

主流程（locate 方法）：
    Step 1  Window 获取：复用 run_id 或 preprocess → ingest → build_windows 现建
    Step 2  Suspicious Window 筛选：按异常分排序取 top-3
    Step 3  多窗口 FAISS 检索：对每个 suspicious window 独立查询
    Step 4  候选聚合：RR score × 窗口权重 × 出现次数奖励
    Step 5  KG 约束 + 重排序：kg_support_score 计算，构建 reasoning_path
    Step 6  置信度计算 + 快慢路径路由（4 维度判断）
    Step 7  LLM Summary（两路均走，fallback 特殊处理）
    Step 8  最终决策 + 写 MySQL + 返回 DiagnosisOut

降级保证（全部在 service 层处理，route 层无需容错）：
    - suspicious_windows 为空  → slow 路径，confidence = 0.0
    - FAISS 无结果            → LLM fallback，confidence clamp ≤ 0.5
    - KG 不可用 / 无支持      → kg_support_score = 0，仍返回完整字段
    - LLM 调用失败            → reasoning = 错误信息，其余字段正常返回
"""

from __future__ import annotations

import logging
import time
import uuid
from math import log
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.database import get_mongo_db
from app.models.diagnosis_record import DiagnosisRecord
from app.schemas.diagnosis import DebugInfo, DiagnosisOut, SimilarCase
from app.services.kg_service_v2 import kg_service_v2
from app.services.llm_service import LLMService
from app.services.log_ingest_service import RunMeta, log_ingest_service
from app.services.log_window_service import log_window_service
from app.services.preprocessing_service import preprocessing_service
from app.services.vector_retrieval_service import vector_retrieval_service

logger = logging.getLogger(__name__)

# ── 路由参数（后续可移入 settings） ───────────────────────────────────────────
_HIGH_SIM       = 0.75   # 高相似度门槛（Criterion A）
_MIN_MARGIN     = 0.05   # top1 vs top2 最小差距（Criterion B）
_CONSISTENCY    = 0.5    # 多窗口一致性比例（Criterion C）
_KG_CONFIRM     = 0.5    # KG 支撑分门槛（Criterion D）
_MAX_WINDOWS    = 3      # suspicious windows 最大数量
_WINDOW_SIZE_S  = 60     # build_windows 参数
_STRIDE_S       = 30
_MIN_ENTRIES    = 1


class FaultLocationService:
    """
    故障定位主服务。

    locate(log_text, db, run_id, top_k, filename) → DiagnosisOut
    所有降级场景在 service 层处理完毕，route 层无需额外容错。
    """

    def __init__(self) -> None:
        self._llm = LLMService()

    def _llm_available(self) -> bool:
        """LLM 是否可通过当前数据库配置或环境变量使用。"""
        return self._llm.is_available()

    # ==========================================================================
    # 公开入口
    # ==========================================================================

    def locate(
        self,
        log_text: str,
        db: Session,
        run_id: Optional[str] = None,
        filename: Optional[str] = "手动输入",
        top_k: int = 5,
        force_refresh: bool = False,
        skip_gate: bool = False,
    ) -> DiagnosisOut:
        """故障定位主入口，返回完整 DiagnosisOut。

        若传入 run_id 且该日志已有诊断记录、未要求强制刷新，则直接复用历史记录
        （存库可重复读取，避免每个日志每次都重新诊断）。

        skip_gate=True 时跳过「故障预测前置门控」：由「软件状态预测」分级为
        严重/紧急后跳转进来，等级已知，无需再调一次大模型判定是否故障——直接
        做根因诊断（省一次调用，并消除同一 run 两轮门控结果不一致的问题）。
        """
        if run_id and not force_refresh:
            cached = self.latest_record_out(run_id, db)
            # 复用历史诊断记录；但从「软件状态预测」严重/紧急跳入(skip_gate=True)时，
            # 不复用旧的「非故障」记录——那是修复前/门控期遗留的「正常」结果，会让
            # 本应有根因的报告回放成"正常"。此时强制重新诊断（结论必为故障）。
            if cached is not None and (cached.is_fault or not skip_gate):
                logger.info("locate: 命中 run_id=%s 的历史诊断记录，直接复用", run_id)
                return cached

        # 报告化诊断：由「软件状态预测」对某份接入报告(report_id=run_id)跳转进来时，
        # 调用方不带文本。此处取该报告的聚合文本（多源数据整体）作为诊断输入——
        # 报告可只含数据库日志，但诊断对象是报告整体，而非强制选某一条日志。
        if run_id and not (log_text and log_text.strip()):
            report_text = self._load_report_text(run_id)
            if report_text:
                log_text = report_text

        t_total_start = time.perf_counter()
        mongo_db = get_mongo_db()

        # Step 1: Window 获取
        run_id, all_windows = self._acquire_windows(
            log_text=log_text,
            run_id=run_id,
            filename=filename,
            mongo_db=mongo_db,
        )

        # 关键修复：按 run_id 诊断时调用方传入的 log_text 为空，
        # 直接喂给大模型会得到「未提供日志内容，无法分析」（且每轮随机）。
        # 这里用该 run 的窗口文本重建有效日志，供预测/检索/大模型统一使用。
        effective_log_text = (
            log_text if (log_text and log_text.strip())
            else self._windows_to_text(all_windows)
        )

        # 故障预测前置门控：必须先由（大模型）预测判定存在风险，才进行故障诊断。
        # 预测为「一般/green」时直接返回非故障结果，避免把所有日志都标成故障。
        # skip_gate=True（由预测页 严重/紧急 跳转进入）时信任已给等级，跳过门控。
        if not skip_gate:
            gate_is_fault, gate_status, gate_summary = self._assess_fault(
                effective_log_text, db
            )
            if not gate_is_fault:
                logger.info(
                    "locate: 预测门控 run_id=%s health=%s → 非故障，跳过诊断",
                    run_id, gate_status,
                )
                return self._build_no_fault_output(
                    db=db, run_id=run_id, log_text=effective_log_text,
                    health_status=gate_status, risk_summary=gate_summary,
                )

        # Step 2: Suspicious Window 筛选
        suspicious = self._select_suspicious_windows(all_windows)

        # Step 3 + 4: 多窗口检索 + 候选聚合（含 embedding 时间）
        t_retrieval_start = time.perf_counter()
        candidate_list = self._retrieve_and_aggregate(suspicious, top_k)
        retrieval_time_ms = (time.perf_counter() - t_retrieval_start) * 1000

        # Step 5: KG 约束 + 重排序
        candidate_list, reasoning_path = self._kg_rerank(
            candidate_list, len(suspicious)
        )

        # Step 6: 置信度计算 + 路由
        channel, final_confidence, top_sim = self._route(
            candidate_list=candidate_list,
            n_windows=len(suspicious),
            reasoning_path=reasoning_path,
        )

        # Step 7: LLM Summary
        top = candidate_list[0] if candidate_list else None
        t_llm_start = time.perf_counter()
        llm_result = self._llm_summary(
            log_text=effective_log_text,
            top_candidate=top,
            candidate_list=candidate_list,
            channel=channel,
            fallback=(top is None),
        )
        llm_time_ms = (time.perf_counter() - t_llm_start) * 1000

        # 真实反映本次是否确实调用了大模型（_llm_summary 打的标记），
        # 不再无条件写 True，便于前端「推理路径」与 debug 看出是否降级。
        llm_ok = bool(llm_result.pop("_llm_ok", False)) if isinstance(llm_result, dict) else False

        total_time_ms = (time.perf_counter() - t_total_start) * 1000

        # ── decision_path + llm_mode（基于 _route() 真实结果赋值）─────────────
        if top is None:
            decision_path = "fallback_llm_infer"
            llm_mode = "fallback_infer"
        elif channel == "fast":
            decision_path = "fast_similarity_summary"
            llm_mode = "fast_summary"
        else:
            decision_path = "slow_llm_analysis"
            llm_mode = "detailed_analysis"

        # 从 reasoning_path 提取 [ROUTE] 行作为 reason 摘要
        reason = next(
            (r for r in reversed(reasoning_path) if r.startswith("[ROUTE]")),
            f"channel={channel}, top_sim={top_sim:.4f}",
        )

        # 在推理路径里留下可见的大模型使用标记（前端「推理路径」可直接看到）
        reasoning_path.append(
            "[LLM] 已调用大模型完整推断根因"
            if llm_ok
            else "[LLM] 未使用大模型（未配置或调用失败），已降级为检索/KG 候选"
        )

        debug = DebugInfo(
            decision_path=decision_path,
            channel=channel,
            llm_called=llm_ok,
            llm_mode=llm_mode,
            similarity_score=round(top_sim, 4) if top_sim else None,
            final_confidence=final_confidence,
            reason=reason,
            n_candidates=len(candidate_list),
            n_windows=len(suspicious),
            embedding_time_ms=0.0,       # embedding 包含在 retrieval_time_ms 内
            retrieval_time_ms=round(retrieval_time_ms, 1),
            llm_time_ms=round(llm_time_ms, 1),
            total_time_ms=round(total_time_ms, 1),
        )
        logger.info(
            "[fault-route] channel=%s decision_path=%s top_sim=%.4f "
            "final_confidence=%.4f llm_mode=%s",
            channel,
            decision_path,
            top_sim or 0.0,
            final_confidence,
            llm_mode,
        )

        # Step 8: 最终决策 + 写 MySQL
        return self._build_output(
            db=db,
            log_text=effective_log_text,
            run_id=run_id,
            channel=channel,
            top_candidate=top,
            candidate_list=candidate_list,
            final_confidence=final_confidence,
            top_sim=top_sim,
            llm_result=llm_result,
            llm_ok=llm_ok,
            reasoning_path=reasoning_path,
            n_windows=len(suspicious),
            debug=debug,
        )

    def latest_record_out(self, run_id: str, db: Session) -> Optional[DiagnosisOut]:
        """返回某日志(run_id)最近一次的诊断记录（转 DiagnosisOut），无则 None。"""
        record = (
            db.query(DiagnosisRecord)
            .filter(DiagnosisRecord.run_id == run_id)
            .order_by(DiagnosisRecord.created_at.desc())
            .first()
        )
        if record is None:
            return None
        return DiagnosisOut(
            id=record.id,
            is_fault=record.is_fault,
            channel_used=record.channel_used,
            fault_type_name=record.fault_type_name,
            similarity_score=record.similarity_score,
            confidence=record.confidence,
            llm_reasoning=record.llm_reasoning,
            created_at=record.created_at,
            run_id=record.run_id,
            root_cause=record.root_cause,
            root_cause_type=record.root_cause_type,
            recovery_hint=record.recovery_hint,
        )

    # ==========================================================================
    # 预测门控 + 文本重建 + 非故障输出
    # ==========================================================================

    @staticmethod
    def _load_report_text(report_id: str) -> Optional[str]:
        """按 report_id(=run_id) 取接入报告的聚合文本；非报告或无文本时返回 None。"""
        try:
            from app.services.prediction_service import PredictionService
            rec = PredictionService()._load_report(report_id)
            text = (rec or {}).get("aggregated_text")
            return text if (text and str(text).strip()) else None
        except Exception as exc:
            logger.warning("读取报告聚合文本失败 report_id=%s: %s", report_id, exc)
            return None

    @staticmethod
    def _windows_to_text(windows: List[dict], max_chars: int = 8000) -> str:
        """把窗口文本按异常分优先拼成有效日志（run_id 诊断时调用方 log_text 为空）。"""
        if not windows:
            return ""
        ordered = sorted(
            windows,
            key=lambda w: (
                w.get("stats", {}).get("critical_events", 0) * 3.0
                + w.get("stats", {}).get("error_events", 0) * 2.0
                + len(w.get("key_events", [])) * 0.5
            ),
            reverse=True,
        )
        parts: List[str] = []
        total = 0
        for w in ordered:
            t = (w.get("text") or "").strip()
            if not t:
                continue
            parts.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return "\n".join(parts)[:max_chars]

    def _assess_fault(self, text: str, db: Session):
        """故障预测前置：返回 (是否故障, health_status, risk_summary)。

        复用 PredictionService（大模型可用时由大模型判定 green/yellow/red），不落库；
        预测不可用时降级为「继续诊断」，避免误拦截真实故障。
        """
        if not text or not text.strip():
            # 连窗口文本都没有：无内容可判，视为非故障，避免把空内容硬判成故障。
            return False, "green", "未提供有效日志内容"
        try:
            from app.services.prediction_service import PredictionService
            svc = PredictionService()
            res = svc.assess(text) or {}
            hs = res.get("health_status", "green")
            return hs in ("yellow", "red"), hs, res.get("risk_summary")
        except Exception as exc:
            logger.warning("预测门控不可用，降级为继续诊断：%s", exc)
            return True, "unknown", None

    def _build_no_fault_output(
        self,
        db: Session,
        run_id: Optional[str],
        log_text: str,
        health_status: str,
        risk_summary: Optional[str],
    ) -> DiagnosisOut:
        """预测未发现风险时的「非故障」诊断结果（写库，可在诊断历史中查看）。"""
        label = {"green": "一般", "unknown": "未知"}.get(health_status, health_status)
        reasoning = (
            f"故障预测（大模型）判定软件状态为「{label}」，未达到故障阈值，未触发故障诊断。"
        )
        summary = risk_summary or "软件状态预测未发现明显异常"
        record = DiagnosisRecord(
            input_log=(log_text or "")[:4000],
            run_id=run_id,
            channel_used="none",
            similarity_score=None,
            fault_type_name=None,
            is_fault=False,
            confidence=0.0,
            llm_reasoning=reasoning,
            root_cause=f"{summary}（预测未发现异常，无需诊断）",
            root_cause_type=None,
            recovery_hint="如确认存在异常，可补充日志或在「软件状态预测」复核后重试诊断。",
        )
        if run_id:
            db.query(DiagnosisRecord).filter(DiagnosisRecord.run_id == run_id).delete(synchronize_session=False)
        db.add(record)
        db.commit()
        db.refresh(record)
        return DiagnosisOut(
            id=record.id,
            is_fault=False,
            channel_used="none",
            fault_type_name=None,
            similarity_score=None,
            confidence=0.0,
            llm_reasoning=reasoning,
            created_at=record.created_at,
            run_id=run_id,
            root_cause=record.root_cause,
            root_cause_type=None,
            recovery_hint=record.recovery_hint,
            reasoning_path=[
                f"[GATE] 预测 health_status={health_status} → 非故障，跳过诊断"
            ],
        )

    # ==========================================================================
    # Step 1: Window 获取
    # ==========================================================================

    def _acquire_windows(
        self,
        log_text: str,
        run_id: Optional[str],
        filename: Optional[str],
        mongo_db,
    ) -> Tuple[str, List[dict]]:
        """
        返回 (run_id, windows)。
        若 run_id 已传且 MongoDB 中存在对应 windows，直接复用；否则现建。
        """
        if run_id:
            windows = self._fetch_windows(run_id, mongo_db)
            if windows:
                logger.info(
                    "locate: reusing %d windows for run_id=%s", len(windows), run_id
                )
                return run_id, windows
            logger.warning(
                "locate: run_id=%s provided but no windows found, rebuilding", run_id
            )

        new_run_id = run_id or str(uuid.uuid4())
        logger.info("locate: building new windows for run_id=%s", new_run_id)

        pp = preprocessing_service.preprocess(
            log_text,
            source_type="locate",
            filename=filename,
        )
        run_meta = RunMeta(run_id=new_run_id, source_type="locate")
        log_ingest_service.ingest(pp, run_meta, mongo_db)
        log_window_service.build_windows(
            run_meta=run_meta,
            mongo_db=mongo_db,
            window_size_s=_WINDOW_SIZE_S,
            stride_s=_STRIDE_S,
            min_entries=_MIN_ENTRIES,
        )

        windows = self._fetch_windows(new_run_id, mongo_db)
        return new_run_id, windows

    @staticmethod
    def _fetch_windows(run_id: str, mongo_db) -> List[dict]:
        """从 MongoDB 拉取指定 run_id 的全部有效窗口（含必要字段）。"""
        return list(
            mongo_db["log_windows"].find(
                {"run_id": run_id, "stats.n_entries": {"$gt": 0}},
                {
                    "text": 1,
                    "stats": 1,
                    "key_events": 1,
                    "window_id": 1,
                    "metadata": 1,
                    "strategy": 1,
                    "_id": 0,
                },
            )
        )

    # ==========================================================================
    # Step 2: Suspicious Window 筛选
    # ==========================================================================

    @staticmethod
    def _priority_score(w: dict) -> float:
        s = w.get("stats", {})
        return (
            s.get("critical_events", 0) * 3.0
            + s.get("error_events", 0) * 2.0
            + len(w.get("key_events", [])) * 0.5
        )

    def _select_suspicious_windows(self, all_windows: List[dict]) -> List[dict]:
        """
        按异常分排序取 top-N suspicious windows。
        若全部得分为 0，兜底取 n_entries 最多的 2 个窗口。
        """
        if not all_windows:
            return []

        scored = [(w, self._priority_score(w)) for w in all_windows]
        suspicious = sorted(
            [(w, s) for w, s in scored if s > 0],
            key=lambda x: x[1],
            reverse=True,
        )

        if suspicious:
            return [w for w, _ in suspicious[:_MAX_WINDOWS]]

        # 兜底：取条目数最多的 2 个
        return sorted(
            all_windows,
            key=lambda w: w.get("stats", {}).get("n_entries", 0),
            reverse=True,
        )[:2]

    # ==========================================================================
    # Step 3 + 4: 多窗口检索 + 候选聚合
    # ==========================================================================

    @staticmethod
    def _query_knowledge_base(text: str, top_k: int) -> List[dict]:
        """查询「知识库管理」写入的同一个 Chroma 向量库，返回相似案例。

        关键修复：诊断的相似度必须来自用户在「知识库管理」录入的案例
        （`create_knowledge_case` / `logs/upload` / `batch-index` 写入的
        Chroma collection `fault_logs`），而不是旧的离线 FAISS 索引
        （部署镜像里通常没有该索引文件 → 相似度恒为「-」）。

        Chroma 使用 cosine 距离，相似度 = 1 - distance（裁剪到 [0,1]）。
        知识库为空时返回 []（调用方据此走大模型兜底，仍保证置信度下限）。
        """
        try:
            from app.services.vector_store import get_vector_store
            vs = get_vector_store()
            if vs.count() == 0:
                return []
            res = vs.query(text, n_results=top_k)
        except Exception as exc:
            logger.warning("知识库(Chroma)检索失败：%s", exc)
            return []

        metadatas = (res.get("metadatas") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        documents = (res.get("documents") or [[]])[0]

        candidates: List[dict] = []
        for i, meta in enumerate(metadatas):
            meta = meta or {}
            dist = distances[i] if i < len(distances) else 1.0
            try:
                sim = 1.0 - float(dist)
            except (TypeError, ValueError):
                sim = 0.0
            sim = max(0.0, min(sim, 1.0))
            candidates.append({
                "fault_type": meta.get("fault_type_name") or meta.get("fault_type") or "__unknown__",
                "similarity": sim,
                "rank": i + 1,
                "case_id": meta.get("log_entry_id") or meta.get("case_id"),
                "root_cause": meta.get("root_cause"),
                "root_cause_type": meta.get("root_cause_type"),
                "window_id": None,
                "document": (documents[i] if i < len(documents) else None),
            })
        return candidates

    def _retrieve_and_aggregate(
        self,
        suspicious_windows: List[dict],
        top_k: int,
    ) -> List[dict]:
        """
        对每个 suspicious window 做知识库(Chroma)检索，
        以 RR score × 窗口异常权重 × 出现次数奖励 聚合候选，返回 top-5。
        """
        if not suspicious_windows:
            return []

        aggregated: Dict[str, dict] = {}

        for win in suspicious_windows:
            text = win.get("text", "").strip()
            if not text:
                continue
            # 窗口权重：异常分对数加权，确保高异常窗口贡献更大
            w_weight = log(1.0 + self._priority_score(win) + 1.0)

            candidates = self._query_knowledge_base(text, top_k)

            for cand in candidates:
                ft = cand.get("fault_type") or "__unknown__"
                # Reciprocal Rank × similarity × 窗口权重
                rr = cand.get("similarity", 0.0) / max(cand.get("rank", 1), 1)

                if ft not in aggregated:
                    aggregated[ft] = {
                        "fault_type": ft,
                        "total_rr_score": 0.0,
                        "appear_count": 0,
                        "max_similarity": 0.0,
                        "best_case": None,
                        "all_cases": [],
                        # KG 字段，Step 5 填充
                        "kg_support_score": 0.0,
                        "kg_root_causes": [],
                        "kg_ft_info": None,
                        "kg_case_rc": None,
                        "reranked_score": 0.0,
                    }

                agg = aggregated[ft]
                agg["total_rr_score"] += rr * w_weight
                agg["appear_count"] += 1
                if cand.get("similarity", 0.0) > agg["max_similarity"]:
                    agg["max_similarity"] = cand["similarity"]
                    agg["best_case"] = dict(cand)
                agg["all_cases"].append(dict(cand))

        if not aggregated:
            return []

        # 最终聚合分：RR 累积分 × 出现次数对数奖励
        for agg in aggregated.values():
            agg["aggregated_score"] = agg["total_rr_score"] * log(
                1.0 + agg["appear_count"]
            )

        return sorted(
            aggregated.values(),
            key=lambda x: x["aggregated_score"],
            reverse=True,
        )[:5]

    # ==========================================================================
    # Step 5: KG 约束 + 重排序
    # ==========================================================================

    def _kg_rerank(
        self,
        candidate_list: List[dict],
        n_windows: int,
    ) -> Tuple[List[dict], List[str]]:
        """
        对每个候选计算 kg_support_score，按 reranked_score 重新排序，
        同步构建 reasoning_path。KG 不可用时 graceful degrade。
        """
        reasoning_path: List[str] = []

        if not candidate_list:
            reasoning_path.append("[RETRIEVAL] no candidates returned from FAISS")
            return candidate_list, reasoning_path

        # 先记录检索层摘要
        top = candidate_list[0]
        top_sim = top["max_similarity"]
        top2_sim = (
            candidate_list[1]["max_similarity"] if len(candidate_list) > 1 else 0.0
        )
        reasoning_path.append(
            f"[RETRIEVAL] top-1: fault_type={top['fault_type']}, "
            f"similarity={top_sim:.3f}, "
            f"appears in {top['appear_count']}/{n_windows} windows"
        )
        if top2_sim > 0:
            reasoning_path.append(
                f"[RETRIEVAL] top-2: fault_type={candidate_list[1]['fault_type']}, "
                f"similarity={top2_sim:.3f}, "
                f"margin={top_sim - top2_sim:.3f}"
            )

        for cand in candidate_list:
            ft = cand["fault_type"]
            if ft == "__unknown__":
                cand["reranked_score"] = cand["aggregated_score"] * 0.7
                continue

            # Check 1: KG 节点存在性（+0.5）
            try:
                ft_info = kg_service_v2.get_fault_type_info(ft)
            except Exception:
                ft_info = None
            node_exists = ft_info is not None
            s1 = 0.5 if node_exists else 0.0

            # Check 2: CAUSED_BY 边累积权重（最多 +0.3）
            try:
                root_causes = kg_service_v2.get_root_cause_by_fault_type(ft)
            except Exception:
                root_causes = []
            s2 = min(
                sum(rc.get("evidence_weight", 1) for rc in root_causes[:3]) * 0.05,
                0.3,
            )

            # Check 3: 案例级别确认（+0.3）
            case_id = (
                cand["best_case"].get("case_id") if cand.get("best_case") else None
            )
            case_rc = None
            s3 = 0.0
            if case_id is not None:
                try:
                    case_rc = kg_service_v2.get_root_cause_by_case_id(case_id)
                    if case_rc and case_rc.get("fault_type") == ft:
                        s3 = 0.3
                except Exception:
                    pass

            cand["kg_support_score"] = s1 + s2 + s3
            cand["kg_root_causes"] = root_causes
            cand["kg_ft_info"] = ft_info
            cand["kg_case_rc"] = case_rc

            # 重排序分：检索 70% + KG 30%
            cand["reranked_score"] = (
                cand["aggregated_score"] * 0.7 + cand["kg_support_score"] * 0.3
            )

            # 只在 top candidate 上记录详细 KG 路径
            if ft == candidate_list[0]["fault_type"]:
                if node_exists:
                    reasoning_path.append(
                        f"[KG] FaultType::{ft} node confirmed in knowledge graph"
                    )
                else:
                    reasoning_path.append(
                        f"[KG] FaultType::{ft} not found in knowledge graph"
                    )
                if root_causes:
                    rc0 = root_causes[0]
                    reasoning_path.append(
                        f"[KG] {ft} CAUSED_BY {rc0['root_cause_type']} "
                        f"(evidence_weight={rc0.get('evidence_weight', '?')})"
                    )
                if s3 > 0:
                    reasoning_path.append(
                        f"[KG] Case::{case_id} HAS_RESULT_FAULT confirmed → {ft}"
                    )

        # 按 reranked_score 重新排序
        candidate_list.sort(key=lambda x: x["reranked_score"], reverse=True)
        return candidate_list, reasoning_path

    # ==========================================================================
    # Step 6: 置信度计算 + 路由
    # ==========================================================================

    def _route(
        self,
        candidate_list: List[dict],
        n_windows: int,
        reasoning_path: List[str],
    ) -> Tuple[str, float, float]:
        """
        返回 (channel, final_confidence, top_similarity)。
        空候选列表时 → slow + confidence=0.0。
        """
        if not candidate_list:
            reasoning_path.append(
                "[ROUTE] no FAISS candidates → slow (LLM fallback)"
            )
            return "slow", 0.0, 0.0

        top = candidate_list[0]
        top_sim = top["max_similarity"]
        top2_sim = (
            candidate_list[1]["max_similarity"]
            if len(candidate_list) > 1
            else 0.0
        )
        gap = top_sim - top2_sim
        appear_ratio = top["appear_count"] / max(n_windows, 1)
        kg_score = top.get("kg_support_score", 0.0)

        A = top_sim >= _HIGH_SIM
        B = gap >= _MIN_MARGIN or top2_sim == 0.0
        C = appear_ratio >= _CONSISTENCY
        D = kg_score >= _KG_CONFIRM

        # 新增：超高相似度豁免
        super_high = top_sim >= 0.9

        channel = "fast" if (A and (B or super_high) and (C or D)) else "slow"

        # 置信度计算
        base = top_sim
        bonus = 0.0
        bonus += appear_ratio * 0.10
        bonus += min(kg_score / 1.1, 1.0) * 0.10
        if gap < _MIN_MARGIN and top2_sim > 0 and not super_high:
            bonus -= 0.08
        final_confidence = round(min(base + bonus, 1.0), 4)

        reasoning_path.append(
            f"[ROUTE] A(sim≥{_HIGH_SIM})={A}, "
            f"B(gap≥{_MIN_MARGIN})={B}, "
            f"C(consist≥{_CONSISTENCY})={C}, "
            f"D(kg≥{_KG_CONFIRM})={D}, "
            f"SUPER(top_sim≥0.9)={super_high} "
            f"→ channel={channel}, confidence={final_confidence}"
        )
        return channel, final_confidence, top_sim

    # ==========================================================================
    # Step 7: LLM Summary
    # ==========================================================================

    def _build_llm_context(
        self,
        top_candidate: Optional[dict],
        candidate_list: List[dict],
    ) -> str:
        """将候选结果格式化为 LLM 上下文字符串。"""
        if not top_candidate:
            return "无 FAISS 检索结果"

        lines: List[str] = []
        ft = top_candidate["fault_type"]
        rc_list = top_candidate.get("kg_root_causes", [])
        rc_str = rc_list[0]["root_cause_type"] if rc_list else "未知"
        lines.append(f"最匹配故障类型：{ft}（根因类型：{rc_str}）")
        lines.append(f"检索相似度：{top_candidate['max_similarity']:.3f}")
        lines.append(f"出现窗口数：{top_candidate['appear_count']}")

        for i, c in enumerate(top_candidate.get("all_cases", [])[:3], 1):
            lines.append(
                f"相似案例{i}：fault_type={c.get('fault_type')}, "
                f"similarity={c.get('similarity', 0):.3f}, "
                f"root_cause={c.get('root_cause') or '未知'}"
            )

        if len(candidate_list) > 1:
            r = candidate_list[1]
            lines.append(
                f"次候选：{r['fault_type']} (similarity={r['max_similarity']:.3f})"
            )

        return "\n".join(lines)

    def _llm_summary(
        self,
        log_text: str,
        top_candidate: Optional[dict],
        candidate_list: List[dict],
        channel: str,
        fallback: bool,
    ) -> dict:
        """
        调用 LLM。

        当 LLM 可用时，统一走「完整推断」模式（fallback_mode=True），
        让大模型自主给出 fault_type / confidence / root_cause / reasoning /
        recovery_hint —— 这些字段在 _build_output 中作为最终判定的权威来源
        （FAISS top_sim 仍保留为 similarity_score 佐证）。
        LLM 不可用时由运行时配置解析抛错，捕获后降级为占位 reasoning，
        此时 _build_output 退回 FAISS/KG 候选行为。
        """
        context_str = self._build_llm_context(top_candidate, candidate_list)
        # LLM 未配置/不可用：明确降级，返回 _llm_ok=False（不再伪装成已调用大模型）。
        if not self._llm_available():
            logger.warning(
                "诊断降级：大模型未配置/不可用，本次未调用 LLM。"
                "请检查模型 API 管理中的激活配置或现有 LLM 环境变量。"
            )
            return {
                "reasoning": "未启用大模型：本次基于检索候选与知识图谱给出结果（启用模型 API 配置或现有 LLM 环境变量后重试可得大模型根因分析）。",
                "recovery_hint": None,
                "_llm_ok": False,
            }
        try:
            # 方案 A：fast 通道（知识库检索到高把握相似案例）→ 信任检索确认的故障类型，
            # 大模型只补简洁摘要/建议（fast_mode=True, fallback_mode=False），省一次完整推理、更快；
            # slow / 无候选 → 完整推断（fallback_mode=True），行为同现状。
            is_fast = (channel == "fast" and top_candidate is not None)
            res = self._llm.locate(
                log_text=log_text,
                context_str=context_str,
                fast_mode=is_fast,
                fallback_mode=(not is_fast),
            )
            if isinstance(res, dict):
                res["_llm_ok"] = True
            return res
        except Exception as exc:
            logger.error("LLM locate failed: %s", exc)
            return {"reasoning": f"LLM 调用失败：{exc}", "recovery_hint": None, "_llm_ok": False}

    # ==========================================================================
    # Step 8: 最终决策 + 写 MySQL + 返回 DiagnosisOut
    # ==========================================================================

    def _build_output(
        self,
        db: Session,
        log_text: str,
        run_id: str,
        channel: str,
        top_candidate: Optional[dict],
        candidate_list: List[dict],
        final_confidence: float,
        top_sim: float,
        llm_result: dict,
        llm_ok: bool,
        reasoning_path: List[str],
        n_windows: int,
        debug: Optional[DebugInfo] = None,
    ) -> DiagnosisOut:
        """
        决策优先级：
          P1: FAISS + KG reranked top candidate（kg_support_score > 0）
          P2: FAISS top candidate（有结果但 KG 无支持）
          P3: LLM fallback（仅 FAISS 无结果时，confidence clamp ≤ 0.5）
        """
        # ── fault_type 决策 ────────────────────────────────────────────────────
        # LLM 可用时以大模型判定为权威；否则沿用 FAISS/KG 候选行为（优雅降级）。
        llm_ft = llm_result.get("fault_type")   # 完整推断/ fallback 模式下有值

        if llm_ok and llm_ft:
            # 大模型判定优先：fault_type / confidence 以 LLM 为准；
            # similarity_score 仍由 FAISS top_sim 提供（佐证，保留不变）。
            ft = llm_ft
            llm_conf = llm_result.get("confidence")
            if isinstance(llm_conf, (int, float)) and llm_conf > 0:
                final_confidence = round(min(float(llm_conf), 1.0), 4)
            reasoning_path.append(
                "[LLM] fault_type / confidence determined by LLM (authoritative)"
            )
        elif top_candidate:
            ft = top_candidate["fault_type"]
            if ft == "__unknown__":
                ft = llm_ft   # fallback for unknowns
        elif llm_ft:
            ft = llm_ft
            # 知识库无匹配：用大模型自报置信度（clamp ≤0.6），避免恒为 0/空
            llm_conf = llm_result.get("confidence")
            if isinstance(llm_conf, (int, float)) and llm_conf > 0:
                final_confidence = round(min(float(llm_conf), 0.6), 4)
            else:
                final_confidence = min(final_confidence, 0.5)
            reasoning_path.append(
                "[FALLBACK] fault_type determined by LLM (low confidence ≤ 0.6)"
            )
        else:
            ft = None

        # ── root_cause 决策 ────────────────────────────────────────────────────
        root_cause: Optional[str] = None
        root_cause_type: Optional[str] = None
        recovery_hint: Optional[str] = llm_result.get("recovery_hint")

        if top_candidate:
            kg_rcs = top_candidate.get("kg_root_causes", [])
            if kg_rcs:
                root_cause = kg_rcs[0].get("root_cause")
                root_cause_type = kg_rcs[0].get("root_cause_type")
                # KG recovery_hint 优先级高于 LLM
                if kg_rcs[0].get("recovery_hint"):
                    recovery_hint = kg_rcs[0]["recovery_hint"]
            else:
                # 从检索元数据取
                bc = top_candidate.get("best_case") or {}
                root_cause = bc.get("root_cause")
                root_cause_type = bc.get("root_cause_type")

        # LLM 可用时以大模型根因为权威（覆盖 FAISS/KG 取到的根因）。
        if llm_ok and llm_result.get("root_cause"):
            root_cause = llm_result["root_cause"]

        # 检索/KG 未给出根因时，回退到大模型给出的根因（保证「诊断结果要有根因」）
        if not root_cause:
            root_cause = llm_result.get("root_cause")
        if not root_cause:
            # 大模型也不可用（降级）：给出明确占位，引导补充知识库
            if top_candidate is None:
                root_cause = "知识库暂无匹配案例，且大模型未启用；建议在「知识库」补充相似故障案例后重试诊断。"

        # ── affected_components ────────────────────────────────────────────────
        affected_components: List[str] = []
        if ft and ft != "__unknown__":
            try:
                affected_components = kg_service_v2.get_affected_components(ft)
            except Exception:
                pass

        # ── similar_cases 输出 ─────────────────────────────────────────────────
        similar_cases_out: List[SimilarCase] = []
        if top_candidate:
            for c in top_candidate.get("all_cases", [])[:5]:
                similar_cases_out.append(
                    SimilarCase(
                        rank=c.get("rank", 0),
                        similarity=c.get("similarity", 0.0),
                        fault_type=c.get("fault_type"),
                        root_cause=c.get("root_cause"),
                        root_cause_type=c.get("root_cause_type"),
                        case_id=c.get("case_id"),
                        window_id=c.get("window_id"),
                    )
                )

        # ── 置信度下限：诊断只在 严重/紧急 等级触发，结论必须有意义 ──────────────
        # 按需求「诊断置信度不得低于 50%」，对故障路径置信度取下限 0.5、封顶 1.0。
        final_confidence = round(min(max(final_confidence, 0.5), 1.0), 4)

        # ── 相似度显示：知识库检索有候选时给出真实匹配百分比（即便较低也展示），
        #    仅当知识库为空 / 无候选时才为 None（前端显示「-」）。相似度来源是
        #    「知识库管理」录入并向量化的案例（同一 Chroma 库 fault_logs）。 ───────
        sim_display = round(top_sim, 4) if (candidate_list and top_sim is not None) else None

        # ── 写 MySQL diagnosis_records ─────────────────────────────────────────
        record = DiagnosisRecord(
            input_log=log_text[:4000],   # 截断，避免超大写入
            run_id=run_id,
            channel_used=channel,
            similarity_score=sim_display,
            fault_type_name=ft,
            is_fault=True,               # 输入默认为故障日志
            confidence=final_confidence,
            llm_reasoning=llm_result.get("reasoning"),
            root_cause=root_cause,
            root_cause_type=root_cause_type,
            recovery_hint=recovery_hint,
        )
        if run_id:
            db.query(DiagnosisRecord).filter(DiagnosisRecord.run_id == run_id).delete(synchronize_session=False)
        db.add(record)
        db.commit()
        db.refresh(record)

        # ── 组装 DiagnosisOut ──────────────────────────────────────────────────
        return DiagnosisOut(
            # 旧字段（前端依赖）
            id=record.id,
            is_fault=True,
            channel_used=channel,
            fault_type_name=ft,
            similarity_score=sim_display,
            confidence=final_confidence,
            llm_reasoning=record.llm_reasoning,
            created_at=record.created_at,
            # 新增可选字段
            run_id=run_id,
            root_cause=root_cause,
            root_cause_type=root_cause_type,
            recovery_hint=recovery_hint,
            affected_components=affected_components or None,
            similar_cases=similar_cases_out or None,
            reasoning_path=reasoning_path or None,
            n_windows_analyzed=n_windows,
            debug=debug,
        )


# ── 全局单例 ───────────────────────────────────────────────────────────────────
fault_location_service = FaultLocationService()
