import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_jobs import fetch_all_jobs, fetch_smartrecruiters_jobs


class USBoardTests(unittest.TestCase):
    def record(self, job_id="1", country="us", **updates):
        return dict(dict(id=job_id, name="Software Engineer",
                         releasedDate=datetime.now(timezone.utc).isoformat(),
                         company={"name": "Western Digital"},
                         location={"country": country, "city": "San Jose"}), **updates)

    def response(self, records):
        return Mock(status_code=200, json=Mock(return_value={"content": records}))

    @patch("fetch_jobs._get")
    def test_us_filter_age_and_normalization(self, get):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        get.return_value = self.response([self.record(), self.record("2", "ca"),
            self.record("3", ""), self.record("4", releasedDate=old),
            self.record("5", name=None), None])
        jobs = fetch_smartrecruiters_jobs("WesternDigital", 168, country="us")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Western Digital")
        self.assertIn("/WesternDigital/1", jobs[0]["url"])
        self.assertIn("/postings/1", jobs[0]["description_url"])
        self.assertEqual(get.call_args.kwargs["params"]["country"], "us")

    @patch("fetch_jobs._get")
    def test_pagination_preserves_results_when_later_page_fails(self, get):
        get.side_effect = [self.response([self.record(str(i)) for i in range(100)]),
                           Mock(status_code=429)]
        self.assertEqual(len(fetch_smartrecruiters_jobs("Acme", 168, "us")), 100)
        self.assertEqual(get.call_args.kwargs["params"]["offset"], 100)

    @patch("fetch_jobs._get")
    def test_repeated_pages_stop_and_global_mode_keeps_other_countries(self, get):
        get.return_value = self.response([self.record(str(i), "ca") for i in range(100)])
        self.assertEqual(len(fetch_smartrecruiters_jobs("Acme", 168)), 100)
        self.assertEqual(get.call_count, 2)
        self.assertNotIn("country", get.call_args.kwargs["params"])

    @patch("fetch_jobs._get")
    def test_malformed_responses_stop(self, get):
        for response in [None, Mock(status_code=200, json=Mock(side_effect=ValueError)),
                         Mock(status_code=200, json=Mock(return_value=[]))]:
            get.return_value = response
            self.assertEqual(fetch_smartrecruiters_jobs("Acme", 168, "us"), [])

    @patch("fetch_jobs.time.sleep")
    @patch("fetch_jobs.fetch_smartrecruiters_jobs", return_value=[])
    def test_us_config_routes_to_country_filtered_fetch(self, fetch, sleep):
        fetch_all_jobs({"smartrecruiters_us_companies": ["ServiceNow", "Solidigm"]})
        self.assertEqual(fetch.call_count, 2)
        fetch.assert_any_call("ServiceNow", 48, country="us")
        fetch.assert_any_call("Solidigm", 48, country="us")


if __name__ == "__main__":
    unittest.main()
