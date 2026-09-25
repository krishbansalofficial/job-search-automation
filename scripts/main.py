"""
Orchestrates the whole pipeline. This is the entry point GitHub
Actions runs every morning.

Deliberately stops short of auto submitting anything. It runs in two
passes: first it scrapes postings, filters them for eligibility and
writes every eligible one to the Postings tab (no Gemini involved);
then, as a best-effort step, it scores what the Gemini budget allows,
drafts tailored resumes and logs scores to the scores tab. You still
click apply yourself.
"""

import argparse
import json
import hashlib
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fetch_jobs import fetch_all_jobs
from score_and_tailor import score_and_tailor, fetch_job_description_text
from compile_resume import compile_resume
from log_to_sheet import log_job, log_postings, get_logged_urls
from fallback_report import create_sheet_fallback_pdf
from send_email import send_summary_email
from gemini_budget import GeminiBudget
from eligibility import check_eligibility
from pipeline_stats import PipelineStats
from job_queue import JobQueue
from score_and_tailor import SCORE_PROMPT, DEFAULT_MODELS
from application_pack import build_application_pack


def load_config():
    config_path = Path(__file__).parent.parent / "config" / "config.yml"
    if not config_path.exists():
        # A fresh clone runs on the example until you copy it to config.yml.
        config_path = config_path.with_name("config.example.yml")
    if not config_path.exists():
        raise RuntimeError(
            "config/config.yml not found. Copy config/config.example.yml "
            "to config/config.yml and fill in your own values."
        )
    return yaml.safe_load(config_path.read_text())


def prioritize(jobs, priority_titles):
    # Stable sort: preferred role families jump the FIFO backlog, oldest first within each group.
    priority_titles = [t.lower() for t in priority_titles]
    return sorted(jobs, key=lambda job: not any(t in job.get("title", "").lower() for t in priority_titles))


def quiet_when_empty():
    # Set by the workflow's extra retry runs so only the daily run emails "nothing new".
    return os.environ.get("QUIET_WHEN_EMPTY", "").lower() == "true"


def main(dry_run=False):
    config = load_config()

    resume_template_path = Path(config["resume_template_path"])
    resume_text = resume_template_path.read_text()

    budget = None if dry_run else GeminiBudget(config)
    queue = JobQueue(config.get("job_queue_path", ".state/job-queue.json"), readonly=dry_run)
    queue.configure_rules(config.get("eligibility", {}))
    stats = PipelineStats()
    already_logged = set() if dry_run else get_logged_urls(config["google_sheet_name"], config["google_sheet_worksheet"])
    # Eligibility must run before the scoring cap so rejected jobs do not
    # occupy slots. Bound description work separately from Gemini requests.
    discovery_config = dict(config, max_jobs_per_run=None)
    jobs = fetch_all_jobs(discovery_config, excluded_urls=already_logged, stats=stats)
    queue.ingest(jobs, already_logged)
    jobs = prioritize(queue.pending(), config.get("priority_titles", []))
    stats.counts["queued_pending"] = len(jobs)
    preview = []
    if not jobs:
        print("No matching postings found today.")
        stats.save(config["output_dir"])
        if dry_run:
            Path(config["output_dir"], "dry-run-jobs.json").write_text("[]", encoding="utf-8")
            return
        build_application_pack([], config["output_dir"])
        if quiet_when_empty():
            print("Retry run with nothing new; skipping summary email.")
            return
        try:
            send_summary_email(config["summary_email_to"], config["summary_email_from"], [], stats=stats)
        except Exception as e:  # noqa: BLE001
            print(f"Could not send summary email: {e}")
            stats.counts["email_errors"] += 1
            stats.save(config["output_dir"])
        return

    # The request ledger enforces pacing and attempt ceilings. This separate
    # cap bounds how many eligible jobs we try to score.
    max_jobs = config.get("max_jobs_to_score", 50)
    if config.get("max_jobs_per_run") is not None:
        max_jobs = min(max_jobs, int(config["max_jobs_per_run"]))
    candidate_limit = int(config.get("max_candidates_to_evaluate", 100))
    if max_jobs < 1 or candidate_limit < 1:
        raise ValueError("Scoring and candidate limits must be positive")

    min_score = config.get("min_match_score_to_tailor", 60)
    models = tuple(config.get("gemini_models") or DEFAULT_MODELS)
    results = []
    sheet_errors = []
    # Eligible jobs not scored this run (Gemini down or scoring cap reached);
    # they are already on the Postings tab and stay queued for scoring.
    unscored = []
    scoring_stopped = False

    # Pass 1: scrape + eligibility. Needs no Gemini, so it always completes.
    eligible = []
    for i, job in enumerate(jobs):
        if i >= candidate_limit:
            stats.counts["selection_deferred"] = len(jobs) - i
            break
        stats.counts["evaluated"] += 1
        job_description = fetch_job_description_text(job)
        stats.counts["description_" + job.get("description_quality", "unknown")] += 1
        eligibility = check_eligibility(job, job_description, config)
        job["eligibility_review"] = eligibility["review"]
        if not eligibility["eligible"]:
            stats.reject(job, eligibility["reasons"])
            queue.reject(job, eligibility["reasons"])
            print(f"Skipping {job['title']}: {', '.join(eligibility['reasons'])}")
            continue
        stats.counts["eligible"] += 1
        eligible.append((job, job_description))
        if dry_run:
            preview.append({"title": job["title"], "company": job["company"], "url": job["url"],
                            "description_quality": job.get("description_quality"), "review": eligibility["review"]})

    if not dry_run and eligible:
        try:
            stats.counts["postings_logged"] = log_postings(
                config["google_sheet_name"], config.get("google_sheet_postings_worksheet", "Postings"),
                [job for job, _ in eligible])
        except Exception as e:  # noqa: BLE001
            print(f"  could not log postings to sheet: {e}")
            sheet_errors.append(str(e))
            stats.counts["sheet_errors"] += 1

    # Pass 2: best-effort Gemini scoring of eligible jobs, logged to the scores tab.
    for n, (job, job_description) in enumerate([] if dry_run else eligible):
        if scoring_stopped or stats.counts["scoring_attempted_jobs"] >= max_jobs:
            unscored.append(job)
            continue
        stats.counts["scoring_attempted_jobs"] += 1
        print(f"Scoring {job['company']} - {job['title']}...")
        key = queue.cache_key(resume_text, job_description,
                              [SCORE_PROMPT, *models, job["company"], job["title"]])
        score_result = queue.cached_score(job, key)
        if score_result is None:
            score_result = score_and_tailor(job, resume_text, job_description, budget=budget, models=models)
        else:
            stats.counts["score_cache_hits"] += 1
        if score_result.get("status") == "deferred":
            stats.counts["budget_deferred_candidates"] = len(eligible) - n
            stats.counts["deferred_gemini_" + score_result.get("reason", "budget")] = 1
            print(score_result.get("reasoning") or "Gemini budget unavailable; remaining jobs will be scored next run.")
            scoring_stopped = True
            unscored.append(job)
            continue
        if score_result.get("status") == "failed":
            stats.counts["scoring_failed"] += 1
            print("  Scoring failed; leaving this job eligible for a later run.")
            continue

        queue.save_score(job, key, score_result)
        stats.counts["scored"] += 1
        resume_path = None
        if score_result.get("match_score", 0) >= min_score:
            stats.counts["matched"] += 1
            suffix = hashlib.sha256(job["url"].encode()).hexdigest()[:10]
            stem = f"{job['company']}_{job['title']}"[:120] + "_" + suffix
            try:
                resume_path = compile_resume(
                    resume_template_path,
                    score_result.get("bullet_suggestions", []),
                    config["output_dir"], stem,
                )
            except OSError as error:
                print(f"  Resume compilation unavailable: {error}")
            stats.counts["resumes_created" if resume_path else "resume_failures"] += 1

        queue.set_resume_pending(job, score_result.get("match_score", 0) >= min_score and not resume_path)
        try:
            log_job(
                config["google_sheet_name"],
                config["google_sheet_worksheet"],
                job,
                score_result,
                resume_path,
            )
            stats.counts["logged"] += 1
            if score_result.get("match_score", 0) < min_score or resume_path:
                queue.complete(job)
        except Exception as e:  # noqa: BLE001
            print(f"  could not log to sheet: {e}")
            sheet_errors.append(str(e))
            stats.counts["sheet_errors"] += 1

        results.append({"job": job, "score_result": score_result, "resume_path": resume_path})

    stats.counts["gemini_requests"] = budget.used if budget else 0
    stats.counts["queue_remaining"] = len(queue.pending())
    stats.counts["eligible_unscored"] = len(unscored)
    stats.save(config["output_dir"])
    if dry_run:
        Path(config["output_dir"], "dry-run-jobs.json").write_text(json.dumps(preview, indent=2), encoding="utf-8")
        print(f"Dry run complete: {len(preview)} eligible candidates; no Gemini, Sheets, email or queue writes.")
        return
    base_resume = None
    if eligible:
        try:
            base_resume = compile_resume(resume_template_path, [], config["output_dir"], "base_resume")
        except OSError as error:
            print(f"  Base resume compilation unavailable: {error}")
    build_application_pack(results, config["output_dir"], min_score, unscored=unscored,
                           postings=[job for job, _ in eligible], base_resume=base_resume)
    attachments = []
    # Attach a bounded number of successful PDFs; the artifact contains all.
    byte_limit = 15 * 1024 * 1024
    for result in sorted(results, key=lambda r: r["score_result"].get("match_score", 0), reverse=True):
        path = Path(result["resume_path"]) if result.get("resume_path") else None
        if path and path.is_file() and len(attachments) < config.get("max_resume_email_attachments", 5):
            size = path.stat().st_size
            if size <= byte_limit:
                attachments.append(str(path))
                byte_limit -= size
    if sheet_errors:
        fallback_pdf = create_sheet_fallback_pdf(
            results, config["output_dir"], sheet_errors[0]
        )
        if fallback_pdf:
            attachments.append(fallback_pdf)
            print(f"Created Google Sheets fallback report: {fallback_pdf}")

    matches = [r for r in results if r["score_result"].get("match_score", 0) >= min_score]
    if quiet_when_empty() and not matches and not sheet_errors:
        print("Retry run with no new matches; skipping summary email.")
    else:
        try:
            send_summary_email(
                config["summary_email_to"],
                config["summary_email_from"],
                matches,
                attachments=attachments,
                stats=stats,
                unscored=unscored,
            )
        except Exception as e:  # noqa: BLE001
            print(f"Could not send summary email: {e}")
            stats.counts["email_errors"] += 1
    stats.save(config["output_dir"])

    print(f"Done. Processed {len(results)} postings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover and score job postings")
    parser.add_argument("--dry-run", action="store_true", help="Preview discovery/eligibility without Gemini, Sheets, email or queue writes")
    main(dry_run=parser.parse_args().dry_run)
