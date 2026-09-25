"""
Uses the Gemini API (free tier is enough for this) to:
  1. Score how well the resume matches a job description (0 to 100)
  2. List missing ATS keywords
  3. Suggest reworded resume bullets that surface those keywords
     honestly, without inventing experience that isn't there

Set GEMINI_API_KEY as an environment variable (or GitHub secret).
Get a free key at https://aistudio.google.com

Every request attempt, including retries and fallback calls, consumes
the saved daily budget. A rate/quota error blocks that model until the
next Pacific day; scoring stops for the day once every model is blocked.
Failed and deferred jobs remain eligible for retry.

This intentionally does NOT rotate between multiple Google accounts
or API keys to dodge a daily cap. Google's terms of service prohibit
creating multiple accounts to circumvent usage limits, so that
approach isn't used here even though it's a common workaround
online.
"""

import json
import os
import time

# Models tried in order, each with its own free tier quota and load. Override
# with `gemini_models` in config; the "Gemini models" workflow lists valid names.
# gemini-2.5-* are listed for the key but return 404 "no longer available to new users".
DEFAULT_MODELS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.6-flash",
                  "gemini-3.1-flash-lite", "gemini-3.5-flash-lite")

# Tracks which models have hit their daily limit during this run, so
# later jobs don't waste calls retrying an exhausted model.
_quota_exhausted_models = set()

# Models that answered 503/overloaded this run. Unlike quota errors this
# doesn't block the saved daily budget; the next run tries again.
_overloaded_models = set()

SCORE_PROMPT = """You are helping a student tailor their
resume honestly for a specific job posting. Do not invent skills,
tools, or experience that are not already present in the resume text.

Resume:
---
{resume_text}
---

Job posting ({company} - {title}):
---
{job_description}
---

Respond with ONLY valid JSON in this exact shape, no markdown fences:
{{
  "match_score": <integer 0-100>,
  "missing_keywords": [<up to 8 short strings>],
  "reasoning": "<one or two sentences on the score>",
  "bullet_suggestions": [
    {{
      "original": "<an existing resume bullet that's a close fit>",
      "revised": "<the same bullet reworded to surface missing keywords, using only skills already evidenced in the resume>"
    }}
  ]
}}
"""


def _is_quota_error(error):
    text = str(error).lower()
    return "429" in text or "resource_exhausted" in text or "quota" in text


def _is_unavailable_model_error(error):
    text = str(error).lower()
    return "404" in text or "not_found" in text or "no longer available" in text


def _is_overload_error(error):
    text = str(error).lower()
    return "503" in text or "unavailable" in text or "overloaded" in text or "high demand" in text


def _configure():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at "
            "https://aistudio.google.com and set it as an environment "
            "variable or GitHub Actions secret."
        )
    try:
        from google import genai
    except ImportError as error:
        raise RuntimeError(
            "google-genai is not installed. Run pip install -r requirements.txt."
        ) from error
    # SDK-level retries would bypass our attempt ledger.
    return genai.Client(api_key=api_key, http_options={"retry_options": {"attempts": 1}})


def score_and_tailor(job, resume_text, job_description_text, budget=None, models=DEFAULT_MODELS):
    if budget is None:
        from gemini_budget import GeminiBudget
        budget = GeminiBudget({})
    try:
        client = _configure()
    except RuntimeError as error:
        print(f"  scoring skipped for {job.get('company')}: {error}")
        return {
            "status": "failed",
            "match_score": 0,
            "missing_keywords": [],
            "reasoning": "Scoring skipped because GEMINI_API_KEY is not configured.",
            "bullet_suggestions": [],
        }

    prompt = SCORE_PROMPT.format(
        resume_text=resume_text,
        company=job.get("company", ""),
        title=job.get("title", ""),
        job_description=(job_description_text or job.get("title", ""))[:16000],
    )

    models_to_try = [
        m for m in models
        if m not in _quota_exhausted_models and m not in _overloaded_models
        and not budget.model_blocked(m)
    ]

    if not models_to_try:
        return {
            "status": "deferred",
            "reason": "overloaded" if _overloaded_models else "quota",
            "match_score": 0,
            "missing_keywords": [],
            "reasoning": (
                "Deferred: Gemini models were rate limited or overloaded "
                "earlier in this run. Will retry on the next scheduled run."
            ),
            "bullet_suggestions": [],
        }

    for model_name in models_to_try:
        for attempt in range(2):
            try:
                if not budget.reserve():
                    return {"status": "deferred", "reason": "budget"}
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"automatic_function_calling": {"disable": True}},
                )
                text = response.text.strip().strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
                result = json.loads(text)
                if not isinstance(result, dict) or not isinstance(result.get("match_score"), (int, float)) or not 0 <= result["match_score"] <= 100:
                    raise ValueError("Invalid scoring response")
                return result
            except json.JSONDecodeError as e:
                print(f"  {model_name} returned bad JSON for {job.get('company')}: {e}")
                time.sleep(2 ** attempt)
            except Exception as e:  # noqa: BLE001
                if _is_quota_error(e):
                    print(f"  {model_name} returned a quota/rate limit; blocking it until the next Pacific day.")
                    _quota_exhausted_models.add(model_name)
                    budget.block_model(model_name)
                    break
                if _is_unavailable_model_error(e):
                    # Retrying a retired/unknown model can never succeed; skip it for the day.
                    print(f"  {model_name} is not available to this key (404); skipping it today.")
                    _quota_exhausted_models.add(model_name)
                    budget.block_model(model_name)
                    break
                if _is_overload_error(e):
                    # Retrying a busy model only burns budget; try the fallback.
                    print(f"  {model_name} is overloaded (503); skipping it for this run.")
                    _overloaded_models.add(model_name)
                    break
                print(f"  attempt {attempt + 1} failed for {job.get('company')} on {model_name}: {e}")
                time.sleep(2 ** attempt)

    if all(m in _overloaded_models or m in _quota_exhausted_models for m in models_to_try):
        if not any(m in _overloaded_models for m in models):
            budget.block()
        return {
            "status": "deferred",
            "reason": "overloaded" if any(m in _overloaded_models for m in models_to_try) else "quota",
            "match_score": 0,
            "missing_keywords": [],
            "reasoning": "Deferred: every Gemini model is overloaded or out of quota. Will retry on a later run.",
            "bullet_suggestions": [],
        }

    return {
        "status": "failed",
        "match_score": 0,
        "missing_keywords": [],
        "reasoning": "Scoring failed after trying all available models.",
        "bullet_suggestions": [],
    }


# Preserve the existing import interface for callers.
from job_descriptions import fetch_job_description_text
