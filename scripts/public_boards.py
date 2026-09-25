"""Additional public feeds, bounded to avoid unbounded discovery runs."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


def _date(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            stamp = float(value)
            return datetime.fromtimestamp(
                stamp / 1000 if stamp > 100_000_000_000 else stamp,
                tz=timezone.utc,
            ).isoformat()
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            date = parsedate_to_datetime(str(value))
        return date.replace(tzinfo=date.tzinfo or timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _job(source, title, company, url, date, location, description, max_age):
    from fetch_jobs import _within_age_limit

    if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
        return None
    if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        return None
    posted = _date(date)
    if not _within_age_limit(posted, max_age):
        return None
    return dict(source=source, title=title.strip(), company=company or "",
                url=url, posted_at=posted, location=location or "",
                description_url=url, description=description or "")


def fetch_json_board(source, max_age_hours, max_pages=5):
    """Fetch up to five pages; stop on failures, empty pages or repeated cursors."""
    from fetch_jobs import _get

    urls = {"himalayas": "https://himalayas.app/jobs/api",
            "arbeitnow": "https://www.arbeitnow.com/api/job-board-api"}
    jobs, cursors = [], set()
    cursor = None
    for page in range(1, max_pages + 1):
        params = {"limit": 20} if source == "himalayas" else {"page": page}
        if cursor:
            params["cursor"] = cursor
        response = _get(urls[source], params=params)
        if response is None or response.status_code != 200:
            print(f"  [{source}] request failed; stopping source")
            break
        try:
            data = response.json()
        except ValueError:
            print(f"  [{source}] invalid JSON; stopping source")
            break
        if not isinstance(data, dict):
            break
        records = data.get("jobs" if source == "himalayas" else "data")
        if not isinstance(records, list) or not records:
            break
        for record in records:
            if not isinstance(record, dict):
                continue
            if source == "himalayas":
                expiry = _date(record.get("expiryDate"))
                if expiry and datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
                    continue
                restrictions = record.get("locationRestrictions") or []
                location = ", ".join(
                    str(item.get("name") or item.get("alpha2") or "")
                    if isinstance(item, dict) else str(item) for item in restrictions
                ) or "Worldwide"
                job = _job(source, record.get("title"), record.get("companyName"),
                           record.get("applicationLink"), record.get("pubDate"),
                           location, record.get("description"), max_age_hours)
            else:
                job = _job(source, record.get("title"), record.get("company_name"),
                           record.get("url"), record.get("created_at"),
                           record.get("location"), record.get("description"), max_age_hours)
            if job:
                jobs.append(job)
        if source == "himalayas":
            cursor = data.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                break
            cursors.add(cursor)
        elif not (data.get("links") or {}).get("next"):
            break
    return jobs


def fetch_weworkremotely_jobs(max_age_hours):
    from fetch_jobs import _get

    response = _get("https://weworkremotely.com/remote-jobs.rss")
    if response is None or response.status_code != 200:
        print("  [weworkremotely] request failed; stopping source")
        return []
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        print("  [weworkremotely] invalid XML; stopping source")
        return []
    jobs = []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        company, separator, role = title.partition(": ")
        # WWR titles use "Company: Role"; region is optional in the feed.
        region = next((child.text for child in item
                       if child.tag.split("}")[-1] == "region"), "")
        job = _job("weworkremotely", role if separator else title,
                   company if separator else "", item.findtext("link"),
                   item.findtext("pubDate"), region,
                   item.findtext("description"), max_age_hours)
        if job:
            jobs.append(job)
    return jobs
