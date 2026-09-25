"""
Cross-references companies found by the job pipeline against USCIS's
official H-1B Employer Data Hub, so your tracking sheet shows which
companies have a history of sponsoring H-1B visas.

This reads a CSV file downloaded by hand, not fetched live, for two
reasons:
  1. USCIS's website blocks automated requests (bot detection), so a
     script trying to download it directly fails anyway.
  2. This data only updates quarterly, so refetching it on a daily
     schedule would be pointless even if it worked.

Setup (repeat every few months when USCIS releases new data):
  1. Go to https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
  2. Follow the link to the H-1B Employer Data Hub Files page
  3. Download the CSV for the most recent fiscal year
  4. Save it as data/h1b_employer_data.csv in this repo, replacing
     the previous file if one exists
  5. Commit and push

Matching is approximate. USCIS lists legal entity names ("Google
LLC"), which won't always match how a job posting names the company
("Google"). Treat a match as a lead worth checking, not proof, and
treat no match as "not found in this dataset", not "doesn't sponsor".
"""

import csv
import re
from pathlib import Path

_cache = None


def _normalize(name):
    name = name.lower().strip()
    name = re.sub(r"\b(llc|inc|corp|corporation|co|ltd|company)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    return name


def _load(csv_path):
    global _cache
    if _cache is not None:
        return _cache

    path = Path(csv_path)
    if not path.exists():
        print(f"  [h1b-lookup] {csv_path} not found, skipping sponsor lookup")
        _cache = {}
        return _cache

    lookup = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            employer = (
                row.get("Employer") or row.get("Employer Name")
                or row.get("Petitioner Business Name") or ""
            )
            if not employer:
                continue
            key = _normalize(employer)
            if not key:
                continue

            approvals = row.get("Initial Approval") or row.get("Initial Approvals") or "0"
            denials = row.get("Initial Denial") or row.get("Initial Denials") or "0"
            existing = lookup.get(key, {"approvals": 0, "denials": 0})
            try:
                existing["approvals"] += int(str(approvals).replace(",", "") or 0)
                existing["denials"] += int(str(denials).replace(",", "") or 0)
            except ValueError:
                pass
            lookup[key] = existing

    print(f"  [h1b-lookup] loaded {len(lookup)} employers from {csv_path}")
    _cache = lookup
    return _cache


def get_sponsor_history(company_name, csv_path="data/h1b_employer_data.csv"):
    """
    Returns a short string like "12 approved, 1 denied" if the
    company matches an entry in the USCIS data, or an empty string
    if not found or the data file isn't present.
    """
    lookup = _load(csv_path)
    if not lookup:
        return ""

    key = _normalize(company_name)
    if not key:
        return ""

    if key in lookup:
        stats = lookup[key]
        return f"{stats['approvals']} approved, {stats['denials']} denied"

    for known_key, stats in lookup.items():
        if key and (key in known_key or known_key in key):
            return f"{stats['approvals']} approved, {stats['denials']} denied (approx match)"

    return ""