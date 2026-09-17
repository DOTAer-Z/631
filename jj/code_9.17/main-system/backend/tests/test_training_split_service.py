import math
import unittest

from app.services.training_split_service import (
    SplitRatios,
    SplitSample,
    build_stratified_split,
)


class TrainingSplitServiceTests(unittest.TestCase):
    def _sample(
        self,
        version_id: int,
        test_name: str,
        sample_class: str | None = "fault",
        domain: str | None = "kernel",
        fault_type: str | None = "watchdog",
    ) -> SplitSample:
        return SplitSample(
            version_id=version_id,
            test_name=test_name,
            platform="NuttX",
            sample_class=sample_class,
            domain=domain,
            fault_type=fault_type,
        )

    def test_split_is_deterministic_for_input_order_and_uses_version_ids_as_payload(self):
        samples = [
            self._sample(91, "Test_4"),
            self._sample(17, "Test_1"),
            self._sample(53, "Test_3"),
            self._sample(24, "Test_2"),
            self._sample(80, "Test_5"),
            self._sample(62, "Test_6"),
        ]

        result = build_stratified_split(samples, SplitRatios(0.8, 0.1, 0.1), 631)

        self.assertEqual(
            result,
            {
                "train": [80, 91, 17, 24, 62],
                "validation": [53],
                "test": [],
            },
        )
        self.assertEqual(
            result,
            build_stratified_split(
                list(reversed(samples)), SplitRatios(0.8, 0.1, 0.1), 631
            ),
        )

    def test_split_is_deterministic_when_platforms_share_a_test_name(self):
        samples = [
            SplitSample(11, "Shared", "NuttX", "fault", "kernel", "watchdog"),
            SplitSample(22, "Shared", "Zephyr", "fault", "kernel", "watchdog"),
            SplitSample(33, "Alpha", "NuttX", "fault", "kernel", "watchdog"),
            SplitSample(44, "Beta", "NuttX", "fault", "kernel", "watchdog"),
            SplitSample(55, "Gamma", "NuttX", "fault", "kernel", "watchdog"),
            SplitSample(66, "Delta", "NuttX", "fault", "kernel", "watchdog"),
        ]

        result = build_stratified_split(samples, SplitRatios(0.5, 0.25, 0.25), 631)

        self.assertEqual(
            result,
            build_stratified_split(
                list(reversed(samples)), SplitRatios(0.5, 0.25, 0.25), 631
            ),
        )

    def test_split_is_disjoint_and_covers_each_version_once(self):
        samples = [
            self._sample(1, "Test_1"),
            self._sample(2, "Test_2"),
            self._sample(3, "Test_3", "normal", None, None),
            self._sample(4, "Test_4", "normal", None, None),
            self._sample(5, "Test_5", "normal", None, None),
        ]

        result = build_stratified_split(samples, SplitRatios(0.6, 0.2, 0.2), 20)

        self.assertTrue(set(result["train"]).isdisjoint(result["validation"]))
        self.assertTrue(set(result["train"]).isdisjoint(result["test"]))
        self.assertTrue(set(result["validation"]).isdisjoint(result["test"]))
        self.assertEqual(
            set(result["train"]) | set(result["validation"]) | set(result["test"]),
            {1, 2, 3, 4, 5},
        )

    def test_tiny_groups_match_model_train_count_rules(self):
        samples = [
            self._sample(1, "One", "single", "d", "f"),
            self._sample(2, "TwoA", "pair", "d", "f"),
            self._sample(3, "TwoB", "pair", "d", "f"),
        ]

        result = build_stratified_split(samples, SplitRatios(0.8, 0.1, 0.1), 631)

        self.assertEqual(set(result["train"]), {1, 2, 3})
        self.assertEqual(result["validation"], [])
        self.assertEqual(result["test"], [])

    def test_null_metadata_is_grouped_as_unknown(self):
        samples = [
            self._sample(1, "Test_1", None, None, None),
            self._sample(2, "Test_2", "UNKNOWN", "UNKNOWN", "UNKNOWN"),
            self._sample(3, "Test_3", None, None, None),
        ]

        result = build_stratified_split(samples, SplitRatios(0.34, 0.33, 0.33), 631)

        self.assertEqual(len(result["train"]), 1)
        self.assertEqual(len(result["validation"]), 1)
        self.assertEqual(len(result["test"]), 1)

    def test_invalid_ratios_are_rejected(self):
        cases = (
            SplitRatios(0.8, 0.1, 0.2),
            SplitRatios(-0.1, 0.6, 0.5),
            SplitRatios(math.nan, 0.5, 0.5),
            SplitRatios(math.inf, 0.0, 0.0),
        )

        for ratios in cases:
            with self.subTest(ratios=ratios):
                with self.assertRaisesRegex(ValueError, "Split ratios"):
                    build_stratified_split([], ratios, 631)


if __name__ == "__main__":
    unittest.main()
