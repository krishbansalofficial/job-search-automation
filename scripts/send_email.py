"""
Sends the morning summary email over Gmail SMTP with an app password.
No paid email API needed.

Setup:
  1. Turn on 2-Step Verification on the Gmail account you'll send from
  2. Create an App Password at https://myaccount.google.com/apppasswords
  3. Set GMAIL_APP_PASSWORD as an env var / GitHub secret
"""

import os
import smtplib
from html import escape
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def empty_run_message(stats=None):
    """Explain why a run produced no matches instead of implying nothing was found."""
    counts = stats.counts if stats else {}
    queued = counts.get("queue_remaining", 0)
    if counts.get("deferred_gemini_overloaded"):
        return f"Gemini was overloaded (503), so no jobs could be scored. {queued} jobs are queued and will be scored on a later run."
    if counts.get("deferred_gemini_quota"):
        return f"Gemini hit a rate or quota limit, so scoring stopped. {queued} jobs are queued for the next Pacific day."
    if counts.get("deferred_gemini_budget") and not counts.get("scored"):
        return f"Today's Gemini request budget is used up. {queued} jobs are queued for the next Pacific day."
    if counts.get("scored"):
        return f"Scored {counts['scored']} jobs; none reached the match threshold."
    return "No new matching postings today."


MAX_UNSCORED_ROWS = 30


def unscored_html(unscored):
    """Eligible jobs Gemini could not score yet, so a bad Gemini day still yields companies."""
    if not unscored:
        return ""
    rows = "".join(f"""
        <tr>
          <td>{escape(job.get('company', ''))}</td>
          <td>{escape(job.get('title', ''))}</td>
          <td>{escape(job.get('location', ''))}</td>
          <td><a href="{escape(job.get('url', ''), quote=True)}">Posting</a></td>
          <td>{escape('; '.join(job.get('eligibility_review', [])))}</td>
        </tr>""" for job in unscored[:MAX_UNSCORED_ROWS])
    more = f"<p>…and {len(unscored) - MAX_UNSCORED_ROWS} more in the review page.</p>" if len(unscored) > MAX_UNSCORED_ROWS else ""
    return f"""
    <h2>Eligible jobs waiting for scoring ({len(unscored)})</h2>
    <p>These passed the eligibility checks but have no match score or tailored resume yet.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Company</th><th>Title</th><th>Location</th><th>Link</th><th>Check before applying</th></tr>
      {rows}
    </table>{more}"""


def build_summary_html(results, stats=None, unscored=None):
    """results is a list of dicts: job, score_result, resume_path"""
    statistics = f"<h3>Pipeline statistics</h3><p>{escape(stats.summary())}</p>" if stats else ""
    repo, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    if repo and run_id:
        statistics += f'<p><a href="https://github.com/{escape(repo, quote=True)}/actions/runs/{escape(run_id, quote=True)}">Download the application pack and resumes from this run</a></p>'
    if not results:
        return f"<p>{escape(empty_run_message(stats))}</p>" + unscored_html(unscored) + statistics

    rows = []
    for r in sorted(results, key=lambda x: x["score_result"].get("match_score", 0), reverse=True):
        job = r["job"]
        score = r["score_result"]
        rows.append(f"""
        <tr>
          <td>{escape(job.get('company', ''))}</td>
          <td>{escape(job.get('title', ''))}</td>
          <td>{score.get('match_score', '')}</td>
          <td><a href="{escape(job.get('url', ''), quote=True)}">Posting</a></td>
          <td>{escape(', '.join(score.get('missing_keywords', [])))}</td>
          <td>{escape('; '.join(job.get('eligibility_review', [])))}</td>
        </tr>""")

    return f"""
    <h2>Job matches found today ({len(results)})</h2>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Company</th><th>Title</th><th>Score</th><th>Link</th><th>Missing keywords</th><th>Check before applying</th></tr>
      {''.join(rows)}
    </table>
    <p>Review each result before applying.</p>
    {unscored_html(unscored)}
    {statistics}
    """


def send_summary_email(to_addr, from_addr, results, attachments=None, stats=None, unscored=None):
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is not set. Create a free Gmail app "
            "password and set it as an env var / GitHub secret."
        )

    msg = MIMEMultipart("mixed")
    deferred = stats and any(stats.counts.get(k) for k in ("deferred_gemini_overloaded", "deferred_gemini_quota"))
    if results:
        msg["Subject"] = f"Job search: {len(results)} new matches"
    elif unscored:
        msg["Subject"] = f"Job search: {len(unscored)} eligible jobs (scoring deferred)"
    elif deferred:
        msg["Subject"] = "Job search: scoring deferred, Gemini unavailable"
    else:
        msg["Subject"] = "Job search: 0 new matches"
    msg["From"] = from_addr
    msg["To"] = to_addr
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(build_summary_html(results, stats, unscored), "html"))
    msg.attach(body)

    for attachment in attachments or []:
        path = os.fspath(attachment)
        with open(path, "rb") as file:
            part = MIMEApplication(file.read(), _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=os.path.basename(path))
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_addr, app_password)
        server.sendmail(from_addr, to_addr, msg.as_string())
