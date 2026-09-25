"""Use feed descriptions first; fetch supported public ATS details if needed."""
import html
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def clean_text(value):
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True)).strip()


def lever_description(data):
    sections = [data.get("descriptionPlain") or data.get("description", "")]
    sections.extend(f"{item.get('text', '')}\n{item.get('content', '')}"
                    for item in data.get("lists", []))
    sections.append(data.get("additionalPlain") or data.get("additional", ""))
    return "\n".join(filter(None, sections))


def detail_endpoint(job):
    parsed = urlparse(job.get("description_url") or job.get("url", ""))
    host, path = parsed.hostname, parsed.path
    if parsed.scheme != "https":
        return None
    if host in {"boards-api.greenhouse.io", "api.lever.co", "api.smartrecruiters.com"}:
        return parsed._replace(query="", fragment="").geturl()
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.fullmatch(r"/([^/]+)/jobs/(\d+)/?", path)
        if match:
            return f"https://boards-api.greenhouse.io/v1/boards/{match[1]}/jobs/{match[2]}"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        parts = path.strip("/").split("/")
        if len(parts) == 2:
            api = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
            return f"https://{api}/v0/postings/{parts[0]}/{parts[1]}"
    return None


def fetch_job_description_text(job):
    text = clean_text(job.get("description"))
    if text:
        job["description_quality"] = job.get("description_quality", "feed")
        return text
    endpoint = detail_endpoint(job)
    if endpoint:
        try:
            response = requests.get(endpoint, timeout=15, allow_redirects=False,
                                    headers={"User-Agent": "job-search-automation/1.0"})
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    if "lever.co" in urlparse(endpoint).hostname:
                        content = lever_description(data)
                    elif "smartrecruiters.com" in endpoint:
                        sections = data.get("jobAd", {}).get("sections", {})
                        content = "\n".join(s.get("text", "") for s in sections.values() if isinstance(s, dict))
                    else:
                        content = data.get("content", "")
                    text = clean_text(content)
                    if text:
                        job["description"] = text
                        job["description_quality"] = "detail"
                        return text
        except (requests.RequestException, ValueError, TypeError):
            pass
    job["description_quality"] = "title_only"
    return job.get("title", "")
