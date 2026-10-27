from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass


Split = dict[str, list[int]]
GroupKey = tuple[str, str, str]


@dataclass(frozen=True)
class SplitSample:
    version_id: int
    test_name: str
    platform: str
    sample_class: str | None
    domain: str | None
    fault_type: str | None


@dataclass(frozen=True)
class SplitRatios:
    train: float
    validation: float
    test: float


def build_stratified_split(
    samples: list[SplitSample], ratios: SplitRatios, seed: int
) -> Split:
    """Build the deterministic Test-level split used by ``model_train``."""
    _validate_ratios(ratios)
    split: Split = {"train": [], "validation": [], "test": []}
    groups = _group_samples(samples)

    for key in sorted(groups):
        group = sorted(groups[key], key=lambda sample: (sample.test_name, sample.platform))
        _rng_for_group(seed, key).shuffle(group)
        train_count, validation_count, test_count = _split_counts(len(group), ratios)
        split["train"].extend(sample.version_id for sample in group[:train_count])
        split["validation"].extend(
            sample.version_id
            for sample in group[train_count : train_count + validation_count]
        )
        split["test"].extend(
            sample.version_id
            for sample in group[
                train_count + validation_count : train_count + validation_count + test_count
            ]
        )

    return split


def sample_group_key(sample: SplitSample) -> GroupKey:
    return (
        _metadata_value(sample.sample_class),
        _metadata_value(sample.domain),
        _metadata_value(sample.fault_type),
    )


def _validate_ratios(ratios: SplitRatios) -> None:
    values = (ratios.train, ratios.validation, ratios.test)
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("Split ratios must be finite non-negative numbers")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("Split ratios must sum to 1.0")


def _group_samples(samples: list[SplitSample]) -> dict[GroupKey, list[SplitSample]]:
    groups: dict[GroupKey, list[SplitSample]] = {}
    for sample in samples:
        groups.setdefault(sample_group_key(sample), []).append(sample)
    return groups


def _metadata_value(value: str | None) -> str:
    return "UNKNOWN" if value is None else str(value)


def _rng_for_group(seed: int, key: GroupKey) -> random.Random:
    payload = json.dumps([seed, *key], separators=(",", ":")).encode("utf-8")
    return random.Random(int.from_bytes(hashlib.sha256(payload).digest()[:8], "big"))


def _split_counts(size: int, ratios: SplitRatios) -> tuple[int, int, int]:
    if size == 0:
        return 0, 0, 0
    if size == 1:
        return 1, 0, 0

    values = (ratios.train, ratios.validation, ratios.test)
    quotas = [size * ratio for ratio in values]
    counts = [math.floor(quota) for quota in quotas]
    counts[0] = max(counts[0], 1)

    while sum(counts) > size:
        candidates = [idx for idx, count in enumerate(counts) if count > (1 if idx == 0 else 0)]
        idx = min(candidates, key=lambda candidate: (quotas[candidate] - counts[candidate], candidate))
        counts[idx] -= 1

    while sum(counts) < size:
        idx = max(range(3), key=lambda candidate: (quotas[candidate] - counts[candidate], -candidate))
        counts[idx] += 1

    return counts[0], counts[1], counts[2]
