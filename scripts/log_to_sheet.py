"""
Writes one row per job to the Google Sheet you use for application
tracking. Uses a Google service account (free) rather than a paid
integration.

Setup (one time, see README for the full walkthrough):
  1. Create a Google Cloud project and enable the Sheets API (free)
  2. Create a service account and download its JSON key
  3. Share your tracking sheet with the service account's email
  4. Set GOOGLE_SERVICE_ACCOUNT_JSON as an env var / GitHub secret
     containing the full JSON key contents
"""

import json
import os
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from h1b_lookup import get_sponsor_history
from job_queue import canonical_url

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER = [
    "Date Found", "Company", "Title", "Location", "Match Score",
    "Missing Keywords", "H-1B Sponsor History", "Job URL",
    "Tailored Resume", "Status",
]
JOB_URL_COLUMN = HEADER.index("Job URL") + 1  # gspread columns are 1-indexed

# Every eligible scraped job, written before (and regardless of) Gemini scoring.
POSTINGS_HEADER = [
    "Date Found", "Company", "Title", "Location", "Source",
    "H-1B Sponsor History", "Job URL", "Check Before Applying",
]
POSTINGS_URL_COLUMN = POSTINGS_HEADER.index("Job URL") + 1


def _get_client():
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. See README for how "
            "to create a free service account key."
        )
    info = json.loads(raw_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_worksheet(client, sheet_name, worksheet_name, header=HEADER):
    sheet = client.open(sheet_name)
    try:
        worksheet = sheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(header))
    ensure_header(worksheet, header)
    return worksheet


def ensure_header(worksheet, header=HEADER):
    existing = worksheet.row_values(1)
    if existing != header:
        worksheet.update("A1", [header])


def get_logged_urls(sheet_name, worksheet_name):
    """
    Returns the set of Job URLs already logged in the sheet, so
    main.py can skip postings it's already scored and written down
    instead of re-scoring the same jobs every run.
    """
    try:
        client = _get_client()
        worksheet = _get_worksheet(client, sheet_name, worksheet_name)
        urls = worksheet.col_values(JOB_URL_COLUMN)[1:]  # skip header row
        return set(u for u in urls if u)
    except Exception as e:  # noqa: BLE001
        print(f"  [dedup] could not read existing URLs from sheet, skipping dedup this run: {e}")
        return set()


def log_job(sheet_name, worksheet_name, job, score_result, resume_path):
    client = _get_client()
    worksheet = _get_worksheet(client, sheet_name, worksheet_name)

    # A previous append may have succeeded before its response was lost.
    # Preserve user-maintained Status cells and avoid duplicate retry rows.
    existing = worksheet.col_values(JOB_URL_COLUMN)[1:]
    for row_number, url in enumerate(existing, start=2):
        if url and canonical_url(url) == canonical_url(job.get("url", "")):
            if resume_path:
                worksheet.update_cell(row_number, HEADER.index("Tailored Resume") + 1, resume_path)
            return

    sponsor_history = get_sponsor_history(job.get("company", ""))

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        job.get("company", ""),
        job.get("title", ""),
        job.get("location", ""),
        score_result.get("match_score", ""),
        ", ".join(score_result.get("missing_keywords", [])),
        sponsor_history,
        job.get("url", ""),
        resume_path or "",
        "Ready to review" if resume_path else "Scored - no PDF",
    ]
    worksheet.append_row(row, value_input_option="RAW")


def log_postings(sheet_name, worksheet_name, jobs):
    """Append unseen jobs to the postings tab in one write; returns rows added."""
    worksheet = _get_worksheet(_get_client(), sheet_name, worksheet_name, POSTINGS_HEADER)
    seen = {canonical_url(url) for url in worksheet.col_values(POSTINGS_URL_COLUMN)[1:] if url}
    found = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for job in jobs:
        url = canonical_url(job.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append([
            found,
            job.get("company", ""),
            job.get("title", ""),
            job.get("location", ""),
            job.get("source", ""),
            get_sponsor_history(job.get("company", "")),
            job.get("url", ""),
            "; ".join(job.get("eligibility_review", [])),
        ])
    if rows:
        worksheet.append_rows(rows, value_input_option="RAW")
    return len(rows)
