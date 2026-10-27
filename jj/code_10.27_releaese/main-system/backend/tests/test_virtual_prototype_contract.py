import unittest

from pydantic import ValidationError

from app.schemas.virtual_prototype import (
    FAULT_TYPES, VirtualPrototypeJobCreate, VirtualPrototypeReplayRequest,
)


class VirtualPrototypeContractTests(unittest.TestCase):
    def test_catalog_is_exactly_ten_new_codes(self):
        self.assertEqual(FAULT_TYPES, (
            "F1101", "F1206", "F2301", "F3409", "F4512",
            "F4620", "F5106", "F5203", "F5313", "F5402",
        ))
        for code in FAULT_TYPES:
            self.assertEqual(VirtualPrototypeJobCreate(fault_type=code).fault_type, code)

    def test_augmentation_is_exactly_fifteen(self):
        value = VirtualPrototypeJobCreate(fault_type="F4512", task_kind="augmentation", sample_count=15)
        self.assertEqual(value.sample_count, 15)
        for count in (1, 14, 16):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                VirtualPrototypeJobCreate(fault_type="F4512", task_kind="augmentation", sample_count=count)

    def test_reproduction_and_replay_are_exactly_one(self):
        self.assertEqual(VirtualPrototypeReplayRequest().sample_count, 1)
        with self.assertRaises(ValidationError):
            VirtualPrototypeReplayRequest(sample_count=15)
        with self.assertRaises(ValidationError):
            VirtualPrototypeJobCreate(fault_type="F4512", task_kind="reproduction", sample_count=2)

    def test_old_codes_and_fallback_fields_are_rejected(self):
        for code in ("F2305", "F5201", "F5403"):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                VirtualPrototypeJobCreate(fault_type=code)
        for fields in (
            {"options": {"fallback_fault_type": "F5203"}},
            {"fallback_fault_type": "F5203"},
            {"auto_select_execution": True},
            {"expected_executed_fault_type": "F5203"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                VirtualPrototypeJobCreate(fault_type="F5203", **fields)

    def test_three_replacement_codes_require_real_execution(self):
        for code in ("F2301", "F5203", "F5402"):
            for options in ({"evidence_mode": "simulated"}, {"capability_policy": "simulate_if_unavailable"}):
                with self.subTest(code=code, options=options), self.assertRaises(ValidationError):
                    VirtualPrototypeJobCreate(fault_type=code, options=options)
            value = VirtualPrototypeJobCreate(fault_type=code, options={"evidence_mode": "observed", "capability_policy": "native_only"})
            self.assertEqual(value.options.evidence_mode, "observed")

    def test_unsafe_and_automatic_simulation_options_are_rejected(self):
        for code, options in (
            ("F4512", {"capability_policy": "simulate_if_unavailable"}),
            ("F2301", {"prepare_timeout_seconds": 0}),
            ("F2301", {"shell": "echo forbidden"}),
            ("F1206", {"workers": 2}), ("F4512", {"workers": 9}),
        ):
            with self.subTest(code=code, options=options), self.assertRaises(ValidationError):
                VirtualPrototypeJobCreate(fault_type=code, options=options)

    def test_default_duration_is_omitted_and_explicit_duration_preserved(self):
        value = VirtualPrototypeJobCreate(fault_type="F4512", task_kind="augmentation", sample_count=15)
        self.assertNotIn("duration_seconds", value.model_dump(exclude_none=True))
        explicit = VirtualPrototypeJobCreate(fault_type="F4512", duration_seconds=10)
        self.assertEqual(explicit.model_dump(exclude_none=True)["duration_seconds"], 10)


if __name__ == "__main__":
    unittest.main()
