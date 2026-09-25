import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from job_queue import JobQueue, canonical_url, deduplicate_jobs
import main


def job(url="https://jobs.example/1", source="greenhouse", **extra):
    return dict(company="Acme", title="Software Engineer", location="New York",
                url=url, source=source, **extra)


class QueueTests(unittest.TestCase):
    def test_url_tracking_removed_but_job_id_preserved(self):
        self.assertEqual(canonical_url("https://jobs.example/a/?utm_source=x&id=2#apply"), "https://jobs.example/a?id=2")
        self.assertNotEqual(canonical_url("https://jobs.example/?id=1"), canonical_url("https://jobs.example/?id=2"))
        self.assertEqual(canonical_url("https://linkedin.com/jobs/view/engineer-123?trk=x"), "https://www.linkedin.com/jobs/view/123")

    def test_cross_source_duplicate_keeps_alias_and_richer_description(self):
        merged = deduplicate_jobs([job(), job("https://aggregator.example/2", "adzuna", description="Requirements")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["description"], "Requirements")
        self.assertEqual(merged[0]["alternate_urls"], ["https://aggregator.example/2"])

    def test_same_source_openings_and_different_locations_survive(self):
        first, second = job(), job("https://jobs.example/2")
        third = job("https://other.example/3", "lever")
        third["location"] = "London"
        self.assertEqual(len(deduplicate_jobs([first, second, third])), 3)

    def test_ambiguous_cross_source_matches_are_not_merged(self):
        self.assertEqual(len(deduplicate_jobs([job(), job("https://jobs.example/2"),
                                               job("https://other.example/3", "adzuna")])), 3)
        self.assertEqual(len(deduplicate_jobs([job("https://other.example/3", "adzuna"),
                                               job(), job("https://jobs.example/2")])), 3)

    def test_pending_survives_missing_feed_and_completed_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            queue = JobQueue(path)
            queue.ingest([job(posted_at="2020-01-01")])
            restarted = JobQueue(path)
            restarted.ingest([])
            self.assertEqual(len(restarted.pending()), 1)
            restarted.complete(restarted.pending()[0])
            restarted.ingest([job()])
            self.assertEqual(JobQueue(path).pending(), [])

    def test_alias_logged_in_sheet_completes_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.json")
            queue.ingest([job(), job("https://other.example/2", "adzuna")], ["https://other.example/2?utm_source=x"])
            self.assertEqual(queue.pending(), [])

    def test_repeated_ingest_does_not_grow_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.json")
            for _ in range(5):
                queue.ingest([job(), job("https://other.example/2", "adzuna")])
            self.assertEqual(len(queue.pending()), 1)
            self.assertEqual(queue.pending()[0]["alternate_urls"], ["https://other.example/2"])

    def test_score_cache_invalidates_with_resume_description_or_version(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.json")
            queue.ingest([job()])
            key = queue.cache_key("resume", "description", "v1")
            queue.save_score(job(), key, {"match_score": 40})
            self.assertEqual(JobQueue(queue.path).cached_score(job(), key), {"match_score": 40})
            for values in [("changed", "description", "v1"), ("resume", "changed", "v1"), ("resume", "description", "v2")]:
                self.assertIsNone(queue.cached_score(job(), queue.cache_key(*values)))

    def test_rule_change_reopens_rejected_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.json")
            queue.configure_rules({"max": 2})
            queue.ingest([job()])
            queue.reject(job(), ["experience"])
            queue.configure_rules({"max": 2})
            self.assertEqual(queue.pending(), [])
            queue.configure_rules({"max": 5})
            self.assertEqual(len(queue.pending()), 1)

    def test_corrupt_state_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            path.write_text("not json")
            with self.assertRaises(ValueError):
                JobQueue(path)
            self.assertEqual(path.read_text(), "not json")

    def test_dry_run_makes_no_service_calls_or_queue_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            queue = JobQueue(path)
            queue.ingest([job()])
            before = path.read_bytes()
            template = Path(directory) / "resume.tex"
            template.write_text("Resume")
            config = {"resume_template_path": str(template), "output_dir": directory, "job_queue_path": str(path)}
            forbidden = Mock(side_effect=AssertionError("Dry run called a service"))
            with patch.multiple(main, load_config=Mock(return_value=config), fetch_all_jobs=Mock(return_value=[]),
                                fetch_job_description_text=Mock(return_value="Description"),
                                GeminiBudget=forbidden, get_logged_urls=forbidden, score_and_tailor=forbidden, log_postings=forbidden,
                                log_job=forbidden, send_summary_email=forbidden, compile_resume=forbidden):
                main.main(dry_run=True)
            forbidden.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(json.loads((Path(directory) / "dry-run-jobs.json").read_text())), 1)

    def test_sheet_failure_reuses_score_on_next_run(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "resume.tex"
            template.write_text("Resume")
            config = dict(resume_template_path=str(template), output_dir=directory,
                          job_queue_path=str(Path(directory) / "queue.json"), google_sheet_name="s",
                          google_sheet_worksheet="w", summary_email_to="to", summary_email_from="from")
            score = Mock(return_value={"match_score": 40})
            with patch.multiple(main, load_config=Mock(return_value=config), fetch_all_jobs=Mock(return_value=[job()]),
                                fetch_job_description_text=Mock(return_value="Description"),
                                GeminiBudget=Mock(return_value=Mock(used=0)), get_logged_urls=Mock(return_value=set()), log_postings=Mock(return_value=0),
                                score_and_tailor=score, log_job=Mock(side_effect=[RuntimeError("Sheets down"), None]),
                                send_summary_email=Mock(), create_sheet_fallback_pdf=Mock(return_value=None)):
                main.main()
                main.main()
            self.assertEqual(score.call_count, 1)
            self.assertEqual(JobQueue(config["job_queue_path"]).pending(), [])


if __name__ == "__main__":
    unittest.main()
