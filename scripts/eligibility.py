"""Conservative, local eligibility rules. Unknown requirements stay eligible."""
import re


def check_eligibility(job, description, config):
    rules = config.get("eligibility", {})
    reasons, review = [], []
    title = job.get("title", "")
    text = description or ""
    if rules.get("exclude_senior_titles", False) and re.search(
            r"\b(senior|sr\.?|staff|principal|director|manager|lead|head of|vp|vice president|chief)\b", title, re.I):
        reasons.append("senior_title")
    maximum = rules.get("max_required_experience_years")
    if maximum is not None:
        # Only explicit minimum requirements. Preferences, alternative education
        # routes and ambiguous experience statements are left for human review.
        for sentence in re.split(r"[\n.;]", text):
            if re.search(r"\b(preferred|ideally|or|not required)\b", sentence, re.I):
                continue
            match = re.search(r"(?:at least|minimum(?: of)?|requires?)\s+(\d+)\+?\s+years?\s+(?:of\s+)?(?:professional\s+|relevant\s+|industry\s+|work\s+)?experience", sentence, re.I)
            if match and int(match[1]) > maximum:
                reasons.append("required_experience")
                break
    if rules.get("requires_sponsorship"):
        if re.search(r"\b(?:no (?:visa |immigration )?sponsorship (?:is )?(?:available|provided|offered)|(?:cannot|unable to|will not|do not|does not) (?:provide |offer )?(?:visa |immigration )?sponsor(?:ship)?)\b", text, re.I):
            reasons.append("explicit_no_sponsorship")
        else:
            review.append("Confirm sponsorship availability")
    # Explicit location exclusions only; remote/ambiguous locations stay visible.
    location = job.get("location", "")
    excluded = rules.get("excluded_location_terms", [])
    if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", location, re.I) for term in excluded):
        reasons.append("excluded_location")
    year = rules.get("graduation_year")
    if year:
        # Only an unambiguous single-year requirement, not date windows.
        match = re.search(r"must (?:graduate|be graduating) in (20\d{2})(?!\s*(?:[-–/]|or|to|through))\b", text, re.I)
        if match and int(match[1]) != year:
            reasons.append("graduation_year")
    if re.search(r"citizen|clearance|graduat|authorized to work|right to work", text, re.I):
        review.append("Confirm graduation/work-authorization requirements")
    if not location:
        review.append("Confirm work location")
    if job.get("description_quality") == "title_only":
        review.append("Full description unavailable")
    return {"eligible": not reasons, "reasons": reasons, "review": review}
