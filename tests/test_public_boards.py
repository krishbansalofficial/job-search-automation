import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_boards import _date, fetch_json_board, fetch_weworkremotely_jobs
from fetch_jobs import fetch_all_jobs


class PublicBoardTests(unittest.TestCase):
    def response(self, data):
        return Mock(status_code=200, json=Mock(return_value=data))

    def record(self, **updates):
        record = dict(title="Software Engineer", companyName="Acme",
                      applicationLink="https://himalayas.app/jobs/one",
                      pubDate=datetime.now(timezone.utc).timestamp(),
                      description="<p>Build software</p>",
                      locationRestrictions=["United States", {"name": "Canada"}])
        return dict(record, **updates)

    def test_dates_support_seconds_milliseconds_iso_and_rss(self):
        for value in [1700000000, 1700000000000, "2023-11-14T22:13:20Z",
                      "Tue, 14 Nov 2023 22:13:20 GMT"]:
            self.assertEqual(_date(value), "2023-11-14T22:13:20+00:00")
        self.assertIsNone(_date("bad date"))

    @patch("fetch_jobs._get")
    def test_himalayas_pagination_filters_and_description(self, get):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        get.side_effect = [self.response(dict(jobs=[self.record(),
            self.record(pubDate=old), self.record(expiryDate=old),
            self.record(applicationLink="javascript:bad"), None], nextCursor="opaque")),
            self.response(dict(jobs=[]))]
        jobs = fetch_json_board("himalayas", 168)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["location"], "United States, Canada")
        self.assertEqual(jobs[0]["description"], "<p>Build software</p>")
        self.assertEqual(get.call_args.kwargs["params"]["cursor"], "opaque")

    @patch("fetch_jobs._get")
    def test_arbeitnow_bounded_pagination(self, get):
        get.return_value = self.response(dict(data=[dict(title="Developer",
            company_name="Acme", url="https://www.arbeitnow.com/job/1",
            created_at= datetime.now(timezone.utc).timestamp(), location="Berlin")],
            links={"next": "https://www.arbeitnow.com/api/job-board-api?page=2"}))
        self.assertEqual(len(fetch_json_board("arbeitnow", 168)), 5)
        self.assertEqual(get.call_count, 5)
        self.assertEqual(get.call_args.kwargs["params"], {"page": 5})

    @patch("fetch_jobs._get")
    def test_weworkremotely_rss_preserves_attribution(self, get):
        get.return_value = Mock(status_code=200, content=b'''<rss><channel>
          <item><title>Acme: Software Engineer</title>
          <link>https://weworkremotely.com/remote-jobs/acme</link>
          <description><![CDATA[<p>Requirements</p>]]></description></item>
          <item><title>Missing link</title></item>
        </channel></rss>''')
        jobs = fetch_weworkremotely_jobs(168)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Acme")
        self.assertEqual(jobs[0]["title"], "Software Engineer")
        self.assertIn("weworkremotely.com", jobs[0]["url"])

    @patch("fetch_jobs._get")
    def test_failures_are_isolated(self, get):
        for response in [None, Mock(status_code=429),
                         Mock(status_code=200, json=Mock(side_effect=ValueError), content=b"bad")]:
            get.return_value = response
            self.assertEqual(fetch_json_board("himalayas", 168), [])
            self.assertEqual(fetch_weworkremotely_jobs(168), [])

    @patch("public_boards.fetch_weworkremotely_jobs", return_value=[])
    @patch("public_boards.fetch_json_board", return_value=[])
    def test_switches_integrate_with_discovery(self, json_board, rss):
        fetch_all_jobs({})
        json_board.assert_not_called()
        rss.assert_not_called()
        fetch_all_jobs(dict(use_himalayas=True, use_arbeitnow=True, use_weworkremotely=True))
        self.assertEqual(json_board.call_count, 2)
        rss.assert_called_once_with(48)


if __name__ == "__main__":
    unittest.main()
