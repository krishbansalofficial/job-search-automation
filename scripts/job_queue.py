"""Atomic local queue. Run one writer at a time; Actions serializes runs."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        host = "job-boards.greenhouse.io"
    if host in {"www.linkedin.com", "linkedin.com"}:
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)$", path)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match[1]}"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in
             {"source", "ref", "refid", "trackingid", "trk", "gh_src", "lever-source"}]
    return urlunsplit((parsed.scheme.lower(), host, path, urlencode(sorted(query)), ""))


def fingerprint(job):
    values = [re.sub(r"\W+", " ", job.get(key, "").casefold()).strip()
              for key in ("company", "title", "location")]
    if not all(values) or values[0].startswith("unknown") or job.get("source") == "x":
        return None
    return tuple(values)


def deduplicate_jobs(jobs):
    """Only merge different-source metadata matches; retain same-board openings."""
    jobs = list(jobs)
    source_ids = {}
    for job in jobs:
        signature = fingerprint(job)
        if signature:
            source_ids.setdefault(signature, {}).setdefault(job.get("source"), set()).add(canonical_url(job.get("url", "")))
    ambiguous = {signature for signature, sources in source_ids.items()
                 if any(len(ids) > 1 for ids in sources.values())}
    result, urls, metadata = [], {}, {}
    for original in jobs:
        job = dict(original)
        url = canonical_url(job.get("url", ""))
        if not url:
            continue
        signature = fingerprint(job)
        previous = urls.get(url)
        if previous is None and signature not in ambiguous and signature in metadata and len(metadata[signature]) == 1:
            candidate = metadata[signature][0]
            if candidate.get("source") != job.get("source"):
                previous = candidate
        if previous is not None:
            previous["alternate_urls"] = sorted(set([*previous.get("alternate_urls", []), job["url"],
                                                     *job.get("alternate_urls", [])]) - {previous["url"]})
            if job.get("description") and (url == canonical_url(previous["url"]) or
                    len(job["description"]) > len(previous.get("description") or "")):
                previous["description"] = job["description"]
                previous["description_quality"] = job.get("description_quality", "feed")
            urls[url] = previous
            continue
        result.append(job)
        urls[url] = job
        for alias in job.get("alternate_urls", []):
            urls[canonical_url(alias)] = job
        if signature:
            metadata.setdefault(signature, []).append(job)
    return result


class JobQueue:
    def __init__(self, path, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1, "jobs": {}}
        if self.state.get("version") != 1 or not isinstance(self.state.get("jobs"), dict):
            raise ValueError("Unsupported or corrupt queue; restore its backup before running")

    def save(self):
        if self.readonly:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def ingest(self, jobs, logged_urls=()):
        existing = [record["job"] for record in self.state["jobs"].values()]
        merged = deduplicate_jobs(existing + jobs)
        logged = {canonical_url(url) for url in logged_urls}
        records = {}
        for job in merged:
            key = canonical_url(job["url"])
            record = self.state["jobs"].get(key, {"job": job, "status": "pending",
                "first_seen": datetime.now(timezone.utc).isoformat()})
            record["job"] = job
            aliases = {key, *(canonical_url(url) for url in job.get("alternate_urls", []))}
            if aliases & logged and not record.get("needs_resume"):
                record["status"] = "completed"
            records[key] = record
        self.state["jobs"] = records
        self.save()

    def pending(self):
        return [record["job"] for record in sorted(self.state["jobs"].values(), key=lambda r: r["first_seen"])
                if record["status"] == "pending"]

    def configure_rules(self, rules):
        if self.state.get("eligibility_rules") != rules:
            for record in self.state["jobs"].values():
                if record["status"] == "rejected":
                    record["status"] = "pending"
            self.state["eligibility_rules"] = rules

    def reject(self, job, reasons):
        record = self.state["jobs"][canonical_url(job["url"])]
        record.update(status="rejected", rejection_reasons=reasons)
        self.save()

    def complete(self, job):
        self.state["jobs"][canonical_url(job["url"])]["status"] = "completed"
        self.save()

    def set_resume_pending(self, job, pending):
        self.state["jobs"][canonical_url(job["url"])]["needs_resume"] = pending
        self.save()

    def cache_key(self, resume, description, scoring_version):
        return hashlib.sha256(json.dumps([resume, description, scoring_version]).encode()).hexdigest()

    def cached_score(self, job, key):
        record = self.state["jobs"][canonical_url(job["url"])]
        return record.get("score") if record.get("score_key") == key else None

    def save_score(self, job, key, score):
        record = self.state["jobs"][canonical_url(job["url"])]
        record.update(score_key=key, score=score)
        self.save()
