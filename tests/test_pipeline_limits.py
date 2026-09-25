import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_jobs import fetch_all_jobs
from gemini_budget import GeminiBudget
from score_and_tailor import DEFAULT_MODELS, score_and_tailor


class PipelineTests(unittest.TestCase):
    def budget(self, directory, limit=2):
        budget = GeminiBudget({"gemini_usage_path": str(Path(directory) / "usage.json"),
                               "gemini_max_requests_per_day": limit,
                               "gemini_max_requests_per_run": limit,
                               "gemini_request_delay_seconds": 0})
        budget._today = lambda: "2026-09-22"
        return budget

    def test_seen_jobs_do_not_consume_selection_slots(self):
        jobs = [{"title": "Software Engineer", "url": str(i)} for i in range(30)]
        with patch("fetch_jobs.fetch_workingnomads_jobs", return_value=jobs):
            selected = fetch_all_jobs({"use_working_nomads": True, "max_jobs_per_run": 5},
                                      excluded_urls={str(i) for i in range(25)})
        self.assertEqual([j["url"] for j in selected], [str(i) for i in range(25, 30)])

    def test_early_career_before_senior(self):
        jobs = [{"title": title, "url": str(i)} for i, title in enumerate(
            ["Senior Software Engineer", "Software Engineer", "New Grad Software Engineer"])]
        with patch("fetch_jobs.fetch_workingnomads_jobs", return_value=jobs):
            selected = fetch_all_jobs({"use_working_nomads": True, "max_jobs_per_run": 2})
        self.assertEqual([j["url"] for j in selected], ["2", "1"])

    def test_daily_budget_survives_new_process_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.budget(directory)
            self.assertTrue(first.reserve())
            second = self.budget(directory)
            self.assertTrue(second.reserve())
            self.assertFalse(second.reserve())
            third = self.budget(directory)
            third._today = lambda: "2026-09-23"
            self.assertTrue(third.reserve())

    def test_sdk_retries_disabled(self):
        from score_and_tailor import _configure
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch("google.genai.Client") as client:
            _configure()
        self.assertEqual(client.call_args.kwargs["http_options"]["retry_options"]["attempts"], 1)

    def test_run_budget_can_be_lower_than_daily_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = self.budget(directory, limit=5)
            budget.run_limit = 1
            self.assertTrue(budget.reserve())
            self.assertFalse(budget.reserve())
            self.assertTrue(self.budget(directory, limit=5).reserve())

    def test_failed_attempts_cannot_exceed_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = self.budget(directory)
            generate = Mock(side_effect=RuntimeError("503 unavailable"))
            client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
            with patch("score_and_tailor._configure", return_value=client), patch("score_and_tailor.time.sleep"), \
                    patch("score_and_tailor._overloaded_models", set()):
                result = score_and_tailor({}, "resume", "description", budget)
            self.assertEqual(generate.call_count, 2)
            self.assertEqual(result["status"], "deferred")

    def test_overloaded_models_are_skipped_without_blocking_daily_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = self.budget(directory, limit=20)
            generate = Mock(side_effect=RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand."))
            client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
            with patch("score_and_tailor._configure", return_value=client), patch("score_and_tailor.time.sleep"), \
                    patch("score_and_tailor._overloaded_models", set()), patch("score_and_tailor._quota_exhausted_models", set()):
                first = score_and_tailor({}, "resume", "description", budget)
                second = score_and_tailor({}, "resume", "description", budget)
            # One call per model, then later jobs defer without spending requests.
            self.assertEqual(generate.call_count, len(DEFAULT_MODELS))
            self.assertEqual((first["status"], second["status"]), ("deferred", "deferred"))
            self.assertEqual((first["reason"], second["reason"]), ("overloaded", "overloaded"))
            self.assertTrue(self.budget(directory, limit=20).reserve())

    def run_models(self, budget, side_effect, models=("model-a", "model-b")):
        generate = Mock(side_effect=side_effect)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        with patch("score_and_tailor._configure", return_value=client), patch("score_and_tailor._quota_exhausted_models", set()), \
                patch("score_and_tailor._overloaded_models", set()):
            return score_and_tailor({}, "resume", "description", budget, models=models), generate

    def test_quota_error_blocks_only_that_model_and_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            reply = SimpleNamespace(text='{"match_score": 72}')
            result, generate = self.run_models(self.budget(directory, limit=5),
                                               [RuntimeError("429 RESOURCE_EXHAUSTED"), reply])
            self.assertEqual(result["match_score"], 72)
            self.assertEqual([c.kwargs["model"] for c in generate.call_args_list], ["model-a", "model-b"])
            later = self.budget(directory, limit=5)
            self.assertTrue(later.model_blocked("model-a"))
            self.assertFalse(later.model_blocked("model-b"))
            self.assertTrue(later.reserve())

    def test_retired_model_is_skipped_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            retired = RuntimeError("404 NOT_FOUND. models/gemini-2.5-flash is no longer available to new users.")
            result, generate = self.run_models(self.budget(directory, limit=5),
                                               [retired, SimpleNamespace(text='{"match_score": 64}')])
            self.assertEqual(result["match_score"], 64)
            self.assertEqual([c.kwargs["model"] for c in generate.call_args_list], ["model-a", "model-b"])
            self.assertTrue(self.budget(directory, limit=5).model_blocked("model-a"))

    def test_quota_on_every_model_stops_for_the_day(self):
        with tempfile.TemporaryDirectory() as directory:
            result, generate = self.run_models(self.budget(directory, limit=5), RuntimeError("429 RESOURCE_EXHAUSTED"))
            self.assertEqual(generate.call_count, 2)
            self.assertEqual((result["status"], result["reason"]), ("deferred", "quota"))
            self.assertFalse(self.budget(directory, limit=5).reserve())


if __name__ == "__main__":
    unittest.main()
