# Job Search Automation (free stack, no VPS required)

Finds fresh job postings, scores them against your resume, drafts a
tailored version of the relevant bullets, compiles a PDF, logs
everything to a tracking spreadsheet, and emails you a morning
summary.

The spreadsheet has two tabs, both created automatically:

- **Postings** (`google_sheet_postings_worksheet`): every eligible
  scraped job. Written before any Gemini call, so it fills up even
  when scoring is down or out of budget.
- **Applications** (`google_sheet_worksheet`): the scores tab, with
  one row per scored job (match score, missing keywords, tailored
  resume, status). Runs on GitHub Actions' free schedule, so there is no
server to pay for or maintain.

It deliberately does not auto submit applications. It gets you to
"here is a reviewed, tailored PDF and a link" every morning; you
still click apply.

## What it costs

Nothing, at normal usage levels:

- GitHub Actions: free scheduled runs (more minutes if you're on the
  free GitHub Student Developer Pack, but not required)
- Gemini API: free tier is enough for scoring and rewording a
  handful of postings a day
- Greenhouse / Lever APIs: public, free, no key needed
- Google Sheets API: free with a Google account
- Gmail SMTP: free with an app password

## One time setup

1. **Get a Gemini API key**
   Go to https://aistudio.google.com, create a free key.

2. **Create a Google service account for Sheets access**
   - In Google Cloud Console, create a project (free) and enable both the
     Google Sheets API and Google Drive API. The Drive API is needed to find
     the existing spreadsheet by its configured name.
   - Create a service account, then create and download a JSON key
     for it.
   - Open the tracking spreadsheet you want to use (create a blank
     one if you don't have it yet) and share it with the service
     account's email address (found in the JSON key, looks like
     `something@project-id.iam.gserviceaccount.com`), giving it
     Editor access.

3. **Create a Gmail app password**
   Turn on 2-Step Verification on the Gmail account you want to send
   from, then create an app password at
   https://myaccount.google.com/apppasswords.

4. **Add your resume**
   Replace `resume/resume_template.tex` with your actual LaTeX resume.
   Keep each bullet as a distinct string on its own `\resumeItem{...}`
   line (or your template's equivalent) so the tailoring script can
   find and swap individual bullets.

5. **Fill in your config**
   Copy `config/config.example.yml` to `config/config.yml` and edit
   the company lists, target titles, email addresses, and (if needed)
   `max_jobs_per_run`. The default cap is 25 so Actions can finish and
   update Sheets reliably.

6. **Add GitHub secrets**
   In your repo: Settings, Secrets and variables, Actions. Add:
   - `GEMINI_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the entire contents of the
     JSON key file)
   - `GMAIL_APP_PASSWORD`

7. **Keep your copy private and opt in**
   Use a private repository (or a private fork): Actions artifacts contain
   your tailored resumes and are downloadable by anyone on a public repo.
   Then add the repository variable `ENABLE_PIPELINE` = `true`
   (Settings, Secrets and variables, Actions, Variables).

8. **Push the repo and enable Actions**
   The workflow in `.github/workflows/daily-job-search.yml` runs
   automatically every morning (11:00 UTC), plus retry runs at 15:00,
   19:00 and 23:00 UTC for days when Gemini is overloaded. Retry runs share
   the daily request budget and only email when they find matches. You can also trigger it manually from
   the Actions tab (workflow_dispatch) to test it before trusting the
   schedule.

## Running it locally first

Before relying on the scheduled run, test locally:

```bash
pip install -r requirements.txt
cp config/config.example.yml config/config.yml   # then edit it
export GEMINI_API_KEY=...
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat path/to/key.json)"
export GMAIL_APP_PASSWORD=...
python scripts/main.py
```

## Extending it

### Additional public job boards

Eleven additional employer boards are enabled: Robinhood, Roblox, Affirm,
Coinbase, Discord, Reddit, and Dropbox through Greenhouse; Perplexity,
Snowflake, SentiLink, and Ashby through Ashby. All returned nonempty public
feeds on September 24, 2026 and require no new API keys. These employers offer
US roles, but their feeds also include international postings. Existing title,
age, eligibility and scoring limits still apply; an active board does not
guarantee an eligible junior opening. Edit `greenhouse_companies` and
`ashby_companies` in `config/config.yml` to manage these sources.

**US employer boards:** ServiceNow, Western Digital, Intuitive, Solidigm, and
Arista Networks are enabled under `smartrecruiters_us_companies`. Their public
SmartRecruiters feeds require no keys. Each request uses `country=us`, and the
parser also checks the returned country. Both onsite and remote roles with a
US job location can appear; this does not guarantee sponsorship or remote-work
eligibility. Other existing feeds retain their existing geographic scope.

Each employer is limited to five pages of 100 listings per run. Existing age,
title, eligibility and scoring limits apply; supported detail endpoints provide
descriptions when candidates are evaluated. These boards returned US postings
in live checks on September 23, 2026. The integration follows the
[SmartRecruiters public Posting API](https://developers.smartrecruiters.com/docs/endpoints).

Three more sources are enabled in `config/config.yml`, with no API keys:

| Board | Coverage | Config switch | Fetch limit per run |
| --- | --- | --- | --- |
| [Himalayas](https://himalayas.app/docs/remote-jobs-api) | Remote roles with country restrictions | `use_himalayas` | Five cursor pages, up to 100 listings |
| [We Work Remotely](https://weworkremotely.com/remote-job-rss-feed) | Remote roles | `use_weworkremotely` | One public RSS feed |
| [Arbeitnow](https://www.arbeitnow.com/blog/job-board-api) | Germany-focused onsite, hybrid and remote roles | `use_arbeitnow` | Up to five API pages |

These are bounded discovery samples, not complete inventories. Himalayas refreshes
its API daily. Existing age/title filters, deduplication, eligibility checks and
Gemini budgets apply to these sources too. Feed descriptions are retained for
scoring, and original board links are preserved for attribution. Missing dates
remain eligible, consistent with the existing feeds; missing locations stay
unknown except when Himalayas explicitly supplies no country restrictions.
An unavailable or malformed feed stops that source and leaves others running.
Set any switch to `false` to disable it. Discovery does not submit applications.

The public endpoints returned HTTP 200 during checks on September 23, 2026.
Offline fixture tests cover normalization, age/expiry filtering, pagination,
configuration switches and feed failures.

### Optional X and LinkedIn sources

Both sources are disabled by default in `config/config.yml` and feed the same
title filters, URL deduplication, selection cap, and Gemini scoring budget.
Discovery itself makes no Gemini requests.

- **X:** set `use_x: true`, customize `x_search_query`, and add the GitHub
  Actions secret `X_BEARER_TOKEN` from your X developer app. Local runs read
  the same environment variable. Uses the official
  [recent-search API](https://docs.x.com/x-api/posts/search/introduction), one
  request of up to 10 posts per run, without pagination. API access may cost
  money; check your X account's access and billing before enabling it. Results
  are hiring leads, with the post text used for scoring and the post URL for
  review. Employer identity and job availability are not verified.
- **LinkedIn:** `use_linkedin_public: true` enables parsing public guest job
  cards for the first two `linkedin_search_queries`, one page each, using
  `linkedin_location` and internship/entry-level filters. This is an unofficial,
  unsupported interface. [LinkedIn prohibits automated scraping](https://www.linkedin.com/help/linkedin/answer/a1341387);
  it may block requests or change its markup. The code uses no account cookies,
  proxies, retries, or access-control bypass. Blocked or empty responses stop
  that source. Only card metadata is collected; scoring falls back to the job
  title because the scraper does not fetch full descriptions.

Neither integration has been verified against authenticated/live account access;
tests use recorded-shape fixtures and mocked responses. A failed source leaves
the other feeds running. No automatic applications are submitted.

### Persistent queue, deduplication, and dry runs

Run a preview with:

```bash
python scripts/main.py --dry-run
```

This fetches public sources and evaluates eligibility, then writes
`output/dry-run-jobs.json` and `output/pipeline-stats.json`. It does not access
Sheets, call Gemini, compile resumes, send emails, or write the queue/usage
ledger. It can still consume enabled non-Gemini source API allowances (such as
X or Adzuna). Without reading Sheets, the preview may include previously logged
jobs that are not already marked complete in the local queue.

Normal runs store `.state/job-queue.json` atomically. Newly discovered jobs are
saved before scoring. Pending jobs survive the seven-day discovery cutoff and
are processed oldest-first; they may have closed since discovery, so review
availability before applying. Successful Sheet writes mark jobs complete.
Failures retain jobs and cache successful scores for retry without another
Gemini call. Cache keys include resume, description, prompt, models, title and
company, so changed inputs invalidate scores. No resume text is stored in cache
keys, though cached bullet suggestions can contain resume excerpts.

Explicit eligibility rejections are retained separately, and changing the
eligibility configuration reopens them. Queue records are not automatically
pruned. Local runs must have only one writer. A corrupt queue fails visibly
instead of silently discarding pending work.

Deduplication removes known URL tracking parameters while preserving job IDs.
Across different sources, exact normalized company/title/location matches can
also merge; alternate URLs are retained for Sheet-history checks. Same-source
openings with distinct IDs and different locations are retained. Metadata
matching is a heuristic: separate openings with identical metadata on different
sources can still merge. Missing locations and unknown employers are not used
for metadata matching.

The scheduled workflow restores the latest `job-queue-main` artifact and saves
a new snapshot after each run, with 90-day retention (subject to repository
limits). Only the main branch runs the production job. Initial setup or deletion/
expiration of all artifacts starts a new queue; download a backup for longer
term retention. Queue artifacts contain job descriptions and cached scoring
results and inherit the repository's artifact access permissions. This queue
storage is separate from the existing cached Gemini request ledger.

The `Tests` workflow runs the offline regression suite and Python compilation
checks on every push and pull request, without API credentials. Run the same
checks locally with `python -m unittest discover -s tests -v`.

### Discovery and Gemini limits

The configured employer boards now include Cloudflare, Datadog, Stripe,
Figma, Duolingo, Palantir, Zoox, Notion, Ramp, and Linear. Their public API
endpoints were checked successfully on September 22, 2026; open roles and
board availability can change. These are discovery sources, not guarantees
of junior openings or sponsorship.

Descriptions are retained from Greenhouse, Lever (including requirement lists),
Ashby, Recruitee and the remote-job feeds. Missing descriptions can be fetched
from supported Greenhouse, Lever and SmartRecruiters detail endpoints.
Community-repo Greenhouse/Lever links also use these detail endpoints.
Other missing descriptions fall back to the title and are flagged for review;
Adzuna text is marked as a snippet, not a complete description.

Before Gemini, `eligibility` rules skip senior titles, explicit experience
minimums over two years, explicit sponsorship refusals, and an unambiguous
graduation-year mismatch. The configured graduation year is 2026, from the
resume, and sponsorship is required, from the existing application profile.
These values are stored in pipeline config because local `profile.yml` is not
available in Actions. Geography is unrestricted by default; configure
`excluded_location_terms` to exclude specific location phrases. Citizenship,
clearance, uncertain sponsorship and ambiguous education/experience alternatives
remain for human review. These rules are heuristics, not an eligibility verdict.

`target_titles` are case-insensitive substrings of the posting title, so prefer
specific phrases ("Technology Consultant", not "Consultant"). Queued jobs whose
titles contain a `priority_titles` phrase are evaluated before the rest of the
first-in-first-out backlog, so preferred roles are not stuck behind older postings.

`max_candidates_to_evaluate: 100` bounds local description/eligibility work.
Rejected candidates do not consume the scoring-job cap or Gemini budget.
Every normal run writes `output/pipeline-stats.json`, including per-source
counts, title rejections, duplicates, eligibility rejection reasons/URLs,
scoring results, actual Gemini attempts, and resume/Sheet outcomes. The first
discovery count is **after each source's age filter**, not its raw inventory.
Logs and summary emails show aggregate counts, even with no matches, and
Actions uploads the JSON alongside PDFs. Unexamined candidates are reported
as deferred rather than assumed eligible. Reports describe this run; deferred jobs are retained in the persistent queue described below.

Discovery uses public job feeds, not Gemini. The pipeline searches the last
seven days, removes already logged URLs **before** the 25-job selection cap,
and prioritizes early-career titles over unspecified and senior titles.
The score threshold remains 60; a larger candidate pool does not guarantee
a particular number of good matches.

`gemini_max_requests_per_run` and `gemini_max_requests_per_day` both default
to 20 total API attempts across models, including retries. Every attempt is
reserved on disk before sending it, with at least 15 seconds between attempts.
Any 429 stops further Gemini calls until the next Pacific day. A 503
("high demand") skips that model for the rest of the run without blocking the
day; if every model is overloaded, remaining jobs defer to the next run. Failed/deferred
jobs are not logged as completed, so they remain eligible while in the age window.

Set these ceilings and `gemini_request_delay_seconds` below your project's
actual limits in [AI Studio](https://aistudio.google.com/usage?timeRange=last-28-days&tab=rate-limit).
Google limits requests and tokens per minute as well as requests per day;
these defaults are pipeline safeguards, not a claim about your account quota.
Usage from other applications is not visible to this pipeline.

Actions serializes workflow runs and caches `.state/gemini-usage.json` between
runs. Local runs retain their own ledger; do not run multiple local processes
or local and Actions scoring concurrently against the same project. A deleted
or evicted Actions cache loses its usage history. Existing usage before this
change is also unknown, so begin using the ledger after a daily quota reset.

Run offline regression checks with `python -m unittest discover -s tests -v`.

- **More companies**: add slugs to `config.yml` under
  `greenhouse_companies`, `lever_companies`, `ashby_companies`,
  `smartrecruiters_companies`, or `recruitee_companies`, depending on
  which ATS a company's careers page runs on. Find the slug from the
  careers page URL, e.g. `jobs.ashbyhq.com/<slug>`,
  `careers.smartrecruiters.com/<slug>`, `<slug>.recruitee.com`.
  (Workable was tried too, but its public jobs API now returns an
  empty list for every account regardless of open postings, so it
  isn't a usable source.)
- **Handshake**: Handshake doesn't have a public API, so it isn't
  included here. Check it manually since VT gives you direct access
  and employers post there specifically for students.
- **Stricter filtering**: raise `min_match_score_to_tailor` in
  `config.yml` if you're getting too many low quality matches.
- **Google Sheets fallback**: if a Sheet write fails, the email includes a
  PDF report of that run and the same report is available in the Actions
  artifact. The workflow still tries Sheets first on every run.
- **Cover letters**: add a second prompt in `score_and_tailor.py`
  that drafts a short cover letter paragraph the same way the bullet
  suggestions are drafted, then compile it alongside the resume.

## Applying faster (semi-automated, local only)

### Daily application packs and one-command review

Scheduled runs now build `application-manifest.json` and `review.html` alongside
the PDFs in the **tailored-resumes** artifact. Email includes up to five tailored
PDFs (bounded to 15 MB before email encoding) and a link to the run's complete
artifact. You can change `max_resume_email_attachments` in config.

One-time local setup:

```bash
pip install -r requirements-apply.txt
python -m playwright install chromium
python scripts/doctor.py --local
```

Prepare your next five supported applications:

```bash
python scripts/review_jobs.py --latest
```

This downloads the latest main-branch artifact using GitHub CLI, reusing an
existing GitHub CLI login or configured Git credential helper. If neither is
authenticated, run `gh auth login`. For another repository, pass
`--repo owner/name`. To preview without opening forms, add `--list`.

The command opens one supported form at a time, fills the profile fields and
uploads its tailored PDF. You review all answers and submit yourself. After
closing the browser, answer `a` for applied, `s` to skip, Enter for later, or
`q` to quit. Only your explicit answer updates `.state/application-status.json`.
Applied/skipped jobs are omitted from subsequent local sessions. This status
is local, not automatically synchronized to Google Sheets. Unsupported sites
and jobs without PDFs remain manual; see their links in the HTML review page.
Use `--manifest path/to/application-manifest.json` for an older downloaded pack.
The latest artifact contains that run's matches, not every historical match.

The scheduled workflow checks dependencies and required secrets before running
and publishes counts/errors to the Actions summary. PDF failures remain queued
and retry with their saved score on subsequent runs; Sheet writes check for an
existing URL so a retry doesn't overwrite your manually maintained Status or
append a duplicate row. PDF filenames include a job-URL hash to avoid collisions.
The Gemini ledger is backed up in the queue artifact as well as the existing
cache, and restoration retains the higher same-day request count and any quota
block. Run only one local pipeline writer at a time; cloud/local ledgers still
do not share live updates.

`scripts/apply_assist.py` opens a specific job's real application
page in a visible browser, fills in your contact info from
`config/profile.yml`, uploads your resume, and answers dropdown,
radio and autocomplete questions (work authorization, sponsorship,
country, school, degree, EEO self-identification) from the
`screening_answers` rules you write in your profile -- then stops and
hands control back to you. It prints which questions it answered and
which required fields are still empty. It never clicks submit and
never ticks checkboxes; questions with no matching rule stay blank.

### Applying to every eligible posting

The application pack now includes every eligible job from the
Postings tab, not just scored matches. Scored matches come first with
their tailored PDF; the other postings use `base_resume.pdf` (your
untailored template, compiled each run), with pre-fillable forms
listed first. Greenhouse postings hosted on a company's own careers
site are opened through Greenhouse's hosted application form, so they
are pre-fillable too.

```bash
python scripts/review_jobs.py --latest --only posting --limit 20
```

`--only match` limits it to scored matches; the default `all` does
both. Copy the `screening_answers` block from
`config/profile.example.yml` into your `config/profile.yml` and make
every answer true for you before using it.

It only supports Greenhouse, Lever, and Ashby postings -- the three
ATSes whose application forms are stable enough to target reliably
(tested against live postings). SmartRecruiters and Recruitee aren't
wired up.

This is deliberately a separate, local-only tool, not part of the
scheduled GitHub Actions workflow: Actions runs headless with no one
at a keyboard to review the form and click submit, so pre-filling a
form there would either need to auto-submit (see below for why that's
avoided) or would just be pointless work with nobody to finish it.

Setup:

```bash
pip install playwright
playwright install chromium
cp config/profile.example.yml config/profile.yml   # then edit it
```

Usage, once you have a posting you want to apply to (e.g. from the
tracking sheet or the morning email):

```bash
python scripts/apply_assist.py <job-posting-url> --resume path/to/tailored.pdf
```

## A note on scope

This intentionally stops at "form pre-filled, ready for your
review," not "submitted." Job sites' own terms of service generally
do not allow automated form submission, and a high volume of
applications that all look machine generated tends to work against
you with recruiters even when the keywords line up. The time this
saves is
best spent on the outreach and referral side of the search, not on
pushing the daily application count higher.
