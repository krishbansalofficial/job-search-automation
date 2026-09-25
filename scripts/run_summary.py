"""Publish human-readable pipeline health to the GitHub Actions run summary."""
import json
import os
from pathlib import Path


def summarize(path="output/pipeline-stats.json"):
    report = Path(path)
    if not report.exists():
        return "## Job search\n\nPipeline did not produce a report. Check the failed step's logs.\n"
    counts = json.loads(report.read_text(encoding="utf-8"))["counts"]
    lines = ["## Job search", "", "Metric | Count", "--- | ---"]
    lines.extend(f"{name.replace('_', ' ')} | {value}" for name, value in sorted(counts.items()))
    if any(counts.get(name, 0) for name in ("email_errors", "sheet_errors", "resume_failures", "scoring_failed")):
        lines += ["", "Some work needs attention. Review the error counts and step logs."]
    lines += ["", "Download **tailored-resumes** for the application manifest, review page, PDFs, and diagnostics.",
              "Run `python scripts/review_jobs.py --latest` locally to prepare supported applications."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = summarize()
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as report:
            report.write(text)
