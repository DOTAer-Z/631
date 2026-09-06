from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.services.llm import LLMError

_SYSTEM_PROMPT = (
    "You are a log timestamp format detector. Given several sample log lines, "
    "determine how to extract and parse the leading timestamp of each line. "
    "Respond with a single JSON object and nothing else."
)

_USER_TEMPLATE = """Analyze these sample log lines and return how to parse their timestamp.

Return a JSON object with exactly these fields:
- "kind": "epoch" if the timestamp is a Unix epoch number (seconds), otherwise "strptime".
- "regex": a Python `re` regular expression whose group 1 captures the timestamp substring from a line (use re.search semantics).
- "strptime_fmt": Python datetime.strptime format string for the captured substring when kind is "strptime"; empty string when kind is "epoch".
- "timezone": IANA timezone name to assume for naive datetimes (e.g. "Asia/Shanghai"); use "UTC" for epoch or timezone-aware timestamps.
- "ts_at_line_start": true if the timestamp is at the start of the line.

Sample lines:
{samples}

JSON:"""


@dataclass(frozen=True)
class TimestampFormatSpec:
    kind: str  # "epoch" | "strptime"
    regex: str
    strptime_fmt: str
    timezone: str
    ts_at_line_start: bool

    def normalized_key(self) -> tuple:
        return (self.kind, self.regex, self.strptime_fmt, self.timezone, self.ts_at_line_start)


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def apply_spec(spec: TimestampFormatSpec, raw_line: str) -> tuple[float | None, str]:
    """Deterministically parse a single line using an inferred spec.

    Returns (utc_epoch_float | None, content). content is the stripped line, matching
    the rule-based parser's behavior so downstream storage is consistent.
    """
    content = raw_line.strip()
    if not content:
        return None, ""

    pattern = _compiled(spec.regex)
    if pattern is None:
        return None, content

    match = pattern.search(content)
    if match is None:
        return None, content
    ts_text = match.group(1) if match.groups() else match.group(0)
    ts_text = ts_text.strip()
    if not ts_text:
        return None, content

    try:
        if spec.kind == "epoch":
            return float(ts_text), content

        normalized = ts_text
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        dt = datetime.strptime(normalized, spec.strptime_fmt)
        if dt.tzinfo is None:
            tz_name = spec.timezone or get_settings().import_default_timezone
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.astimezone(timezone.utc).timestamp(), content
    except Exception:  # noqa: BLE001 - any malformed spec/value degrades to "no timestamp"
        return None, content


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(cleaned[start : end + 1])


def _spec_from_payload(payload: dict) -> TimestampFormatSpec | None:
    kind = str(payload.get("kind", "")).strip().lower()
    if kind not in {"epoch", "strptime"}:
        return None
    regex = str(payload.get("regex", "")).strip()
    if not regex:
        return None
    strptime_fmt = str(payload.get("strptime_fmt", "") or "")
    tz = str(payload.get("timezone", "") or "") or get_settings().import_default_timezone
    ts_at_line_start = bool(payload.get("ts_at_line_start", True))
    if kind == "strptime" and not strptime_fmt:
        return None
    return TimestampFormatSpec(
        kind=kind,
        regex=regex,
        strptime_fmt=strptime_fmt,
        timezone=tz,
        ts_at_line_start=ts_at_line_start,
    )


def infer_format(llm_client, sample_lines: list[str]) -> TimestampFormatSpec | None:
    """Ask the LLM for a timestamp format spec, then re-validate it locally.

    Returns None on any failure (LLM error, bad JSON, regex error, or too few
    sample lines parsed) so callers fall back to rule-based parsing.
    """
    samples = [line for line in sample_lines if line.strip()]
    if not samples:
        return None

    settings = get_settings()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(samples="\n".join(samples))},
    ]

    try:
        raw = llm_client.chat(
            messages,
            purpose="annotation_timestamp_inference",
            json_mode=True,
            temperature=0.0,
        )
    except LLMError:
        return None
    except Exception:  # noqa: BLE001
        return None

    try:
        payload = _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        return None

    spec = _spec_from_payload(payload)
    if spec is None:
        return None

    # Local re-validation: the spec must parse a sufficient fraction of samples.
    parsed = sum(1 for line in samples if apply_spec(spec, line)[0] is not None)
    if parsed / len(samples) < settings.timestamp_infer_min_valid_ratio:
        return None
    return spec
