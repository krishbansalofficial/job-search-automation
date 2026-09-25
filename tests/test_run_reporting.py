import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import main
from pipeline_stats import PipelineStats
from send_email import build_summary_html, empty_run_message


class EmptyRunMessageTests(unittest.TestCase):
    def stats(self, **counts):
        stats = PipelineStats()
        stats.counts.update(counts)
        return stats

    def test_overload_is_not_reported_as_no_postings(self):
        html = build_summary_html([], self.stats(deferred_gemini_overloaded=1, queue_remaining=720))
        self.assertIn("Gemini was overloaded", html)
        self.assertIn("720 jobs are queued", html)
        self.assertNotIn("No new matching postings", html)

    def test_other_empty_outcomes(self):
        self.assertIn("quota limit", empty_run_message(self.stats(deferred_gemini_quota=1)))
        self.assertIn("budget is used up", empty_run_message(self.stats(deferred_gemini_budget=1)))
        self.assertIn("none reached", empty_run_message(self.stats(scored=3)))
        self.assertEqual(empty_run_message(self.stats()), "No new matching postings today.")


class QuietRetryRunTests(unittest.TestCase):
    def run_pipeline(self, quiet, score):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "resume.tex"
            template.write_text("resume")
            config = dict(resume_template_path=str(template), output_dir=directory,
                          job_queue_path=str(Path(directory) / "queue.json"),
                          google_sheet_name="sheet", google_sheet_worksheet="jobs",
                          summary_email_to="to", summary_email_from="from")
            email = Mock()
            self.postings = Mock(return_value=1)
            with patch.dict("os.environ", {"QUIET_WHEN_EMPTY": "true" if quiet else "false"}), \
                    patch.multiple(main, load_config=Mock(return_value=config),
                                   GeminiBudget=Mock(return_value=Mock(used=0)),
                                   get_logged_urls=Mock(return_value=set()),
                                   fetch_all_jobs=Mock(return_value=[{"title": "Engineer", "company": "A", "url": "one"}]),
                                   fetch_job_description_text=Mock(return_value="Build apps"),
                                   score_and_tailor=Mock(return_value=score),
                                   compile_resume=Mock(return_value=None), log_job=Mock(),
                                   log_postings=self.postings, send_summary_email=email):
                main.main()
            return email

    def test_retry_run_without_matches_sends_no_email(self):
        self.run_pipeline(True, {"status": "deferred", "reason": "overloaded"}).assert_not_called()

    def test_retry_run_with_match_still_emails(self):
        self.run_pipeline(True, {"match_score": 90}).assert_called_once()

    def test_daily_run_always_emails(self):
        self.run_pipeline(False, {"status": "deferred", "reason": "overloaded"}).assert_called_once()

    def test_postings_logged_even_when_scoring_is_deferred(self):
        self.run_pipeline(False, {"status": "deferred", "reason": "overloaded"})
        self.assertEqual([job["company"] for job in self.postings.call_args.args[2]], ["A"])

    def test_gemini_outage_still_emails_eligible_companies(self):
        email = self.run_pipeline(False, {"status": "deferred", "reason": "overloaded"})
        unscored = email.call_args.kwargs["unscored"]
        self.assertEqual([job["company"] for job in unscored], ["A"])
        html = build_summary_html([], None, unscored)
        self.assertIn("Eligible jobs waiting for scoring (1)", html)
        self.assertIn(">A<", html)


if __name__ == "__main__":
    unittest.main()
