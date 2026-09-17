from sqlalchemy.orm import Session
from app.services.vector_store import VectorStoreService
from app.services.llm_service import LLMService
from app.models.diagnosis_record import DiagnosisRecord
from app.config import settings


class DiagnosisService:
    def __init__(self):
        self.vector_store = VectorStoreService()
        self.llm = LLMService()
        self.threshold = settings.SIMILARITY_THRESHOLD

    def diagnose(self, log_text: str, db: Session) -> dict:
        results = self.vector_store.query(log_text, n_results=5)

        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]

        if distances:
            top_similarity = 1.0 - distances[0]
        else:
            top_similarity = 0.0

        if top_similarity >= self.threshold and metadatas:
            # 快通道：直接返回最匹配的故障类型
            top_meta = metadatas[0]
            record = DiagnosisRecord(
                input_log=log_text,
                channel_used="fast",
                similarity_score=top_similarity,
                fault_type_name=top_meta.get("fault_type_name"),
                is_fault=True,
                confidence=round(top_similarity, 4),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return {
                "id": record.id,
                "channel_used": "fast",
                "is_fault": True,
                "fault_type_name": top_meta.get("fault_type_name"),
                "similarity_score": round(top_similarity, 4),
                "confidence": round(top_similarity, 4),
                "llm_reasoning": None,
                "created_at": record.created_at,
            }
        else:
            # 慢通道：调用 LLM 进行诊断
            candidate_parts = []
            for i, (doc, meta) in enumerate(zip(documents, metadatas)):
                candidate_parts.append(
                    f"案例{i+1}（故障类型: {meta.get('fault_type_name', '未知')}）:\n{doc[:500]}"
                )
            candidate_context = "\n\n".join(candidate_parts) if candidate_parts else "知识库暂无参考案例"

            llm_result = self.llm.diagnose(log_text, candidate_context)

            record = DiagnosisRecord(
                input_log=log_text,
                channel_used="slow",
                similarity_score=round(top_similarity, 4),
                fault_type_name=llm_result.get("fault_type"),
                is_fault=llm_result.get("is_fault", False),
                confidence=llm_result.get("confidence", 0.0),
                llm_reasoning=llm_result.get("reasoning"),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return {
                "id": record.id,
                "channel_used": "slow",
                "is_fault": llm_result.get("is_fault"),
                "fault_type_name": llm_result.get("fault_type"),
                "similarity_score": round(top_similarity, 4),
                "confidence": llm_result.get("confidence"),
                "llm_reasoning": llm_result.get("reasoning"),
                "created_at": record.created_at,
            }
