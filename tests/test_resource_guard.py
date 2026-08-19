import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "genso_resource_guard", ROOT / "genso" / "resource_guard.py"
)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


class ResourceGuardTests(unittest.TestCase):
    def test_clean_idle_fluctuation_is_allowed(self):
        self.assertIsNone(guard.generation_vram_error(6.89))

    def test_exact_threshold_is_allowed(self):
        self.assertIsNone(guard.generation_vram_error(6.5))

    def test_loaded_generation_model_is_rejected_with_measurement(self):
        message = guard.generation_vram_error(1.42)
        self.assertIsNotNone(message)
        self.assertIn("1.42 GiB", message)
        self.assertIn("6.5 GiB以上", message)


if __name__ == "__main__":
    unittest.main()
