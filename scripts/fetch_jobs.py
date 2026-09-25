"""
Pulls fresh job postings from public feeds and optional social sources.

Sources:
  1. Greenhouse public job board API (no key required)
  2. Lever public job board API (no key required)
  3. The community maintained "New Grad Positions" GitHub repo,
     parsed from its README table (updated daily by many contributors)
  4. Working Nomads public JSON API (no key required)
  5. RemoteOK public JSON API (no key required)
  6. Remotive public API (no key required, listings delayed 24h)
  7. Jobicy public API (no key required, listings delayed ~6h)
  8. Adzuna public API (free registration, covers US/UK/SG onsite roles)

Optional social sources are configured separately; LinkedIn's guest
interface is unofficial and subject to its restrictions.
"""

import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from job_descriptions import lever_description
from job_queue import canonical_url, deduplicate_jobs

USER_AGENT = "job-search-automation/1.0 (personal use)"


def _get(url, **kwargs):
    """Fetch one public source without letting one outage end the run."""
    try:
        return requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=20, **kwargs
        )
    except requests.RequestException as error:
        print(f"  request failed for {url}: {error}")
        return None


def _within_age_limit(posted_at_iso, max_age_hours):
    if not posted_at_iso:
        return True
    try:
        posted = datetime.fromisoformat(posted_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - posted).total_seconds() / 3600
    return age_hours <= max_age_hours


def fetch_greenhouse_jobs(company_slug, max_age_hours):
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"
    resp = _get(url, params={"content": "true"})
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [greenhouse] {company_slug}: request failed ({resp.status_code})")
        return []

    jobs = []
    for job in resp.json().get("jobs", []):
        posted_at = job.get("updated_at")
        if not _within_age_limit(posted_at, max_age_hours):
            continue
        jobs.append({
            "source": "greenhouse",
            "company": company_slug,
            "title": job.get("title", "").strip(),
            "url": job.get("absolute_url", ""),
            "posted_at": posted_at,
            "location": (job.get("location") or {}).get("name", ""),
            "description_url": f"{url}/{job.get('id')}",
            "description": job.get("content", ""),
        })
    return jobs


def fetch_lever_jobs(company_slug, max_age_hours):
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [lever] {company_slug}: request failed ({resp.status_code})")
        return []

    jobs = []
    for job in resp.json():
        created_ms = job.get("createdAt")
        posted_at = None
        if created_ms:
            posted_at = datetime.fromtimestamp(
                created_ms / 1000, tz=timezone.utc
            ).isoformat()
        if not _within_age_limit(posted_at, max_age_hours):
            continue
        categories = job.get("categories", {})
        jobs.append({
            "source": "lever",
            "company": company_slug,
            "title": job.get("text", "").strip(),
            "url": job.get("hostedUrl", ""),
            "posted_at": posted_at,
            "location": categories.get("location", ""),
            "description_url": f"https://api.lever.co/v0/postings/{company_slug}/{job.get('id')}",
            "description": lever_description(job),
        })
    return jobs


def fetch_ashby_jobs(company_slug, max_age_hours):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [ashby] {company_slug}: request failed ({resp.status_code})")
        return []

    try:
        data = resp.json()
    except ValueError:
        print(f"  [ashby] {company_slug}: response wasn't valid JSON")
        return []

    jobs = []
    for job in data.get("jobs", []):
        posted_at = job.get("publishedAt")
        if not _within_age_limit(posted_at, max_age_hours):
            continue
        jobs.append({
            "source": "ashby",
            "company": company_slug,
            "title": job.get("title", "").strip(),
            "url": job.get("jobUrl", ""),
            "posted_at": posted_at,
            "location": job.get("location", ""),
            "description_url": job.get("jobUrl", ""),
            "description": job.get("descriptionPlain") or job.get("descriptionHtml", ""),
        })
    return jobs


def fetch_smartrecruiters_jobs(company_slug, max_age_hours, country=None):
    """Read at most five pages, optionally restricted to an ISO country code."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings"
    records, seen = [], set()
    for offset in range(0, 500, 100):
        params = {"limit": 100, "offset": offset}
        if country:
            params["country"] = country.lower()
        resp = _get(url, params=params)
        if resp is None or resp.status_code != 200:
            print(f"  [smartrecruiters] {company_slug}: request failed; stopping source")
            break
        try:
            data = resp.json()
        except ValueError:
            print(f"  [smartrecruiters] {company_slug}: response wasn't valid JSON")
            break
        page = data.get("content") if isinstance(data, dict) else None
        if not isinstance(page, list) or not page:
            break
        added = 0
        for record in page:
            if not isinstance(record, dict):
                continue
            job_id = record.get("id")
            if not isinstance(job_id, (str, int)) or not job_id or job_id in seen:
                continue
            seen.add(job_id)
            records.append(record)
            added += 1
        if not added or len(page) < 100:
            break
    jobs = []
    for job in records:
        posted_at = job.get("releasedDate")
        if not _within_age_limit(posted_at, max_age_hours):
            continue
        location = job.get("location") or {}
        if not isinstance(location, dict):
            continue
        if country and str(location.get("country", "")).lower() != country.lower():
            continue
        title = job.get("name")
        if not isinstance(title, str) or not title.strip():
            continue
        apply_url = f"https://jobs.smartrecruiters.com/{company_slug}/{job.get('id', '')}"
        jobs.append({
            "source": "smartrecruiters",
            "company": (job.get("company") or {}).get("name") or company_slug,
            "title": title.strip(),
            "url": apply_url,
            "posted_at": posted_at,
            "location": ", ".join(
                p for p in [location.get("city"), location.get("region"), location.get("country")] if p
            ),
            "description_url": f"{url}/{job.get('id', '')}",
        })
    return jobs


def fetch_recruitee_jobs(company_slug, max_age_hours):
    url = f"https://{company_slug}.recruitee.com/api/offers/"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [recruitee] {company_slug}: request failed ({resp.status_code})")
        return []

    try:
        data = resp.json()
    except ValueError:
        print(f"  [recruitee] {company_slug}: response wasn't valid JSON")
        return []

    jobs = []
    for job in data.get("offers", []):
        posted_at = job.get("published_at")
        if not _within_age_limit(posted_at, max_age_hours):
            continue
        apply_url = job.get("careers_url", "")
        jobs.append({
            "source": "recruitee",
            "company": company_slug,
            "title": job.get("title", "").strip(),
            "url": apply_url,
            "posted_at": posted_at,
            "location": ", ".join(p for p in [job.get("city"), job.get("country")] if p),
            "description_url": apply_url,
            "description": job.get("description") or job.get("jobDescription", ""),
        })
    return jobs


def _parse_age_to_hours(age_text):
    """Converts strings like '0d', '5d', '2w', '1mo' to an hour count."""
    if not age_text:
        return None
    match = re.match(r"(\d+)\s*(h|d|w|mo|y)", age_text.strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    multiplier = {"h": 1, "d": 24, "w": 24 * 7, "mo": 24 * 30, "y": 24 * 365}
    return value * multiplier.get(unit, 24)


def fetch_simplify_new_grad_jobs(readme_url, max_age_hours):
    """
    Parses the real HTML tables in the community maintained New Grad
    Positions repo README. Each role category is a separate <table>
    with columns: Company, Role, Location, Application, Age.

    The Application cell holds two links: the actual employer apply
    page, and a second link to Simplify's own tracking page. This
    takes the first one, the real employer link.

    Rows marked with '↳' are additional openings at the same company
    as the row above, so the company name carries forward.
    """
    resp = _get(readme_url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [simplify-repo] request failed ({resp.status_code})")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    last_company = None

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td"])
            if len(cells) < 4:
                continue

            company_cell, role_cell, location_cell, application_cell = cells[0:4]
            age_cell = cells[4] if len(cells) > 4 else None

            company_text = company_cell.get_text(strip=True)
            if company_text in ("↳", ""):
                company = last_company
            else:
                company = re.sub(r"^🔥\s*", "", company_text).strip()
                last_company = company

            if not company:
                continue

            title = role_cell.get_text(strip=True)

            location_text = location_cell.get_text(separator="|", strip=True)
            location = location_text.split("|")[0] if location_text else ""

            links = application_cell.find_all("a")
            if not links or not links[0].get("href"):
                continue
            apply_url = links[0]["href"]

            age_text = age_cell.get_text(strip=True) if age_cell else None
            age_hours = _parse_age_to_hours(age_text)
            posted_at = None
            if age_hours is not None:
                posted_at = (
                    datetime.now(timezone.utc) - timedelta(hours=age_hours)
                ).isoformat()

            if not _within_age_limit(posted_at, max_age_hours):
                continue

            jobs.append({
                "source": "simplify-new-grad-repo",
                "company": company,
                "title": title,
                "url": apply_url,
                "posted_at": posted_at,
                "location": location,
                "description_url": apply_url,
            })

    return jobs


def fetch_workingnomads_jobs(max_age_hours):
    """
    Working Nomads publishes a genuine public JSON endpoint, no login,
    no scraping, no API key.
    """
    url = "https://www.workingnomads.com/api/exposed_jobs/"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [working-nomads] request failed ({resp.status_code})")
        return []

    try:
        raw_jobs = resp.json()
    except ValueError:
        print("  [working-nomads] response wasn't valid JSON, skipping")
        return []

    if raw_jobs:
        print(f"  [working-nomads] sample record keys: {list(raw_jobs[0].keys())}")

    jobs = []
    for job in raw_jobs:
        title = job.get("title") or job.get("job_title") or ""
        company = (
            job.get("company_name") or job.get("companyName")
            or job.get("company") or ""
        )
        apply_url = job.get("url") or job.get("job_url") or ""
        location = job.get("location") or ""
        posted_at = job.get("pub_date") or job.get("publishedAt") or None

        if posted_at:
            try:
                posted_at = datetime.fromisoformat(
                    str(posted_at).replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                posted_at = None

        if not _within_age_limit(posted_at, max_age_hours):
            continue
        if not title or not apply_url:
            continue

        jobs.append({
            "source": "working-nomads",
            "company": company,
            "title": title,
            "url": apply_url,
            "posted_at": posted_at,
            "location": location,
            "description_url": apply_url,
            "description": job.get("description") or job.get("jobDescription", ""),
        })

    return jobs


def fetch_remoteok_jobs(max_age_hours):
    """
    RemoteOK has a genuine public JSON API at remoteok.com/api, no
    key, no login.
    """
    url = "https://remoteok.com/api"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [remoteok] request failed ({resp.status_code})")
        return []

    try:
        raw_jobs = resp.json()
    except ValueError:
        print("  [remoteok] response wasn't valid JSON, skipping")
        return []

    raw_jobs = [j for j in raw_jobs if j.get("id")]

    if raw_jobs:
        print(f"  [remoteok] sample record keys: {list(raw_jobs[0].keys())}")

    jobs = []
    for job in raw_jobs:
        title = job.get("position") or job.get("title") or ""
        company = job.get("company") or ""
        apply_url = job.get("url") or job.get("apply_url") or ""
        location = job.get("location") or ""
        posted_at = job.get("date") or None

        if posted_at:
            try:
                posted_at = datetime.fromisoformat(
                    str(posted_at).replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                posted_at = None

        if not _within_age_limit(posted_at, max_age_hours):
            continue
        if not title or not apply_url:
            continue

        jobs.append({
            "source": "remoteok",
            "company": company,
            "title": title,
            "url": apply_url,
            "posted_at": posted_at,
            "location": location,
            "description_url": apply_url,
            "description": job.get("description") or job.get("jobDescription", ""),
        })

    return jobs


def fetch_remotive_jobs(max_age_hours):
    """Remotive has a well-documented public API, no key required."""
    url = "https://remotive.com/api/remote-jobs"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [remotive] request failed ({resp.status_code})")
        return []

    try:
        data = resp.json()
    except ValueError:
        print("  [remotive] response wasn't valid JSON, skipping")
        return []

    raw_jobs = data.get("jobs", [])
    jobs = []
    for job in raw_jobs:
        title = job.get("title") or ""
        company = job.get("company_name") or ""
        apply_url = job.get("url") or ""
        location = job.get("candidate_required_location") or ""
        posted_at = job.get("publication_date") or None

        if posted_at:
            try:
                posted_at = datetime.fromisoformat(
                    str(posted_at).replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                posted_at = None

        if not _within_age_limit(posted_at, max_age_hours):
            continue
        if not title or not apply_url:
            continue

        jobs.append({
            "source": "remotive",
            "company": company,
            "title": title,
            "url": apply_url,
            "posted_at": posted_at,
            "location": location,
            "description_url": apply_url,
            "description": job.get("description") or job.get("jobDescription", ""),
        })

    return jobs


def fetch_jobicy_jobs(max_age_hours):
    """Jobicy has a well-documented public API, no key required."""
    url = "https://jobicy.com/api/v2/remote-jobs"
    resp = _get(url)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [jobicy] request failed ({resp.status_code})")
        return []

    try:
        data = resp.json()
    except ValueError:
        print("  [jobicy] response wasn't valid JSON, skipping")
        return []

    raw_jobs = data.get("jobs", [])
    jobs = []
    for job in raw_jobs:
        title = job.get("jobTitle") or ""
        company = job.get("companyName") or ""
        apply_url = job.get("url") or ""
        location = job.get("jobGeo") or ""
        posted_at = job.get("pubDate") or None

        if posted_at:
            try:
                posted_at = datetime.fromisoformat(
                    str(posted_at).replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                posted_at = None

        if not _within_age_limit(posted_at, max_age_hours):
            continue
        if not title or not apply_url:
            continue

        jobs.append({
            "source": "jobicy",
            "company": company,
            "title": title,
            "url": apply_url,
            "posted_at": posted_at,
            "location": location,
            "description_url": apply_url,
            "description": job.get("description") or job.get("jobDescription", ""),
        })

    return jobs


def fetch_adzuna_jobs(app_id, app_key, countries, target_titles, max_age_hours):
    """
    Adzuna aggregates real job board and employer listings, not a
    remote-only feed, and covers onsite roles. Free registration at
    developer.adzuna.com gives an app_id and app_key. Free tier caps
    at 1000 calls/month, so this makes one call per country using
    what_or (OR-matching on individual words) built from your target
    titles, rather than one call per title per country.
    """
    if not app_id or not app_key:
        print("  [adzuna] no ADZUNA_APP_ID/ADZUNA_APP_KEY set, skipping")
        return []

    # Results are capped at 50 newest per country, so filler words from
    # multi-word titles ("front end", "full stack") would crowd out real matches.
    filler = {"end", "front", "full", "stack", "application", "product", "associate", "1"}
    words = set()
    for title in target_titles:
        words.update(w for w in re.split(r"[\s-]+", title.lower()) if w and w not in filler)
    what_or = " ".join(sorted(words))

    max_days_old = max(1, max_age_hours // 24)

    jobs = []
    for country in countries:
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what_or": what_or,
            "max_days_old": max_days_old,
            "results_per_page": 50,
            "sort_by": "date",
        }
        resp = _get(url, params=params)
        if resp is None:
            continue
        if resp.status_code != 200:
            print(f"  [adzuna] {country}: request failed ({resp.status_code})")
            continue

        try:
            data = resp.json()
        except ValueError:
            print(f"  [adzuna] {country}: response wasn't valid JSON")
            continue

        results = data.get("results", [])
        print(f"  [adzuna] {country}: {len(results)} postings")

        for job in results:
            title = job.get("title") or ""
            company = (job.get("company") or {}).get("display_name", "")
            apply_url = job.get("redirect_url") or ""
            location = (job.get("location") or {}).get("display_name", "")
            posted_at = job.get("created") or None

            if posted_at:
                try:
                    posted_at = datetime.fromisoformat(
                        str(posted_at).replace("Z", "+00:00")
                    ).isoformat()
                except ValueError:
                    posted_at = None

            if not _within_age_limit(posted_at, max_age_hours):
                continue
            if not title or not apply_url:
                continue

            jobs.append({
                "source": f"adzuna-{country}",
                "company": company,
                "title": title,
                "url": apply_url,
                "posted_at": posted_at,
                "location": location,
                "description_quality": "snippet",
                "description_url": apply_url,
                "description": job.get("description") or job.get("jobDescription", ""),
            })

        time.sleep(0.5)

    return jobs


def fetch_all_jobs(config, excluded_urls=None, stats=None):
    all_jobs = []
    max_age = config.get("max_posting_age_hours", 48)

    print("Fetching from Greenhouse...")
    for slug in config.get("greenhouse_companies", []):
        all_jobs.extend(fetch_greenhouse_jobs(slug, max_age))
        time.sleep(0.5)

    print("Fetching from Lever...")
    for slug in config.get("lever_companies", []):
        all_jobs.extend(fetch_lever_jobs(slug, max_age))
        time.sleep(0.5)

    print("Fetching from Ashby...")
    for slug in config.get("ashby_companies", []):
        all_jobs.extend(fetch_ashby_jobs(slug, max_age))
        time.sleep(0.5)

    print("Fetching from SmartRecruiters...")
    for slug in config.get("smartrecruiters_companies", []):
        all_jobs.extend(fetch_smartrecruiters_jobs(slug, max_age))
        time.sleep(0.5)

    print("Fetching US employer boards...")
    for slug in config.get("smartrecruiters_us_companies", []):
        all_jobs.extend(fetch_smartrecruiters_jobs(slug, max_age, country="us"))
        time.sleep(0.5)

    print("Fetching from Recruitee...")
    for slug in config.get("recruitee_companies", []):
        all_jobs.extend(fetch_recruitee_jobs(slug, max_age))
        time.sleep(0.5)

    if config.get("use_simplify_new_grad_repo"):
        print("Fetching from New Grad Positions repo...")
        all_jobs.extend(
            fetch_simplify_new_grad_jobs(config["simplify_repo_readme_url"], max_age)
        )

    if config.get("use_working_nomads"):
        print("Fetching from Working Nomads...")
        all_jobs.extend(fetch_workingnomads_jobs(max_age))

    if config.get("use_remoteok"):
        print("Fetching from RemoteOK...")
        all_jobs.extend(fetch_remoteok_jobs(max_age))

    if config.get("use_remotive"):
        print("Fetching from Remotive...")
        all_jobs.extend(fetch_remotive_jobs(max_age))

    if config.get("use_jobicy"):
        print("Fetching from Jobicy...")
        all_jobs.extend(fetch_jobicy_jobs(max_age))

    from public_boards import fetch_json_board, fetch_weworkremotely_jobs
    for board in ("himalayas", "arbeitnow"):
        if config.get(f"use_{board}"):
            print(f"Fetching from {board}...")
            all_jobs.extend(fetch_json_board(board, max_age))
    if config.get("use_weworkremotely"):
        print("Fetching from We Work Remotely...")
        all_jobs.extend(fetch_weworkremotely_jobs(max_age))

    if config.get("use_adzuna"):
        print("Fetching from Adzuna...")
        all_jobs.extend(fetch_adzuna_jobs(
            os.environ.get("ADZUNA_APP_ID"),
            os.environ.get("ADZUNA_APP_KEY"),
            config.get("adzuna_countries", ["us", "gb", "sg"]),
            config.get("target_titles", []),
            max_age,
        ))

    titles = [t.lower() for t in config.get("target_titles", [])]
    from social_jobs import fetch_linkedin_jobs, fetch_x_jobs
    if config.get("use_x"):
        print("Fetching hiring posts from X...")
        all_jobs.extend(fetch_x_jobs(config, max_age))
    if config.get("use_linkedin_public"):
        print("Fetching public LinkedIn job cards...")
        all_jobs.extend(fetch_linkedin_jobs(config, max_age))
    if stats is not None:
        stats.counts["discovered_after_source_age_filters"] = len(all_jobs)
        stats.sources.update(job.get("source", "unknown") for job in all_jobs)
    all_jobs = [job for job in all_jobs if _within_age_limit(job.get("posted_at"), max_age)]
    before_titles = len(all_jobs)
    if titles:
        all_jobs = [
            j for j in all_jobs
            if any(t in j["title"].lower() for t in titles)
        ]

    if stats is not None:
        stats.counts["title_and_age_matched"] = len(all_jobs)
        stats.counts["title_rejected"] = before_titles - len(all_jobs)
    unique_jobs = []
    seen_urls = set()
    for job in all_jobs:
        url = job.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_jobs.append(job)

    if stats is not None:
        stats.counts["duplicate_or_missing_urls"] = len(all_jobs) - len(unique_jobs)
    before_cross_source = len(unique_jobs)
    unique_jobs = deduplicate_jobs(unique_jobs)
    if stats is not None:
        stats.counts["cross_source_or_tracking_duplicates"] = before_cross_source - len(unique_jobs)
    excluded_urls = {canonical_url(url) for url in (excluded_urls or set())}
    before = len(unique_jobs)
    unique_jobs = [job for job in unique_jobs if not
                   ({canonical_url(job["url"]), *(canonical_url(url) for url in job.get("alternate_urls", []))} & excluded_urls)]
    if stats is not None:
        stats.counts["already_logged"] = before - len(unique_jobs)
        stats.counts["unseen_candidates"] = len(unique_jobs)
    print(f"Found {before} unique matching postings; skipped {before - len(unique_jobs)} already logged.")

    # Spend the limited scoring budget on early-career roles first. Keep
    # senior roles eligible, but behind junior and unspecified-level roles.
    def priority(job):
        title = job["title"].lower()
        senior = bool(re.search(r"\b(senior|sr|staff|principal|lead|manager|director)\b", title))
        junior = bool(re.search(r"\b(junior|jr|graduate|new grad|early career|entry|university)\b", title))
        return (2 if senior else 0 if junior else 1)

    unique_jobs.sort(key=lambda job: job.get("posted_at") or "", reverse=True)
    unique_jobs.sort(key=priority)
    limit = config.get("max_jobs_per_run", 25)
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError) as error:
            raise ValueError("max_jobs_per_run must be a whole number or null") from error
        if limit < 1:
            raise ValueError("max_jobs_per_run must be at least 1 or null")
        unique_jobs = unique_jobs[:limit]

    print(f"Found {len(unique_jobs)} matching postings after filtering.")
    return unique_jobs
