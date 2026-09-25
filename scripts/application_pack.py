"""Create a portable application-review manifest and HTML page per run."""
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from apply_assist import application_url, detect_ats
from job_queue import canonical_url


def _portable(path, output):
    """Artifact-relative PDF name, only for PDFs actually present in output."""
    path = Path(path) if path else None
    return path.name if path and path.is_file() and path.parent.resolve() == output.resolve() else None


def build_application_pack(results, output_dir, min_score=60, unscored=(), postings=(), base_resume=None):
    """postings: every eligible job this run; they get the untailored base resume."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    jobs, cards = [], []
    for result in sorted(results, key=lambda r: r["score_result"].get("match_score", 0), reverse=True):
        score, job = result["score_result"], result["job"]
        if score.get("match_score", 0) < min_score or urlsplit(job.get("url", "")).scheme != "https":
            continue
        resume_name = _portable(result.get("resume_path"), output)
        entry = {"company": job.get("company", ""), "title": job.get("title", ""),
                 "url": job["url"], "apply_url": application_url(job), "score": score["match_score"],
                 "resume": resume_name, "ats": detect_ats(application_url(job)), "kind": "match",
                 "review": job.get("eligibility_review", []),
                 "reasoning": score.get("reasoning", "")}
        jobs.append(entry)
        resume_link = f'<a href="{escape(resume_name, quote=True)}">Resume PDF</a>' if resume_name else "Resume unavailable—review manually"
        cards.append(f'<article><h2>{escape(entry["company"])} — {escape(entry["title"])}</h2>'
                     f'<p>Match: {entry["score"]}/100</p><p>{escape(entry["reasoning"])}</p>'
                     f'<p>{escape("; ".join(entry["review"]))}</p>'
                     f'<a href="{escape(entry["url"], quote=True)}" rel="noreferrer">Open posting</a> · {resume_link}</article>')
    listed = {canonical_url(entry["url"]) for entry in jobs}
    base_name = _portable(base_resume, output)
    extra = []
    for job in postings:
        if urlsplit(job.get("url", "")).scheme != "https" or canonical_url(job["url"]) in listed:
            continue
        listed.add(canonical_url(job["url"]))
        extra.append({"company": job.get("company", ""), "title": job.get("title", ""), "url": job["url"],
                      "apply_url": application_url(job), "score": None, "resume": base_name,
                      "ats": detect_ats(application_url(job)), "kind": "posting",
                      "review": job.get("eligibility_review", []), "reasoning": ""})
    # Forms the assistant can pre-fill come first.
    extra.sort(key=lambda entry: entry["ats"] is None)
    jobs.extend(extra)
    if extra:
        items = "".join(
            f'<li>{escape(e["company"])} — {escape(e["title"])} '
            f'<a href="{escape(e["url"], quote=True)}" rel="noreferrer">Open posting</a>'
            f'{" · pre-fillable (" + e["ats"] + ")" if e["ats"] else ""}</li>' for e in extra)
        cards.append(f'<article><h2>All eligible postings ({len(extra)})</h2>'
                     f'<p>Not scored yet or below the match threshold; applied to with your base resume.</p>'
                     f'<ul>{items}</ul></article>')
    waiting = "" if postings else "".join(
        f'<li>{escape(job.get("company", ""))} — {escape(job.get("title", ""))} '
        f'<a href="{escape(job["url"], quote=True)}" rel="noreferrer">Open posting</a></li>'
        for job in unscored if urlsplit(job.get("url", "")).scheme == "https")
    if waiting:
        cards.append(f'<article><h2>Eligible, not yet scored ({len(unscored)})</h2>'
                     f'<p>Gemini was unavailable; these stay queued for scoring.</p><ul>{waiting}</ul></article>')
    manifest = output / "application-manifest.json"
    manifest.write_text(json.dumps({"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "jobs": jobs}, indent=2), encoding="utf-8")
    (output / "review.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Applications to review</title>'
        '<style>body{font:17px system-ui;max-width:900px;margin:40px auto;padding:0 20px;background:#f6f7f9;color:#17212d}'
        'article{background:white;padding:24px;margin:20px 0;border:1px solid #ddd;border-radius:10px}a{color:#1658a5}</style>'
        '<h1>Applications to review</h1><p>Review each resume and requirement before submitting. '
        'For supported forms, run <code>python scripts/review_jobs.py --latest</code> from your local project.</p>'
        + ("".join(cards) or '<p>No new qualifying jobs in this run.</p>') + '</html>', encoding="utf-8")
    return manifest
