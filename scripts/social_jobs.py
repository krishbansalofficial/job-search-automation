"""Optional social discovery: X API and LinkedIn's public guest job cards.

No login cookies, proxies, CAPTCHA handling, retries, or Gemini calls.
LinkedIn's guest interface is unsupported and may stop working or block access.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup


def _request(url, **kwargs):
    try:
        response = requests.get(url, timeout=20, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            print(f"  [social] HTTP {response.status_code}; stopping this source.")
            return None
        return response
    except requests.RequestException:
        # Do not print request details: X credentials are in request headers.
        print("  [social] request failed; stopping this source.")
        return None


def fetch_x_jobs(config, max_age_hours):
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        print("  [x] X_BEARER_TOKEN missing; skipping.")
        return []
    query = config.get("x_search_query", '').strip()
    if not query:
        print("  [x] x_search_query missing; skipping.")
        return []
    # One page only. Recent search supports at most the last seven days.
    hours = min(max(float(max_age_hours), 1), 167)
    response = _request(
        "https://api.x.com/2/tweets/search/recent",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": query, "max_results": 10,
                "start_time": (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tweet.fields": "created_at,author_id,entities",
                "expansions": "author_id", "user.fields": "username,name"},
    )
    if response is None:
        return []
    try:
        data = response.json()
    except ValueError:
        print("  [x] invalid JSON; skipping.")
        return []
    if not isinstance(data, dict):
        return []
    jobs = []
    for post in data.get("data", []):
        text = post.get("text", "")
        if not post.get("id") or not text:
            continue
        # Preserve the post link: a social post is a lead, not a verified job.
        url = f"https://x.com/i/web/status/{post['id']}"
        jobs.append({"source": "x", "company": "Unknown employer (X lead)",
                     "title": text, "url": url, "description_url": url,
                     "description": text, "posted_at": post.get("created_at"),
                     "location": "Not specified"})
    return jobs


def parse_linkedin_jobs(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select(".base-search-card"):
        title = card.select_one(".base-search-card__title")
        company = card.select_one(".base-search-card__subtitle")
        link = card.select_one("a.base-card__full-link")
        location = card.select_one(".job-search-card__location")
        posted = card.select_one("time[datetime]")
        if title is None or link is None:
            continue
        # Canonical numeric IDs remove tracking parameters and title slugs.
        match = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)(?:[/?]|$)", link.get("href", ""))
        if not match:
            continue
        url = f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
        jobs.append({"source": "linkedin", "title": title.get_text(" ", strip=True),
                     "company": company.get_text(" ", strip=True) if company else "Unknown employer",
                     "url": url, "description_url": url,
                     "location": location.get_text(" ", strip=True) if location else "",
                     "posted_at": posted.get("datetime") if posted else None})
    return jobs


def fetch_linkedin_jobs(config, max_age_hours):
    jobs = []
    # At most two configured searches, one page each, no pagination/detail fetches.
    for query in config.get("linkedin_search_queries", [])[:2]:
        response = _request(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            headers={"User-Agent": "job-search-automation/1.0 (personal use)"},
            params={"keywords": query, "location": config.get("linkedin_location", "United States"),
                    "f_TPR": f"r{max(3600, int(max_age_hours * 3600))}",
                    "f_E": "1,2", "sortBy": "DD", "start": 0},
        )
        if response is None:
            break
        batch = parse_linkedin_jobs(response.text)
        if not batch:
            print("  [linkedin] no public job cards (empty results, access restriction, or changed markup); stopping.")
            break
        jobs.extend(batch)
    return jobs
