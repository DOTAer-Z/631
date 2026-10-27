from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.training_worker.child import JsonlMetricCallback


def _last_record(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_metric_callback_uses_finite_train_loss_when_loss_is_absent(tmp_path):
    path = tmp_path / "metrics.jsonl"
    callback = JsonlMetricCallback(path)

    callback.on_log(
        state=SimpleNamespace(global_step=1, epoch=0.125),
        logs={"train_loss": 1.407, "epoch": 0.125},
    )

    assert _last_record(path)["loss"] == pytest.approx(1.407)


def test_metric_callback_prefers_regular_loss_including_zero(tmp_path):
    path = tmp_path / "metrics.jsonl"
    callback = JsonlMetricCallback(path)

    callback.on_log(
        state=SimpleNamespace(global_step=2, epoch=0.25),
        logs={"loss": 0, "train_loss": 9.5},
    )

    assert _last_record(path)["loss"] == 0.0


@pytest.mark.parametrize("value", [True, "1.407", float("nan"), float("inf")])
def test_metric_callback_rejects_invalid_train_loss_fallbacks(tmp_path, value):
    path = tmp_path / "metrics.jsonl"
    callback = JsonlMetricCallback(path)

    callback.on_log(
        state=SimpleNamespace(global_step=3, epoch=0.5),
        logs={"train_loss": value},
    )

    assert _last_record(path)["loss"] is None
