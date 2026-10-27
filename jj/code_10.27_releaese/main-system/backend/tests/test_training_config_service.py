import copy
import unittest
from pathlib import Path

import yaml

from app.services.training_config_service import (
    TrainingConfigError,
    build_training_config,
    normalized_config_snapshot,
)


PATHS = {
    "model_name_or_path": "/models/Qwen3.5-9B",
    "train_file": "/training/task/data/train.jsonl",
    "validation_file": "/training/task/data/validation.jsonl",
    "output_dir": "/training/task/output",
}


class TrainingConfigServiceTests(unittest.TestCase):
    def test_quick_and_formal_presets_match_every_source_key_and_value(self):
        preset_files = {
            ("cpt", "quick"): "cpt_qlora_quick.yaml",
            ("cpt", "formal"): "cpt_qlora.yaml",
            ("sft", "quick"): "sft_open_qlora_quick.yaml",
            ("sft", "formal"): "sft_open_qlora.yaml",
        }
        config_root = Path(__file__).resolve().parents[4] / "model_train" / "configs"
        for (task_type, preset), filename in preset_files.items():
            with self.subTest(task_type=task_type, preset=preset):
                source = yaml.safe_load((config_root / filename).read_text(encoding="utf-8"))
                config = build_training_config(task_type, preset, {}, PATHS)
                expected_training = copy.deepcopy(source["training"])
                expected_training["output_dir"] = PATHS["output_dir"]
                self.assertEqual(config["qlora"], source["qlora"])
                self.assertEqual(config["training"], expected_training)
                self.assertEqual(config["model"]["model_name_or_path"], PATHS["model_name_or_path"])
                self.assertEqual(config["data"]["train_file"], PATHS["train_file"])
                self.assertEqual(config["data"]["validation_file"], PATHS["validation_file"])
                self.assertEqual(config["training"]["output_dir"], PATHS["output_dir"])

    def test_sft_adapter_is_optional_and_only_emitted_when_supplied(self):
        base_config = build_training_config("sft", "quick", {}, PATHS)
        cpt_config = build_training_config(
            "sft", "quick", {}, PATHS, cpt_adapter_path="/trusted/cpt/final_adapter"
        )

        self.assertNotIn("adapter", base_config)
        self.assertEqual(cpt_config["adapter"], {"cpt_adapter_path": "/trusted/cpt/final_adapter"})

    def test_allowed_overrides_replace_only_their_matching_values(self):
        overrides = {
            "learning_rate": 0.001,
            "num_train_epochs": 2.5,
            "max_seq_length": 8192,
            "lora_r": 128,
            "lora_alpha": 192,
            "lora_dropout": 0.2,
            "per_device_train_batch_size": 8,
            "gradient_accumulation_steps": 3,
            "logging_steps": 7,
            "eval_steps": 11,
            "save_steps": 13,
        }

        config = build_training_config("cpt", "quick", overrides, PATHS)

        self.assertEqual(config["training"]["learning_rate"], 0.001)
        self.assertEqual(config["training"]["num_train_epochs"], 2.5)
        self.assertEqual(config["training"]["max_seq_length"], 8192)
        self.assertEqual(config["qlora"]["lora_r"], 128)
        self.assertEqual(config["qlora"]["lora_alpha"], 192)
        self.assertEqual(config["qlora"]["lora_dropout"], 0.2)
        self.assertEqual(config["training"]["per_device_train_batch_size"], 8)
        self.assertEqual(config["training"]["gradient_accumulation_steps"], 3)
        self.assertEqual(config["training"]["logging_steps"], 7)
        self.assertEqual(config["training"]["eval_steps"], 11)
        self.assertEqual(config["training"]["save_steps"], 13)

    def test_unknown_or_invalid_task_configuration_is_rejected(self):
        cases = (
            ("cpt", "quick", {"shell_command": "nvidia-smi"}, "unsupported parameter"),
            ("other", "quick", {}, "unsupported task type"),
            ("cpt", "slow", {}, "unsupported preset"),
            ("cpt", "quick", {"learning_rate": True}, "learning_rate"),
            ("cpt", "quick", {"lora_r": 4.5}, "lora_r"),
            ("cpt", "quick", {"max_seq_length": "2048"}, "max_seq_length"),
        )
        for task_type, preset, overrides, message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(TrainingConfigError, message):
                    build_training_config(task_type, preset, overrides, PATHS)

    def test_every_numeric_boundary_is_enforced(self):
        cases = (
            ("max_seq_length", 511, "max_seq_length"),
            ("max_seq_length", 512, None),
            ("max_seq_length", 8192, None),
            ("max_seq_length", 8193, "max_seq_length"),
            ("lora_r", 3, "lora_r"),
            ("lora_r", 4, None),
            ("lora_r", 256, None),
            ("lora_r", 257, "lora_r"),
            ("lora_dropout", -0.01, "lora_dropout"),
            ("lora_dropout", 0, None),
            ("lora_dropout", 0.5, None),
            ("lora_dropout", 0.51, "lora_dropout"),
            ("per_device_train_batch_size", 0, "per_device_train_batch_size"),
            ("per_device_train_batch_size", 1, None),
            ("per_device_train_batch_size", 8, None),
            ("per_device_train_batch_size", 9, "per_device_train_batch_size"),
            ("gradient_accumulation_steps", 0, "gradient_accumulation_steps"),
            ("gradient_accumulation_steps", 1, None),
            ("logging_steps", 0, "logging_steps"),
            ("logging_steps", 1, None),
            ("eval_steps", 0, "eval_steps"),
            ("eval_steps", 1, None),
            ("save_steps", 0, "save_steps"),
            ("save_steps", 1, None),
            ("learning_rate", 0, "learning_rate"),
            ("learning_rate", 0.00001, None),
            ("learning_rate", 0.01, None),
            ("learning_rate", 0.01001, "learning_rate"),
            ("num_train_epochs", 0, "num_train_epochs"),
            ("num_train_epochs", 0.1, None),
            ("num_train_epochs", 100, None),
            ("num_train_epochs", 100.1, "num_train_epochs"),
        )
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                if message is None:
                    config = build_training_config("cpt", "quick", {key: value}, PATHS)
                    self.assertIsNotNone(config)
                else:
                    with self.assertRaisesRegex(TrainingConfigError, message):
                        build_training_config("cpt", "quick", {key: value}, PATHS)

    def test_lora_alpha_requires_an_integer_but_has_no_unstated_range(self):
        config = build_training_config("cpt", "quick", {"lora_alpha": 1024}, PATHS)

        self.assertEqual(config["qlora"]["lora_alpha"], 1024)
        with self.assertRaisesRegex(TrainingConfigError, "lora_alpha"):
            build_training_config("cpt", "quick", {"lora_alpha": 4.5}, PATHS)

    def test_normalized_snapshot_is_path_free_and_independent(self):
        snapshot = normalized_config_snapshot(
            "sft", "formal", {"learning_rate": 0.002, "lora_r": 64}
        )
        original = copy.deepcopy(snapshot)

        rendered = repr(snapshot)
        self.assertNotIn("/", rendered)
        self.assertNotIn("model_name_or_path", snapshot)
        self.assertNotIn("data", snapshot)
        self.assertNotIn("output_dir", rendered)
        self.assertEqual(snapshot["preset"], "formal")
        self.assertEqual(snapshot["training"]["learning_rate"], 0.002)
        self.assertEqual(snapshot["qlora"]["lora_r"], 64)
        snapshot["training"]["learning_rate"] = 0.5
        self.assertEqual(original["training"]["learning_rate"], 0.002)


if __name__ == "__main__":
    unittest.main()
