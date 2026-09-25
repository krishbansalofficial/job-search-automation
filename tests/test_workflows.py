import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_ci_runs_tests_on_push_and_pull_request_without_secrets(self):
        workflow = yaml.load((ROOT / ".github/workflows/tests.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"], ["push", "pull_request"])
        steps = workflow["jobs"]["test"]["steps"]
        self.assertTrue(any("unittest discover" in step.get("run", "") for step in steps))
        self.assertNotIn("secrets.", str(workflow))

    def test_queue_archive_restore_extracts_only_expected_file(self):
        workflow = yaml.load((ROOT / ".github/workflows/daily-job-search.yml").read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["run-pipeline"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == "Extract job queue")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = json.dumps({"version": 1, "jobs": {}})
            with ZipFile(root / "job-queue.zip", "w") as archive:
                archive.writestr("job-queue.json", content)
                archive.writestr("unwanted.txt", "ignored")
            result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / ".state/job-queue.json").read_text(), content)
            self.assertFalse((root / "unwanted.txt").exists())


if __name__ == "__main__":
    unittest.main()
