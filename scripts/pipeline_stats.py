"""Aggregate counters and rejection evidence without credentials or resumes."""
import json
from collections import Counter
from pathlib import Path


class PipelineStats:
    def __init__(self):
        self.counts = Counter({key: 0 for key in (
            "evaluated", "eligible", "eligibility_rejected", "scoring_attempted_jobs",
            "scored", "matched", "scoring_failed", "gemini_requests",
            "selection_deferred", "budget_deferred_candidates", "resumes_created",
            "resume_failures", "logged", "sheet_errors")})
        self.sources = Counter()
        self.rejections = []

    def reject(self, job, reasons):
        self.counts["eligibility_rejected"] += 1
        self.rejections.append({"url": job.get("url"), "title": job.get("title"), "reasons": reasons})

    def summary(self):
        return " | ".join(f"{key}: {value}" for key, value in sorted(self.counts.items()))

    def save(self, directory):
        path = Path(directory) / "pipeline-stats.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"counts": dict(self.counts), "sources": dict(self.sources),
                                    "rejections": self.rejections}, indent=2), encoding="utf-8")
        print("Pipeline statistics: " + self.summary())
        return str(path)
