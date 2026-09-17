from __future__ import annotations

import json

from app.services.llm.timestamp_inference import (
    TimestampFormatSpec,
    apply_spec,
    infer_format,
)
from tests.fakes import FakeLLMClient


def test_apply_spec_epoch() -> None:
    spec = TimestampFormatSpec(
        kind="epoch",
        regex=r"(\d+(?:\.\d+)?)",
        strptime_fmt="",
        timezone="UTC",
        ts_at_line_start=True,
    )
    ts, content = apply_spec(spec, "1717286940.5 hello world")
    assert ts == 1717286940.5
    assert content == "1717286940.5 hello world"


def test_apply_spec_strptime_naive_uses_timezone() -> None:
    spec = TimestampFormatSpec(
        kind="strptime",
        regex=r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)",
        strptime_fmt="%Y-%m-%d %H:%M:%S.%f",
        timezone="Asia/Shanghai",
        ts_at_line_start=True,
    )
    ts, _ = apply_spec(spec, "2018-09-29 16:12:39.365 alpha")
    assert ts == 1538208759.365


def test_apply_spec_returns_none_when_regex_no_match() -> None:
    spec = TimestampFormatSpec(
        kind="epoch", regex=r"^(\d+)", strptime_fmt="", timezone="UTC", ts_at_line_start=True
    )
    ts, content = apply_spec(spec, "no-timestamp-here")
    assert ts is None
    assert content == "no-timestamp-here"


def test_apply_spec_bad_regex_degrades() -> None:
    spec = TimestampFormatSpec(
        kind="epoch", regex=r"(unterminated", strptime_fmt="", timezone="UTC", ts_at_line_start=True
    )
    assert apply_spec(spec, "1.0 x") == (None, "1.0 x")


def test_infer_format_accepts_validated_spec() -> None:
    payload = json.dumps(
        {
            "kind": "epoch",
            "regex": r"(\d+(?:\.\d+)?)",
            "strptime_fmt": "",
            "timezone": "UTC",
            "ts_at_line_start": True,
        }
    )
    client = FakeLLMClient(responses=payload)
    spec = infer_format(client, ["1.0 a", "2.5 b", "3.0 c"])
    assert spec is not None
    assert spec.kind == "epoch"
    assert client.purposes == ["annotation_timestamp_inference"]


def test_infer_format_rejects_hallucinated_spec() -> None:
    # Spec's regex never matches the samples -> validation ratio 0 -> rejected.
    payload = json.dumps(
        {"kind": "epoch", "regex": r"(ZZZZ\d+)", "strptime_fmt": "", "timezone": "UTC", "ts_at_line_start": True}
    )
    client = FakeLLMClient(responses=payload)
    assert infer_format(client, ["1.0 a", "2.0 b"]) is None


def test_infer_format_handles_llm_error() -> None:
    client = FakeLLMClient(configured=False)
    assert infer_format(client, ["1.0 a"]) is None


def test_infer_format_handles_bad_json() -> None:
    client = FakeLLMClient(responses="not json at all")
    assert infer_format(client, ["1.0 a"]) is None
