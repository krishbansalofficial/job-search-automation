import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from social_jobs import fetch_linkedin_jobs, fetch_x_jobs, parse_linkedin_jobs
from fetch_jobs import fetch_all_jobs
from score_and_tailor import fetch_job_description_text

CARD = '''<div class="base-search-card">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/software-engineer-123456/?trackingId=abc"></a>
<h3 class="base-search-card__title">Junior Software Engineer</h3>
<h4 class="base-search-card__subtitle">Example Co</h4>
<span class="job-search-card__location">New York</span>
<time datetime="2026-09-22"></time></div>'''


class SocialTests(unittest.TestCase):
    def test_linkedin_normalizes_job_url(self):
        job = parse_linkedin_jobs(CARD)[0]
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/123456/")
        self.assertEqual(job["company"], "Example Co")
        self.assertEqual(parse_linkedin_jobs('<html>Sign in</html>'), [])

    @patch("social_jobs.requests.get")
    def test_linkedin_stops_on_block(self, get):
        get.return_value = Mock(status_code=429)
        self.assertEqual(fetch_linkedin_jobs({"linkedin_search_queries": ["a", "b"]}, 168), [])
        self.assertEqual(get.call_count, 1)

    @patch("social_jobs.requests.get")
    def test_linkedin_has_two_request_ceiling(self, get):
        get.return_value = Mock(status_code=200, text=CARD)
        fetch_linkedin_jobs({"linkedin_search_queries": ["a", "b", "c"]}, 168)
        self.assertEqual(get.call_count, 2)

    @patch.dict("os.environ", {"X_BEARER_TOKEN": "test"})
    @patch("social_jobs.requests.get")
    def test_x_one_page_and_description(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"data": [{"id": "123", "text": "Hiring software engineer"}],
                                            "meta": {"next_token": "do-not-follow"}}
        jobs = fetch_x_jobs({"x_search_query": "software hiring"}, 168)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(jobs[0]["url"], "https://x.com/i/web/status/123")
        self.assertEqual(fetch_job_description_text(jobs[0]), "Hiring software engineer")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    @patch.dict("os.environ", {}, clear=True)
    @patch("social_jobs.requests.get")
    def test_x_missing_token_makes_no_request(self, get):
        self.assertEqual(fetch_x_jobs({}, 168), [])
        get.assert_not_called()

    @patch("social_jobs.fetch_x_jobs")
    @patch("social_jobs.fetch_linkedin_jobs")
    def test_social_jobs_share_existing_filter_and_dedup(self, linkedin, x):
        linkedin.return_value = [{"title": "Software Engineer", "url": "seen"}]
        x.return_value = [{"title": "Hiring software engineer", "url": "new"},
                          {"title": "Hiring accountant", "url": "unrelated"}]
        jobs = fetch_all_jobs({"use_x": True, "use_linkedin_public": True,
                               "target_titles": ["Software Engineer"], "max_jobs_per_run": 1}, {"seen"})
        self.assertEqual([job["url"] for job in jobs], ["new"])


if __name__ == "__main__":
    unittest.main()
