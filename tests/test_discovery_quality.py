import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eligibility import check_eligibility
from job_descriptions import clean_text, fetch_job_description_text, lever_description
from pipeline_stats import PipelineStats
from send_email import build_summary_html
from fetch_jobs import fetch_lever_jobs, fetch_greenhouse_jobs
import main


class QualityTests(unittest.TestCase):
    rules = {"eligibility": {"exclude_senior_titles": True,
                             "max_required_experience_years": 2,
                             "requires_sponsorship": True, "graduation_year": 2026}}

    def test_feed_description_is_cleaned_without_network(self):
        job = {"description": "&lt;p&gt;Python &amp; SQL&lt;/p&gt;<script>bad()</script>"}
        with patch("job_descriptions.requests.get") as get:
            self.assertEqual(fetch_job_description_text(job), "Python & SQL")
        get.assert_not_called()

    def test_lever_includes_requirements_and_additional_sections(self):
        data = {"descriptionPlain": "Build apps", "lists": [{"text": "Requirements",
                "content": "<li>Requires 5 years of experience</li>"}], "additional": "Benefits"}
        text = clean_text(lever_description(data))
        self.assertIn("Requires 5 years", text)
        self.assertIn("Benefits", text)

    @patch("job_descriptions.requests.get")
    def test_lever_detail_is_specific_to_company_and_job(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"descriptionPlain": "Full description"}
        job = {"source": "simplify-new-grad-repo", "url": "https://jobs.lever.co/acme/abc"}
        self.assertEqual(fetch_job_description_text(job), "Full description")
        self.assertEqual(get.call_args.args[0], "https://api.lever.co/v0/postings/acme/abc")

    @patch("job_descriptions.requests.get")
    def test_missing_description_falls_back_without_arbitrary_fetch(self, get):
        job = {"title": "Developer", "url": "https://unknown.example/job"}
        self.assertEqual(fetch_job_description_text(job), "Developer")
        self.assertEqual(job["description_quality"], "title_only")
        get.assert_not_called()

    @patch("job_descriptions.requests.get")
    def test_failed_detail_response_retains_title(self, get):
        get.return_value = Mock(status_code=429)
        job = {"title": "Engineer", "url": "https://jobs.lever.co/acme/abc"}
        self.assertEqual(fetch_job_description_text(job), "Engineer")
        self.assertEqual(job["description_quality"], "title_only")

    def test_failed_and_deferred_scores_are_not_logged(self):
        for status in ("failed", "deferred"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                template = Path(directory) / "resume.tex"
                template.write_text("resume")
                config = dict(resume_template_path=str(template), output_dir=directory, job_queue_path=str(Path(directory) / "queue.json"),
                              google_sheet_name="sheet", google_sheet_worksheet="jobs",
                              summary_email_to="to", summary_email_from="from")
                with patch.multiple(main, load_config=Mock(return_value=config),
                                    GeminiBudget=Mock(return_value=Mock(used=0)),
                                    get_logged_urls=Mock(return_value=set()), log_postings=Mock(return_value=0),
                                    fetch_all_jobs=Mock(return_value=[{"title": "Engineer", "company": "A", "url": "one"}]),
                                    fetch_job_description_text=Mock(return_value="Build apps"),
                                    score_and_tailor=Mock(return_value={"status": status}),
                                    send_summary_email=Mock()), patch.object(main, "log_job") as log:
                    main.main()
                log.assert_not_called()
                data = json.loads((Path(directory) / "pipeline-stats.json").read_text())
                self.assertEqual(data["counts"]["scored"], 0)
                self.assertEqual(data["counts"]["scoring_failed" if status == "failed" else "budget_deferred_candidates"], 1)

    @patch("fetch_jobs._get")
    def test_greenhouse_requests_content(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"jobs": [{"id": 1, "content": "Build software"}]}
        self.assertEqual(fetch_greenhouse_jobs("acme", 168)[0]["description"], "Build software")
        self.assertEqual(get.call_args.kwargs["params"], {"content": "true"})

    def test_explicit_mismatches_rejected(self):
        for description, reason in [("Requires 5 years of professional experience", "required_experience"),
                                    ("No visa sponsorship available", "explicit_no_sponsorship"),
                                    ("Must graduate in 2025.", "graduation_year")]:
            with self.subTest(description=description):
                self.assertIn(reason, check_eligibility({}, description, self.rules)["reasons"])
        self.assertFalse(check_eligibility({"title": "Senior Software Engineer"}, "", self.rules)["eligible"])

    def test_ambiguous_and_preferred_requirements_kept(self):
        for text in ["5 years experience preferred", "Requires 5 years experience or a Master's degree",
                     "Must graduate in 2025 or 2026", "Must graduate in 2025-2027",
                     "Must be authorized to work", "Sponsorship may be available", ""]:
            with self.subTest(text=text):
                self.assertTrue(check_eligibility({}, text, self.rules)["eligible"])

    def test_location_exclusions_are_opt_in_and_word_bounded(self):
        rules = {"eligibility": {"excluded_location_terms": ["India"]}}
        self.assertTrue(check_eligibility({"location": "Indiana"}, "", rules)["eligible"])
        self.assertFalse(check_eligibility({"location": "Bangalore, India"}, "", rules)["eligible"])

    def test_empty_summary_still_reports_stats(self):
        stats = PipelineStats()
        stats.counts["scored"] = 0
        self.assertIn("scored: 0", build_summary_html([], stats))

    def test_rejected_candidate_does_not_consume_scoring_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "resume.tex"
            template.write_text("resume")
            config = dict(self.rules, resume_template_path=str(template), output_dir=directory, job_queue_path=str(Path(directory) / "queue.json"),
                          google_sheet_name="sheet", google_sheet_worksheet="jobs",
                          summary_email_to="to", summary_email_from="from", max_jobs_per_run=1,
                          max_jobs_to_score=1, min_match_score_to_tailor=60)
            jobs = [{"title": "Senior Engineer", "company": "A", "url": "one"},
                    {"title": "Junior Engineer", "company": "B", "url": "two"}]
            with patch.multiple(main, load_config=Mock(return_value=config),
                                GeminiBudget=Mock(return_value=Mock(used=1)),
                                get_logged_urls=Mock(return_value=set()), log_postings=Mock(return_value=0),
                                fetch_all_jobs=Mock(return_value=jobs),
                                fetch_job_description_text=Mock(return_value="Build apps"),
                                log_job=Mock(), send_summary_email=Mock()), \
                    patch.object(main, "score_and_tailor", return_value={"match_score": 40}) as score:
                main.main()
            self.assertEqual(score.call_count, 1)
            self.assertEqual(score.call_args.args[0]["url"], "two")
            report = json.loads((Path(directory) / "pipeline-stats.json").read_text())
            self.assertEqual(report["counts"]["eligibility_rejected"], 1)
            self.assertEqual(report["counts"]["scored"], 1)


if __name__ == "__main__":
    unittest.main()
