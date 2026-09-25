import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import main
from eligibility import check_eligibility
from fetch_jobs import fetch_all_jobs


def matched_titles(titles):
    config = main.load_config()
    jobs = [{"title": title, "url": f"https://example.com/{i}", "company": "Example", "location": "Remote"}
            for i, title in enumerate(titles)]
    only_working_nomads = {"target_titles": config["target_titles"], "use_working_nomads": True,
                           "max_jobs_per_run": None}
    with patch("fetch_jobs.fetch_workingnomads_jobs", return_value=jobs):
        return {job["title"] for job in fetch_all_jobs(only_working_nomads)}


class TargetTitleTests(unittest.TestCase):
    def test_forward_deployed_and_consulting_roles_match(self):
        wanted = ["Forward Deployed Engineer", "Forward-Deployed Engineer", "Deployment Strategist",
                  "Associate Consultant - Entry-Level Technology Consulting", "Technical Consultant, Enterprise",
                  "Solutions Engineer", "Full-Stack Developer", "Business Technology Analyst"]
        self.assertEqual(matched_titles(wanted), set(wanted))

    def test_generic_consultant_titles_do_not_match(self):
        self.assertEqual(matched_titles(["Beauty Consultant", "Audit Consultant", "Insurance Sales Consultant"]), set())

    def test_leadership_titles_are_rejected_but_leadership_programs_are_not(self):
        rules = {"eligibility": {"exclude_senior_titles": True}}
        for title in ["Lead Forward Deployed Engineer", "Head of Product Management, Forward Deployed",
                      "VP, Forward Deployed Engineering"]:
            self.assertIn("senior_title", check_eligibility({"title": title}, "", rules)["reasons"], title)
        program = check_eligibility({"title": "Technology Leadership Program Analyst"}, "", rules)
        self.assertNotIn("senior_title", program["reasons"])

    def test_priority_titles_jump_the_backlog(self):
        jobs = [{"title": "Software Engineer"}, {"title": "Forward Deployed Engineer"}, {"title": "Data Analyst"},
                {"title": "Technology Consultant"}]
        jobs = main.prioritize(jobs, main.load_config()["priority_titles"])
        self.assertEqual([j["title"] for j in jobs],
                         ["Forward Deployed Engineer", "Technology Consultant", "Software Engineer", "Data Analyst"])


if __name__ == "__main__":
    unittest.main()
