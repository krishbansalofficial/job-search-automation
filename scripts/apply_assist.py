"""
Opens a job's real application page in a visible browser, fills in
your contact info and uploads your resume, and stops before
submitting -- you review the form and click submit yourself.

This intentionally never auto-submits. Dropdowns, radio buttons and
autocomplete boxes (work authorization, sponsorship, EEO/diversity
self-identification, etc.) are only answered from the
`screening_answers` you write yourself in config/profile.yml; any
question without a matching answer is left blank and listed at the end
as still needing you. It never ticks checkboxes (consents, attestations).

Supports Greenhouse, Lever, and Ashby postings -- the three sources
whose application forms are stable enough to target reliably.
SmartRecruiters and Recruitee postings aren't wired up; their apply
flows didn't hold still enough in testing to trust.

Run it by hand, per posting, when you're ready to apply:

    python scripts/apply_assist.py <job-posting-url> [--resume path/to.pdf]

One-time setup:
    pip install playwright
    playwright install chromium
    cp config/profile.example.yml config/profile.yml   # then edit it
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

PROFILE_PATH = Path(__file__).parent.parent / "config" / "profile.yml"

# Associated label/aria-label text (regex) -> profile field. Checked
# in order against every plain text/email/tel input and textarea
# that isn't already handled by a source-specific selector, so
# per-posting custom questions like "LinkedIn URL" still get filled
# when the profile has an answer.
TEXT_FIELD_MATCHERS = [
    (re.compile(r"linkedin", re.I), "linkedin_url"),
    (re.compile(r"github", re.I), "github_url"),
    (re.compile(r"portfolio|personal site|personal website|website", re.I), "portfolio_url"),
    (re.compile(r"current or previous employer|current (company|employer)", re.I), "current_company"),
    (re.compile(r"current or previous job title|current (job )?title", re.I), "current_title"),
    (re.compile(r"first name", re.I), "first_name"),
    (re.compile(r"last name", re.I), "last_name"),
    (re.compile(r"^name$|full name|your name", re.I), "full_name"),
    (re.compile(r"phone", re.I), "phone"),
    (re.compile(r"email", re.I), "email"),
    (re.compile(r"address|^city$|current location", re.I), "address"),
    # Only ever matches when a posting asks this as a free-text box.
    # Most ATSes phrase work authorization / sponsorship as a radio
    # button or dropdown instead; those are answered only from the
    # profile's screening_answers (see _fill_screening).
    (re.compile(r"visa status|work authorization|sponsorship", re.I), "work_authorization_status"),
]


def application_url(job):
    """Best form URL for a job: Greenhouse's hosted form when the posting lives on a company site."""
    match = re.fullmatch(r"https://boards-api\.greenhouse\.io/v1/boards/([\w-]+)/jobs/(\d+)",
                         job.get("description_url", ""))
    if match and detect_ats(job.get("url", "")) is None:
        return f"https://job-boards.greenhouse.io/embed/job_app?for={match[1]}&token={match[2]}"
    return job.get("url", "")


def load_profile():
    if not PROFILE_PATH.exists():
        raise RuntimeError(
            "config/profile.yml not found. Copy config/profile.example.yml "
            "to config/profile.yml and fill in your own details."
        )
    return yaml.safe_load(PROFILE_PATH.read_text())


def detect_ats(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        return None
    host = parsed.hostname
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "greenhouse"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    return None


def _click_if_present(page, text):
    locator = page.get_by_text(text, exact=False).first
    try:
        if locator.count() and locator.is_visible():
            locator.click()
            page.wait_for_timeout(1500)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _fill_text(page, selector, value, filled, key):
    if not value:
        return
    try:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            locator.fill(str(value))
            filled.add(key)
    except Exception:  # noqa: BLE001
        pass


def _upload_resume(page, selector, resume_path):
    if not resume_path or not Path(resume_path).exists():
        return
    try:
        locator = page.locator(selector).first
        if locator.count():
            locator.set_input_files(str(resume_path))
            page.wait_for_timeout(800)  # let the "file selected" UI settle
    except Exception:  # noqa: BLE001
        pass


def _label_for(page, field):
    """The question text for a form control: aria-label, <label for>, or aria-labelledby."""
    text = field.get_attribute("aria-label") or ""
    field_id = field.get_attribute("id")
    if not text and field_id:
        label = page.locator(f'label[for="{field_id}"]')
        if label.count():
            text = label.first.inner_text()
    labelledby = field.get_attribute("aria-labelledby")
    if not text and labelledby:
        parts = [page.locator(f'[id="{part}"]') for part in labelledby.split()]
        text = " ".join(part.first.inner_text() for part in parts if part.count())
    return text.strip()


def match_answer(question, options, screening_answers):
    """Return the option text to pick for a question, or None to leave it for the user.

    Each screening answer is {question: regex, answer: regex}; the first rule whose
    question pattern matches wins, and it picks the first option its answer matches.
    """
    for rule in screening_answers or []:
        if not re.search(rule.get("question", "(?!)"), question, re.I):
            continue
        for option in options:
            if option.strip() and re.search(rule.get("answer", "(?!)"), option, re.I):
                return option
        return None
    return None


def _fill_screening(page, profile, answered):
    """Answer selects, radio groups and comboboxes from the profile's screening_answers."""
    rules = profile.get("screening_answers") or []
    if not rules:
        return
    selects = page.locator("select")
    for i in range(selects.count()):
        field = selects.nth(i)
        try:
            if not field.is_visible():
                continue
            question = _label_for(page, field)
            choice = match_answer(question, field.locator("option").all_inner_texts(), rules)
            if choice:
                field.select_option(label=choice)
                answered.append(question)
        except Exception:  # noqa: BLE001
            continue
    groups = page.locator("fieldset, [role=radiogroup]")
    for i in range(groups.count()):
        group = groups.nth(i)
        try:
            if not group.is_visible() or not group.locator("input[type=radio]").count():
                continue
            legend = group.locator("legend")
            question = legend.first.inner_text() if legend.count() else _label_for(page, group)
            labels = group.locator("label")
            choice = match_answer(question, labels.all_inner_texts(), rules)
            if choice:
                labels.filter(has_text=choice).first.click()
                answered.append(question)
        except Exception:  # noqa: BLE001
            continue
    boxes = page.locator("input[role=combobox]")
    for i in range(boxes.count()):
        field = boxes.nth(i)
        try:
            if not field.is_visible() or field.input_value():
                continue
            question = _label_for(page, field)
            rule = next((r for r in rules if re.search(r.get("question", "(?!)"), question, re.I)), None)
            if rule is None:
                continue
            field.click()
            if rule.get("type"):
                # Search-as-you-type boxes (school, city) only list options after typing.
                field.fill(str(rule["type"]))
                page.wait_for_timeout(1500)
            page.wait_for_timeout(400)
            # Scope to this box's own listbox: other pickers (e.g. phone country
            # codes) keep their options in the DOM too.
            listbox = field.get_attribute("aria-controls") or field.get_attribute("aria-owns")
            options = page.locator(f'[id="{listbox}"] [role=option]' if listbox else "[role=option]")
            choice = match_answer(question, options.all_inner_texts(), rules)
            if choice:
                options.filter(has_text=choice).first.click()
                answered.append(question)
            else:
                page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            continue


def _still_required(page, answered=()):
    """Labels of visible required fields that are still empty after filling.

    Comboboxes keep their chosen value outside the <input>, so questions this
    run answered are excluded; checkbox/radio groups report their legend once.
    """
    missing = []
    fields = page.locator("input[required], input[aria-required=true], select[required], "
                          "select[aria-required=true], textarea[required], textarea[aria-required=true]")
    for i in range(fields.count()):
        field = fields.nth(i)
        try:
            kind = field.get_attribute("type") or ""
            if kind in {"hidden", "file"} or not field.is_visible():
                continue
            if kind in {"radio", "checkbox"}:
                name = field.get_attribute("name")
                if name and page.locator(f'input[name="{name}"]:checked').count():
                    continue
            elif field.input_value():
                continue
            label = ""
            if kind in {"radio", "checkbox"}:
                label = field.evaluate("el => el.closest('fieldset')?.querySelector('legend')?.innerText || ''").strip()
            label = label or _label_for(page, field) or field.get_attribute("name") or "(unlabelled field)"
            if label not in missing and label not in answered:
                missing.append(label)
        except Exception:  # noqa: BLE001
            continue
    return missing


def _fill_by_label(page, profile, filled):
    """
    Best-effort pass over remaining plain text/email/tel inputs and
    textareas: match each one's associated label (or aria-label) text
    against known keywords and fill it if the profile has an answer.
    Deliberately only looks at these input types, never checkboxes,
    radio buttons, or selects.
    """
    fields = page.locator("input[type=text], input[type=email], input[type=tel], textarea")
    for i in range(fields.count()):
        field = fields.nth(i)
        try:
            if not field.is_visible():
                continue
            if field.get_attribute("role") == "combobox":
                continue
            label_text = _label_for(page, field)
            if not label_text:
                continue
            for pattern, profile_key in TEXT_FIELD_MATCHERS:
                if profile_key in filled:
                    continue
                if pattern.search(label_text) and profile.get(profile_key):
                    field.fill(str(profile[profile_key]))
                    filled.add(profile_key)
                    break
        except Exception:  # noqa: BLE001
            continue


def fill_greenhouse(page, profile, resume_path):
    _click_if_present(page, "Apply for this job")
    filled = set()
    _fill_text(page, "#first_name", profile.get("first_name"), filled, "first_name")
    _fill_text(page, "#last_name", profile.get("last_name"), filled, "last_name")
    _fill_text(page, "#email", profile.get("email"), filled, "email")
    _fill_text(page, "#phone", profile.get("phone"), filled, "phone")
    _upload_resume(page, "#resume", resume_path)
    _fill_by_label(page, profile, filled)
    return filled


def fill_lever(page, profile, resume_path):
    filled = set()
    _fill_text(page, "input[name=name]", profile.get("full_name"), filled, "full_name")
    _fill_text(page, "input[name=email]", profile.get("email"), filled, "email")
    _fill_text(page, "input[name=phone]", profile.get("phone"), filled, "phone")
    _fill_text(page, "input[name=org]", profile.get("current_company"), filled, "current_company")
    _fill_text(page, "input[name='urls[LinkedIn]']", profile.get("linkedin_url"), filled, "linkedin_url")
    _fill_text(page, "input[name='urls[GitHub]']", profile.get("github_url"), filled, "github_url")
    _fill_text(page, "input[name='urls[Portfolio]']", profile.get("portfolio_url"), filled, "portfolio_url")
    if profile.get("cover_letter_text"):
        _fill_text(page, "#additional-information", profile.get("cover_letter_text"), filled, "cover_letter_text")
    _upload_resume(page, "#resume-upload-input", resume_path)
    return filled


def fill_ashby(page, profile, resume_path):
    filled = set()
    _fill_text(page, "#_systemfield_name", profile.get("full_name"), filled, "full_name")
    _fill_text(page, "#_systemfield_email", profile.get("email"), filled, "email")
    _upload_resume(page, "#_systemfield_resume", resume_path)
    _fill_by_label(page, profile, filled)
    return filled


FORM_READY = {
    "greenhouse": "#email",
    "lever": "input[name=email]",
    "ashby": "#_systemfield_email",
}

FILLERS = {
    "greenhouse": fill_greenhouse,
    "lever": fill_lever,
    "ashby": fill_ashby,
}


def resolve_apply_url(ats, url):
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    suffix = {"lever": "/apply", "ashby": "/application"}.get(ats)
    if suffix and not path.endswith(suffix):
        path += suffix
    return urlunsplit(parsed._replace(path=path))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job_url", help="The job posting URL (Greenhouse, Lever, or Ashby)")
    parser.add_argument("--resume", help="Path to the tailored resume PDF to upload")
    parser.add_argument("--apply-url", help="Form URL when it differs from the posting (e.g. Greenhouse embed)")
    args = parser.parse_args()

    ats = detect_ats(args.apply_url or args.job_url)
    if ats is None:
        print(
            "Don't recognize this posting's ATS. Supported: Greenhouse "
            "(*.greenhouse.io), Lever (jobs.lever.co), Ashby (jobs.ashbyhq.com)."
        )
        sys.exit(1)

    try:
        profile = load_profile()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    resume_path = args.resume or profile.get("default_resume_path")
    if not resume_path or not Path(resume_path).exists():
        print(f"Note: no resume found at {resume_path!r}. Skipping resume upload.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright isn't installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    apply_url = resolve_apply_url(ats, args.apply_url or args.job_url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(apply_url, timeout=30000)
        # Some boards poll analytics forever, so "networkidle" is best-effort;
        # the real readiness signal is the form's email field appearing.
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.wait_for_selector(FORM_READY[ats], state="visible", timeout=20000)
        except Exception:  # noqa: BLE001
            print("Form fields did not appear in time; filling whatever is on the page.")

        filled = FILLERS[ats](page, profile, resume_path)
        answered = []
        _fill_screening(page, profile, answered)

        print(f"\nFilled: {sorted(filled) if filled else '(nothing matched -- check the form manually)'}")
        if answered:
            print("Answered from your screening_answers:\n  - " + "\n  - ".join(answered))
        missing = _still_required(page, answered)
        if missing:
            print("Still needs you (required, empty):\n  - " + "\n  - ".join(missing))
        print(
            "Review every answer in the browser, then click submit yourself.\n"
            "This waits until you close the browser window (up to 30 minutes)."
        )
        # Waits on the browser window itself rather than a terminal
        # keypress, since this may be launched from a context (an
        # IDE run button, a script, no attached TTY) where stdin
        # hits EOF immediately and would otherwise close the browser
        # before there's any chance to review it.
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            try:
                if page.is_closed():
                    break
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                break
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
