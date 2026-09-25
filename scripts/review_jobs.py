"""Download the latest application pack and prepare forms one at a time.

No submission or outreach is automated. Application status is recorded only
from the user's answer after the browser closes.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from apply_assist import detect_ats
from job_queue import canonical_url

ROOT = Path(__file__).resolve().parents[1]


def github_environment():
    env = dict(os.environ)
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, env=env)
    if auth.returncode == 0:
        return env
    # Reuse the configured Git credential helper, keeping credentials in memory.
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            capture_output=True, text=True, env=dict(env, GIT_TERMINAL_PROMPT="0"))
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise RuntimeError("GitHub authentication needed. Run: gh auth login")
    env["GH_TOKEN"] = fields["password"]
    return env


def download_latest(repo, output_dir):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Repository must be owner/name")
    env = github_environment()
    result = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts?name=tailored-resumes&per_page=100"],
                            capture_output=True, text=True, env=env, check=True)
    artifacts = [a for a in json.loads(result.stdout)["artifacts"]
                 if not a["expired"] and a.get("workflow_run", {}).get("head_branch") == "main"]
    if not artifacts:
        raise RuntimeError("No application artifact is available yet. Run the daily workflow first.")
    latest = max(artifacts, key=lambda a: a["id"])
    destination = Path(output_dir) / str(latest["id"])
    manifest = destination / "application-manifest.json"
    # gh refuses to overwrite files, so an earlier download of this artifact is reused as-is.
    if not destination.exists():
        subprocess.run(["gh", "run", "download", str(latest["workflow_run"]["id"]), "--repo", repo,
                        "--name", "tailored-resumes", "--dir", str(destination)], env=env, check=True)
    if not manifest.exists():
        raise RuntimeError("Latest artifact predates application packs. Run the updated pipeline first.")
    return manifest


def resume_for(job, manifest):
    name = job.get("resume")
    if not name:
        return None
    base = Path(manifest).resolve().parent
    candidate = (base / name).resolve()
    if candidate.parent != base or candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        raise ValueError("Application pack contains a missing or invalid resume path")
    return candidate


def review(manifest, state_path, limit=5, list_only=False, input_fn=input, runner=subprocess.run, only="all"):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("jobs"), list):
        raise ValueError("Unsupported application manifest")
    state_path = Path(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    selected = [job for job in data["jobs"] if state.get(canonical_url(job["url"]), {}).get("status") not in {"applied", "skipped"}
                and only in {"all", job.get("kind", "match")}]
    prepared = 0
    for job in selected:
        if prepared >= limit:
            break
        score = f"{job['score']}/100" if job.get("score") is not None else "not scored"
        print(f"{job['company']} — {job['title']} ({score})\n  {job['url']}")
        if job.get("review"):
            print("  Review: " + "; ".join(job["review"]))
        if list_only:
            prepared += 1
            continue
        apply_url = job.get("apply_url") or job["url"]
        if not detect_ats(apply_url):
            print("  Open this posting manually; form preparation is unsupported.")
            continue
        resume = resume_for(job, manifest)
        if resume is None:
            print("  No tailored PDF; review this posting manually.")
            continue
        prepared += 1
        result = runner([sys.executable, str(ROOT / "scripts/apply_assist.py"), job["url"],
                         "--apply-url", apply_url, "--resume", str(resume)])
        if result.returncode != 0:
            print("  Form preparation failed; leaving this job pending.")
            continue
        answer = input_fn("After reviewing/submitting: [a]pplied, [s]kip, [Enter] later, [q]uit: ").strip().lower()
        if answer == "q":
            break
        if answer not in {"a", "s"}:
            continue
        state[canonical_url(job["url"])] = {"status": "applied" if answer == "a" else "skipped",
                                            "updated_at": datetime.now(timezone.utc).isoformat(),
                                            "company": job["company"], "title": job["title"]}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temp.replace(state_path)
    print(f"Reviewed/listed {prepared} applications. Status is stored locally in {state_path}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", action="store_true", help="Download latest main-branch application pack using GitHub CLI")
    parser.add_argument("--repo", help="owner/name of your pipeline repo (default: this checkout's GitHub remote)")
    parser.add_argument("--manifest", default=str(ROOT / "output/application-manifest.json"))
    parser.add_argument("--state", default=str(ROOT / ".state/application-status.json"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--list", action="store_true", help="List pending applications without opening browsers")
    parser.add_argument("--only", choices=["all", "match", "posting"], default="all",
                        help="match: scored matches with tailored PDFs; posting: other eligible postings")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    try:
        repo = args.repo or subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                                           capture_output=True, text=True, cwd=ROOT).stdout.strip()
        manifest = download_latest(repo, ROOT / "output/downloads") if args.latest else Path(args.manifest)
        review(manifest, args.state, args.limit, args.list, only=args.only)
    except RuntimeError as error:
        # Only raised with this script's own fixed messages, which are safe to show.
        print(f"Could not prepare applications: {error}")
        sys.exit(1)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # Avoid printing subprocess environment or authenticated API response bodies.
        print(f"Could not prepare applications: {type(error).__name__}. Check GitHub login, the manifest, and local dependencies.")
        sys.exit(1)


if __name__ == "__main__":
    main()
