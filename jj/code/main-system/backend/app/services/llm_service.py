import json
import logging
import re
import time
from collections.abc import Callable

from app.services.llm_runtime_config import llm_runtime_config_provider
from app.services.log_parse_errors import LogParseCancelled, LogParseTimedOut

logger = logging.getLogger(__name__)

# 异常行优先级别（用于给大模型的输入组织：先挑这些行，避免长日志把根因线索截在后面）
_EVIDENCE_LEVEL_RE = re.compile(r"\b(CRITICAL|FATAL|PANIC|ERROR|WARN(?:ING)?)\b", re.IGNORECASE)


def _pick_evidence_lines(log_text: str, max_chars: int = 3000) -> str:
    """异常行优先地组织日志输入：先取含 CRITICAL/FATAL/PANIC/ERROR/WARN 的行，
    再补普通行，直到 max_chars。避免对长日志简单 [:N] 截断把关键错误行切掉。"""
    if not log_text:
        return ""
    lines = log_text.splitlines()
    if len(log_text) <= max_chars:
        return log_text
    abnormal, normal = [], []
    for ln in lines:
        (abnormal if _EVIDENCE_LEVEL_RE.search(ln) else normal).append(ln)
    picked, total = [], 0
    for ln in abnormal + normal:
        if total + len(ln) + 1 > max_chars:
            break
        picked.append(ln)
        total += len(ln) + 1
    return "\n".join(picked)


class LLMService:
    """OpenAI 兼容的大模型客户端（DashScope / 本地 vLLM 如 dsqwen32b 等均可）。

    每次调用优先解析数据库中的激活配置；没有激活配置时，继续兼容现有
    ENABLE_LLM / LLM_* 环境变量。调用期间使用不可变的本地运行时句柄。
    """

    def __init__(self, runtime_provider=None):
        self._runtime_provider = runtime_provider or llm_runtime_config_provider

    def is_available(self) -> bool:
        try:
            return self._runtime_provider.resolve() is not None
        except Exception:
            logger.warning(
                "[LLMService] runtime configuration is unavailable",
                exc_info=False,
            )
            return False

    def _runtime(self):
        resolution_failed = False
        try:
            runtime = self._runtime_provider.resolve()
        except Exception:
            resolution_failed = True
            runtime = None
        if resolution_failed:
            raise RuntimeError(
                "LLM runtime configuration could not be resolved"
            ) from None
        if runtime is None:
            raise RuntimeError(
                "大模型未配置或已关闭：请在模型 API 管理中启用配置，或设置现有 LLM 环境变量。"
            )
        return runtime

    @staticmethod
    def _chat_completion_content(runtime, **request):
        error_message = None
        try:
            response = runtime.client.chat.completions.create(**request)
            content = runtime.redact(response.choices[0].message.content)
        except Exception as exc:
            detail = runtime.redact(str(exc)).strip()
            error_message = (
                f"LLM Chat Completions request failed: {detail}"
                if detail
                else "LLM Chat Completions request failed"
            )
        if error_message is not None:
            raise RuntimeError(error_message) from None
        return content

    @staticmethod
    def _stream_chat_completion_content(
        runtime,
        *,
        should_cancel: Callable[[], bool] | None,
        on_progress: Callable[[int, int], None] | None,
        deadline: float | None,
        **request,
    ) -> str:
        options = {"max_retries": 0}
        if deadline is not None:
            options["timeout"] = max(0.001, deadline - time.monotonic())
        client = runtime.client.with_options(**options)
        stream = None
        content_result = ""
        error_message = None
        try:
            stream = client.chat.completions.create(**request, stream=True)
            content_parts: list[str] = []
            received_units = 0
            budget_units = max(1, int(request.get("max_tokens", 1)) * 4)
            for chunk in stream:
                if should_cancel is not None and should_cancel():
                    raise LogParseCancelled("日志解析已取消")
                if deadline is not None and time.monotonic() >= deadline:
                    raise LogParseTimedOut("日志解析超过 60 秒")
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta
                content = getattr(delta, "content", None) or ""
                reasoning = getattr(delta, "reasoning_content", None) or ""
                if not reasoning:
                    model_extra = getattr(delta, "model_extra", None) or {}
                    reasoning = model_extra.get("reasoning_content") or ""
                content_parts.append(content)
                received_units += len(content) + len(reasoning)
                if on_progress is not None:
                    on_progress(received_units, budget_units)
            content_result = runtime.redact("".join(content_parts)) or ""
        except (LogParseCancelled, LogParseTimedOut):
            raise
        except Exception as exc:
            if deadline is not None and time.monotonic() >= deadline:
                raise LogParseTimedOut("日志解析超过 60 秒") from None
            detail = (runtime.redact(str(exc)) or "").strip()
            error_message = (
                f"LLM Chat Completions request failed: {detail}"
                if detail
                else "LLM Chat Completions request failed"
            )
        finally:
            if stream is not None:
                stream.close()
        if error_message is not None:
            raise RuntimeError(error_message) from None
        return content_result

    def diagnose(self, log_text: str, candidate_context: str) -> dict:
        runtime = self._runtime()
        # 说明：保留兼容，非主诊断路径。当前故障诊断实走 fault_location_service.locate()，
        # 本方法未被 router 调用；如需启用旧诊断链再复用此 prompt。
        system_prompt = (
            "你是一名资深软件故障诊断专家。\n"
            "用户会提供一段系统日志，以及来自知识库的参考故障案例。\n"
            "请分析日志，判断是否存在故障，如存在请给出故障类型、置信度（0-1）和详细推理。\n"
            "必须以JSON格式返回，格式如下：\n"
            '{"is_fault": true, "fault_type": "故障类型名称", "confidence": 0.85, "reasoning": "推理过程"}\n'
            "如果没有故障：\n"
            '{"is_fault": false, "fault_type": null, "confidence": 0.9, "reasoning": "推理过程"}'
        )
        user_content = f"## 待诊断日志\n{log_text}\n\n## 参考知识库案例\n{candidate_context}\n\n请分析并返回JSON格式结果。"

        content = self._chat_completion_content(
            runtime,
            model=runtime.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=runtime.max_output_tokens,
        )
        # 提取 JSON（兼容模型输出包含额外文字的情况）
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        return json.loads(content)

    def predict(self, log_text: str, metrics: dict) -> dict:
        runtime = self._runtime()
        # 语义：本方法服务于「预测预警」页——输入是一份【当前已判定为正常】的运行数据，
        # 要做的是【前瞻性风险预测与预警】（当前正常、未来是否可能劣化/出问题），
        # 而不是重复判断"当前是否故障"。输出契约保持 green/yellow/red 不变（前端依赖）。
        system_prompt = (
            "你是一名系统可靠性与风险预警专家。\n"
            "用户会提供一段【当前运行正常】的系统运行数据（以日志为主，含可选系统指标）。\n"
            "请把它作为一个【整体】来做【前瞻性风险预测与预警】：判断系统在当前基础上"
            "是否存在潜在风险或早期劣化趋势、未来是否可能演变为故障，而不是判断它现在是否已故障。\n"
            "必须以JSON格式返回：\n"
            '{"health_status": "green/yellow/red", "risk_summary": "预测结论与预警", '
            '"risk_details": [{"type": "风险类别", "level": "low/medium/high", "detail": "依据 + 建议关注/预防措施"}]}\n'
            "预警分级标准（三级，面向【未来风险】而非【当前故障】）：\n"
            "- green（无预警）：运行平稳，无明显劣化信号，短期内无需预警；\n"
            "- yellow（需关注/预警）：出现早期劣化或潜在风险信号（如偶发重试、资源缓慢上升、"
            "非致命告警苗头、时延抖动等），需预警并持续关注；\n"
            "- red（高风险/紧急预警）：存在很可能在近期演变为故障的高风险信号（如资源逼近上限、"
            "错误率上升趋势、关键依赖不稳定等），需立即预防处置。\n"
            "risk_summary 用一句话概括预测结论（是否有风险、需关注什么）；"
            "risk_details 列出各潜在风险项及其依据与建议的关注/预防措施，无风险时可为空数组。\n"
            "重点：对 yellow / red，risk_summary 必须明确点出【需要规避/预防的问题】，"
            "每个 risk_details.detail 都要给出【风险依据 + 具体的规避/预防措施】（预测预警自成闭环，"
            "不再转交故障诊断）。"
            "对 yellow / red，尽量指出【风险的触发条件或预计窗口】"
            "（如「按当前增速约 N 天到阈值」「并发升高时易触发」），无从估计则省略。"
        )
        cpu = metrics.get("cpu_usage", "N/A")
        mem = metrics.get("memory_usage", "N/A")
        disk = metrics.get("disk_usage", "N/A")
        temp = metrics.get("temperature", "N/A")
        user_content = (
            f"## 系统日志\n{log_text}\n\n"
            f"## 系统指标\n"
            f"- CPU使用率: {cpu}%\n"
            f"- 内存使用率: {mem}%\n"
            f"- 磁盘使用率: {disk}%\n"
            f"- 系统温度: {temp}°C\n\n"
            "请对该系统做前瞻性风险预测与预警，并返回JSON。"
        )

        content = self._chat_completion_content(
            runtime,
            model=runtime.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
        )
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("[LLMService.predict] JSON parse failed: %r", content[:200])
            return None

    def locate(
        self,
        log_text: str,
        context_str: str,
        fast_mode: bool = True,
        fallback_mode: bool = False,
    ) -> dict:
        """
        故障定位专用 LLM 调用。

        fast_mode=True   : 故障类型已由 FAISS+KG 确认，只生成简洁摘要和建议
        fast_mode=False  : 置信度不足，生成详细分析（不推翻检索结论）
        fallback_mode    : FAISS 无结果，允许 LLM 兜底推断故障类型（低置信）

        返回
        ----
        正常路径：{"reasoning": str, "recovery_hint": str | None}
        fallback ：{"fault_type": str | None, "reasoning": str, "recovery_hint": str | None}
        """
        runtime = self._runtime()
        if fallback_mode:
            system_prompt = (
                "你是一名资深软件故障诊断专家。\n"
                "下面这段数据已由软件状态预测判定为「严重/紧急」，请对其做根因诊断。\n"
                "知识库参考案例可能为空；无论是否有参考，都要结合日志本身给出判断。\n"
                "核心要求：root_cause 必须给出【问题的本质 / 根本原因】——即"
                "「为什么会发生」的深层原因，而不是复述表面症状或错误信息。\n"
                "必须以 JSON 格式返回，字段如下：\n"
                '{"fault_type": "故障类型（简洁中文短语，必填）", '
                '"confidence": 0.0-1.0 你对该判断的置信度, '
                '"root_cause": "问题本质/根本原因（一句话，必填，说明为什么发生）", '
                '"reasoning": "分析过程", '
                '"recovery_hint": "处理建议或 null"}'
            )
            user_content = (
                f"## 待诊断数据（异常行优先，已聚焦关键错误/告警行）\n{_pick_evidence_lines(log_text)}\n\n"
                f"## 知识库参考案例\n{context_str}\n\n"
                "请判断故障类型，并给出问题的本质（根本原因）、置信度与处理建议。结论须基于上方证据行，不臆造。"
            )
        elif fast_mode:
            system_prompt = (
                "你是一名软件故障诊断专家。\n"
                "故障类型已由检索系统确认，请生成简洁诊断摘要（1-3 句），\n"
                "说明主要故障特征和处理方向，不需要重新判断故障类型。结论须基于证据行，不臆造。\n"
                "必须以 JSON 格式返回：\n"
                '{"reasoning": "诊断摘要", "recovery_hint": "处理建议或 null"}'
            )
            user_content = (
                f"## 检索确认结果\n{context_str}\n\n"
                f"## 日志片段（异常行优先）\n{_pick_evidence_lines(log_text)}\n\n"
                "请生成简洁诊断摘要和处理建议。"
            )
        else:
            system_prompt = (
                "你是一名软件故障诊断专家。\n"
                "检索置信度不足，候选结果存在争议。\n"
                "请结合日志内容和候选证据，生成详细诊断分析，\n"
                "重点解释最可能的故障表现和根因推断，不得推翻检索已给出的候选结论。结论须基于证据行，不臆造。\n"
                "必须以 JSON 格式返回：\n"
                '{"reasoning": "详细分析", "recovery_hint": "处理建议或 null"}'
            )
            user_content = (
                f"## 检索候选结果（置信度不足）\n{context_str}\n\n"
                f"## 日志内容（异常行优先）\n{_pick_evidence_lines(log_text)}\n\n"
                "请进行详细分析，补充根因推断。"
            )

        content = self._chat_completion_content(
            runtime,
            model=runtime.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        try:
            return json.loads(content)
        except Exception:
            # JSON 解析失败时，将原始输出作为 reasoning 返回，不阻断链路
            return {"reasoning": content, "recovery_hint": None}

    def preprocess(self, raw_text: str) -> dict:
        """清洗日志文本，提取摘要、关键词、建议故障类型"""
        runtime = self._runtime()
        system_prompt = (
            "你是一名日志数据处理专家。\n"
            "用户会提供原始日志文本，请完成以下任务：\n"
            "1. 清洗文本：去除冗余时间戳前缀、标准化格式；**保留错误/告警等关键行，去掉纯噪声行**\n"
            "2. 提取摘要：用1-2句话描述日志的核心问题\n"
            "3. 提取关键词：最多5个技术关键词\n"
            "4. 建议故障类型：根据内容推测最可能的故障类别名称；**无明显故障时填 null，不要硬猜**\n"
            "必须以JSON格式返回：\n"
            '{"cleaned_text": "清洗后文本", "summary": "摘要", '
            '"extracted_keywords": ["关键词1", "关键词2"], '
            '"suggested_fault_type": "建议类型名或null"}'
        )
        user_content = f"## 原始日志\n{raw_text}\n\n请处理并返回JSON。"

        content = self._chat_completion_content(
            runtime,
            model=runtime.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        return json.loads(content)

    # =========================================================================
    # 日志解析专用：parse_log
    # 用于 /api/v1/log-analysis/analyze/* 与 /log-analysis/parse/* 流水线，
    # 在规则提取的 timestamps / error_codes / stack_traces 之上追加语义层信息：
    #   - 关键事件序列（按时间或行号顺序）
    #   - 故障信号（症状）
    #   - 严重度（low / medium / high / critical）
    #   - 推断的故障类型与置信度
    #   - 自然语言根因分析与建议
    # 与 preprocess 不同点：preprocess 偏「清洗+摘要」，parse_log 偏「解析+诊断」。
    # =========================================================================
    def parse_log(
        self,
        raw_text: str,
        max_chars: int = 4000,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        deadline: float | None = None,
    ) -> dict:
        """
        日志解析增强（LLM）。

        Args:
            raw_text: 原始日志文本。过长会按 max_chars 截断（首尾各保留一半）。
            max_chars: 单次喂给 LLM 的最大字符数，默认 4000（输入小一点，给输出留空间）。

        Returns:
            dict（结构稳定，前端可直接绑定）：
              {
                "summary":             str,            # 1-2 句话核心问题
                "severity":            "low|medium|high|critical",
                "parsed_events":       [               # 解析出的关键事件，按发生顺序，最多 5 条
                    {"ts": str|null, "level": str, "module": str|null,
                     "message": str, "evidence_line": int|null}
                ],
                "fault_signals":       [str, ...],     # 症状关键词，最多 6 个
                "suggested_fault_type": str|null,      # 推断的故障类别名称
                "confidence":          float,          # 0.0 - 1.0
                "root_cause_analysis": str,            # 自然语言根因
                "recovery_hint":       str|null        # 处理建议（可空）
              }

        异常：
            未配置 LLM 时由 _runtime 抛 RuntimeError；调用方应捕获并降级。
        """
        runtime = self._runtime()

        # 截断：长日志只保留头部 + 尾部，避免吃掉上下文窗口
        if len(raw_text) > max_chars:
            half = max_chars // 2
            raw_text = (
                raw_text[:half]
                + f"\n... [省略 {len(raw_text) - max_chars} 字符] ...\n"
                + raw_text[-half:]
            )

        system_prompt = (
            "你是一名资深嵌入式 / 服务端日志解析专家，擅长从原始日志中提炼结构化信息和故障语义。\n"
            "用户会提供一段原始日志，请按以下要求解析并返回严格 JSON：\n"
            "1) summary：用1-2句话概括日志反映的核心问题（无故障时也要总结）\n"
            "2) severity：四档之一 low / medium / high / critical。判据（就高不就低）：\n"
            "   - critical：出现致命/崩溃/不可恢复错误（FATAL/PANIC/CRITICAL、段错误、OOM 导致重启、看门狗复位、服务不可用）；\n"
            "   - high：出现 ERROR / 异常 / 失败并已影响功能（即便可恢复，只要报了 ERROR 级或功能受损）；\n"
            "   - medium：无 ERROR，但有 WARN/告警、偶发重试、资源接近阈值、时延抖动等潜在风险或早期劣化苗头；\n"
            "   - low：仅 INFO/DEBUG，无错误无告警，运行正常。\n"
            "   注意：medium 及以上均视为『异常』，low 视为『正常』。\n"
            "3) parsed_events：按发生顺序列出**最多 5 条**关键事件（不是全部！只挑最有诊断价值的），"
            "每条 message 控制在 80 字符以内；包含 ts/level/module/message/evidence_line "
            "（无法判断的字段填 null，evidence_line 是事件在原日志中的行号 1-based；"
            "evidence_line 必须是输入日志中真实存在的行号，无法确定填 null，不得杜撰）\n"
            "4) fault_signals：最多 6 个症状关键词或短语（如 'OOM'、'tcp_reset'、'mutex_deadlock'）\n"
            "5) suggested_fault_type：用一个简洁中文短语描述推断故障类型，无明显故障时填 null\n"
            "6) confidence：你对 suggested_fault_type 的置信度，浮点 [0,1]\n"
            "7) root_cause_analysis：50-150 字的中文根因分析（精简，不要列点）\n"
            "8) recovery_hint：可执行的处理/恢复建议 1-2 句，不确定时填 null\n"
            "\n严格只返回 JSON，不要 ```、不要解释文字、不要超过 800 token。"
        )
        user_content = f"## 原始日志\n{raw_text}\n\n请按要求返回 JSON。"

        # max_tokens 给到 1500，覆盖默认 1024 防止结构化输出被截断（events + root_cause 都要写）
        content = self._stream_chat_completion_content(
            runtime,
            should_cancel=should_cancel,
            on_progress=on_progress,
            deadline=deadline,
            model=runtime.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=max(runtime.max_output_tokens, 1500),
        )
        content = content or ""
        # 提取 JSON（兼容模型在前后包额外文字 / ``` 代码块的情况）
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        try:
            result = json.loads(content)
            # 为每个 parsed_event 分配 1-based 顺序 id，便于前端区分各条事件
            for i, ev in enumerate(result.get("parsed_events") or []):
                if isinstance(ev, dict):
                    ev["id"] = i + 1
            return result
        except Exception:
            # 长日志输出被截断 / 模型输出不规范时，记 info（不是 warning），
            # 返回最小可用结构让前端不至于啥都看不见。完整原文截断后放进 summary 占位。
            logger.info(
                "[LLMService.parse_log] JSON 解析失败（很可能输出被截断），降级为最小结构。"
                " content_head=%r",
                content[:120],
            )
            return {
                "summary": (content[:200] + "…") if content else "LLM 输出不可解析，已降级",
                "severity": "low",
                "parsed_events": [],
                "fault_signals": [],
                "suggested_fault_type": None,
                "confidence": 0.0,
                "root_cause_analysis": "",
                "recovery_hint": None,
                "_truncated": True,
            }
