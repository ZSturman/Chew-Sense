import tempfile
import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import PipelineConfig
from pipeline.recommendations import generate_collection_plan


class CollectionRecommendationTests(unittest.TestCase):
    def test_high_false_positives_on_walking_request_walking_negatives(self):
        metrics = {
            "field_regression": {
                "per_activity": {
                    "walking": {
                        "n_samples": 100,
                        "n_positive": 0,
                        "n_negative": 100,
                        "false_positive": 42,
                        "false_negative": 0,
                        "true_negative": 58,
                        "true_positive": 0,
                        "false_positive_rate": 0.42,
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan = generate_collection_plan(
                metrics,
                Path(tmp),
                PipelineConfig(),
                model_id="chewnet-test",
                dataset_hash="abc123",
            )

        walking = next(item for item in plan["recommendations"] if item["activity"] == "walking")
        self.assertEqual(walking["priority"], 1)
        self.assertEqual(walking["requested_minutes"], 8)
        self.assertIn("false_positive_rate_high", walking["reason_codes"])

    def test_high_false_negatives_on_soft_food_request_soft_food_positives(self):
        metrics = {
            "field_regression": {
                "per_activity": {
                    "eating_soft": {
                        "n_samples": 100,
                        "n_positive": 100,
                        "n_negative": 0,
                        "false_positive": 0,
                        "false_negative": 39,
                        "true_negative": 0,
                        "true_positive": 61,
                        "false_negative_rate": 0.39,
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            plan = generate_collection_plan(
                metrics,
                Path(tmp),
                PipelineConfig(),
                model_id="chewnet-test",
                dataset_hash="abc123",
            )

        soft_food = next(item for item in plan["recommendations"] if item["activity"] == "eating_soft")
        self.assertEqual(soft_food["priority"], 1)
        self.assertEqual(soft_food["requested_minutes"], 8)
        self.assertIn("false_negative_rate_high", soft_food["reason_codes"])

    def test_low_coverage_preset_produces_coverage_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "Walking-reviewed", "walking", duration_sec=360)

            plan = generate_collection_plan(
                {"field_regression": {"per_activity": {}}},
                root,
                PipelineConfig(),
                model_id="chewnet-test",
                dataset_hash="abc123",
            )

        activities = {item["activity"]: item for item in plan["recommendations"]}
        self.assertIn("eating_hard", activities)
        self.assertIn("coverage_gap", activities["eating_hard"]["reason_codes"])
        self.assertGreaterEqual(activities["eating_hard"]["requested_minutes"], 3)

    def test_empty_metrics_still_produce_safe_fallback_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = generate_collection_plan(
                None,
                Path(tmp),
                PipelineConfig(),
                model_id=None,
                dataset_hash=None,
            )

        self.assertEqual(plan["version"], 1)
        self.assertEqual(plan["source_split"], "none")
        self.assertGreater(len(plan["recommendations"]), 0)
        self.assertLessEqual(len(plan["recommendations"]), 5)

    def _write_session(
        self,
        root: Path,
        name: str,
        activity: str,
        duration_sec: int,
    ) -> None:
        session_dir = root / name
        session_dir.mkdir()
        (session_dir / "_metadata.txt").write_text(f'{{"activity":"{activity}"}}')
        pd.DataFrame({
            "timestamp": [0.0, float(duration_sec)],
            "ax": [0.0, 0.0],
            "ay": [0.0, 0.0],
            "az": [0.0, 0.0],
            "gx": [0.0, 0.0],
            "gy": [0.0, 0.0],
            "gz": [0.0, 0.0],
            "label": [False, False],
        }).to_csv(session_dir / f"{name}.csv", index=False)


if __name__ == "__main__":
    unittest.main()
