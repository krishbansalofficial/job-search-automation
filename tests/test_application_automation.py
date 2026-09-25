import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from application_pack import build_application_pack
from apply_assist import application_url, detect_ats, match_answer, resolve_apply_url
from job_queue import JobQueue
from review_jobs import review, resume_for
from run_summary import summarize
from log_to_sheet import log_job, log_postings


class AutomationTests(unittest.TestCase):
    def test_ats_detection_does_not_trust_url_substrings(self):
        self.assertIsNone(detect_ats("https://evil.example/jobs.lever.co"))
        self.assertIsNone(detect_ats("https://jobs.lever.co.evil.example/job"))
        self.assertIsNone(detect_ats("http://jobs.lever.co/acme/1"))
        self.assertEqual(detect_ats("https://jobs.lever.co/acme/1"), "lever")

    def test_apply_path_preserves_query(self):
        self.assertEqual(resolve_apply_url("lever", "https://jobs.lever.co/acme/1?x=2"),
                         "https://jobs.lever.co/acme/1/apply?x=2")

    def make_pack(self, directory):
        pdf = Path(directory) / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfixture")
        result = {"job": {"company": "<script>alert(1)</script>", "title": "Engineer",
                           "url": "https://jobs.lever.co/acme/1", "eligibility_review": ["Check sponsorship"]},
                  "score_result": {"match_score": 80}, "resume_path": str(pdf)}
        return build_application_pack([result], directory)

    def test_manifest_is_portable_and_html_escapes_job_content(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_pack(directory)
            self.assertEqual(json.loads(manifest.read_text())["jobs"][0]["resume"], "resume.pdf")
            html = (Path(directory) / "review.html").read_text()
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)

    def test_invalid_resume_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resume_for({"resume": "../resume.pdf"}, Path(directory) / "manifest.json")

    def test_list_does_not_launch_or_write_status(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_pack(directory)
            state = Path(directory) / "status.json"
            runner = Mock()
            review(manifest, state, list_only=True, runner=runner)
            runner.assert_not_called()
            self.assertFalse(state.exists())

    def test_application_requires_user_confirmation_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_pack(directory)
            state = Path(directory) / "status.json"
            runner = Mock(return_value=Mock(returncode=0))
            review(manifest, state, input_fn=lambda _: "a", runner=runner)
            review(manifest, state, input_fn=lambda _: "a", runner=runner)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(next(iter(json.loads(state.read_text()).values()))["status"], "applied")

    def test_failed_preparation_does_not_mark_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "status.json"
            review(self.make_pack(directory), state, runner=Mock(return_value=Mock(returncode=1)))
            self.assertFalse(state.exists())

    def test_resume_failure_remains_pending_even_if_sheet_has_job(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.json")
            job = {"url": "https://jobs.example/1"}
            queue.ingest([job])
            queue.set_resume_pending(job, True)
            queue.ingest([], [job["url"]])
            self.assertEqual(len(queue.pending()), 1)
            queue.set_resume_pending(job, False)
            queue.complete(job)
            self.assertEqual(queue.pending(), [])

    @patch("log_to_sheet._get_client")
    @patch("log_to_sheet._get_worksheet")
    def test_sheet_retry_updates_resume_without_duplicate_or_status_overwrite(self, get_sheet, client):
        sheet = Mock()
        sheet.col_values.return_value = ["Job URL", "https://jobs.example/1?utm_source=x"]
        get_sheet.return_value = sheet
        log_job("sheet", "jobs", {"url": "https://jobs.example/1"}, {}, "resume.pdf")
        sheet.append_row.assert_not_called()
        sheet.update_cell.assert_called_once_with(2, 9, "resume.pdf")

    @patch("log_to_sheet._get_client")
    @patch("log_to_sheet._get_worksheet")
    def test_postings_are_batched_and_deduplicated(self, get_sheet, client):
        sheet = Mock()
        sheet.col_values.return_value = ["Job URL", "https://jobs.example/1?utm_source=x"]
        get_sheet.return_value = sheet
        jobs = [{"url": "https://jobs.example/1", "company": "A"},
                {"url": "https://jobs.example/2", "company": "B", "eligibility_review": ["check visa"]},
                {"url": "https://jobs.example/2/", "company": "B"}]
        self.assertEqual(log_postings("sheet", "Postings", jobs), 1)
        rows = sheet.append_rows.call_args.args[0]
        self.assertEqual([(row[1], row[6], row[7]) for row in rows], [("B", "https://jobs.example/2", "check visa")])

    def test_screening_answers_pick_matching_option_or_leave_blank(self):
        rules = [{"question": "sponsor", "answer": "^yes"},
                 {"question": "gender|veteran", "answer": "decline|don.t wish"}]
        self.assertEqual(match_answer("Will you require visa sponsorship?", ["Select...", "Yes", "No"], rules), "Yes")
        self.assertEqual(match_answer("Veteran status", ["I am a veteran", "I don't wish to answer"], rules),
                         "I don't wish to answer")
        self.assertIsNone(match_answer("Gender", ["Male", "Female"], rules))
        self.assertIsNone(match_answer("Are you willing to relocate?", ["Yes", "No"], rules))
        self.assertIsNone(match_answer("Sponsorship?", ["Yes"], None))

    def test_greenhouse_company_site_postings_use_hosted_form(self):
        job = {"url": "https://stripe.com/jobs/search?gh_jid=123",
               "description_url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/123"}
        self.assertEqual(application_url(job), "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=123")
        self.assertEqual(detect_ats(application_url(job)), "greenhouse")
        board = dict(job, url="https://job-boards.greenhouse.io/stripe/jobs/123")
        self.assertEqual(application_url(board), board["url"])

    def test_postings_join_pack_with_base_resume_and_can_be_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base_resume.pdf"
            base.write_bytes(b"%PDF-1.4\nbase")
            postings = [{"company": "Other", "title": "Dev", "url": "https://example.com/careers/9"},
                        {"company": "Acme", "title": "SWE", "url": "https://jobs.lever.co/acme/2"},
                        {"company": "Dup", "title": "Engineer", "url": "https://jobs.lever.co/acme/1"}]
            result = {"job": {"company": "Dup", "title": "Engineer", "url": "https://jobs.lever.co/acme/1"},
                      "score_result": {"match_score": 80}, "resume_path": None}
            manifest = build_application_pack([result], directory, postings=postings, base_resume=str(base))
            jobs = json.loads(manifest.read_text())["jobs"]
            self.assertEqual([(j["company"], j["kind"]) for j in jobs],
                             [("Dup", "match"), ("Acme", "posting"), ("Other", "posting")])
            self.assertEqual(jobs[1]["resume"], "base_resume.pdf")
            runner = Mock(return_value=Mock(returncode=0))
            review(manifest, Path(directory) / "status.json", input_fn=lambda _: "", runner=runner, only="posting")
            self.assertEqual(runner.call_count, 1)
            self.assertIn("https://jobs.lever.co/acme/2", runner.call_args.args[0])

    def test_missing_report_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIn("did not produce a report", summarize(Path(directory) / "missing.json"))


if __name__ == "__main__":
    unittest.main()
