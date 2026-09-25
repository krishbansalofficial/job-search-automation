"""Offline setup checks; never prints secret values or makes API requests."""
import argparse
import importlib.util
import os
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check_setup(local=False):
    checks = []
    config = yaml.safe_load((ROOT / "config/config.yml").read_text())
    checks.append(("Resume template", (ROOT / config["resume_template_path"]).is_file()))
    modules = ["playwright"] if local else ["requests", "bs4", "google.genai", "gspread"]
    for module in modules:
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            installed = False
        checks.append((f"Python dependency: {module}", installed))
    if local:
        checks.append(("Local application profile", (ROOT / "config/profile.yml").is_file()))
        checks.append(("GitHub CLI (for downloads)", shutil.which("gh") is not None))
        if checks[1][1]:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                checks.append(("Chromium browser installed", Path(playwright.chromium.executable_path).is_file()))
    else:
        checks.append(("Tectonic PDF compiler", shutil.which("tectonic") is not None))
        for name in ("GEMINI_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "GMAIL_APP_PASSWORD"):
            checks.append((f"Configured secret: {name}", bool(os.environ.get(name))))
    for label, passed in checks:
        print(f"{'OK' if passed else 'MISSING'}: {label}")
    return all(passed for _, passed in checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="Check local application review setup instead of scheduled pipeline")
    raise SystemExit(0 if check_setup(parser.parse_args().local) else 1)
