"""Reserve every API attempt before sending it, including failed attempts.

One pipeline process at a time must use this ledger. Actions serializes runs
and restores/saves the file; local runs retain it on disk.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class GeminiBudget:
    def __init__(self, config):
        self.path = Path(config.get("gemini_usage_path", ".state/gemini-usage.json"))
        self.daily_limit = int(config.get("gemini_max_requests_per_day", 20))
        self.run_limit = int(config.get("gemini_max_requests_per_run", 20))
        self.delay = float(config.get("gemini_request_delay_seconds", 15))
        if min(self.daily_limit, self.run_limit) < 0 or self.delay < 0:
            raise ValueError("Gemini budgets and request delay must be nonnegative")
        self.used = 0
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _today(self):
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()

    def reserve(self):
        wait = self.delay - (time.time() - self.state.get("last_request", 0))
        if wait > 0:
            time.sleep(wait)
        today = self._today()
        if self.state.get("date") != today:
            self.state = {"date": today, "requests": 0}
        if self.used >= self.run_limit or self.state["requests"] >= self.daily_limit or self.state.get("blocked"):
            return False
        self.used += 1
        self.state["requests"] += 1
        self.state["last_request"] = time.time()
        self._save()
        return True

    def block(self):
        # Stop on any 429, even when it might be a temporary RPM/TPM limit.
        self.state["blocked"] = True
        self._save()

    def model_blocked(self, model):
        return self.state.get("date") == self._today() and model in self.state.get("blocked_models", [])

    def block_model(self, model):
        # A 429 from one model leaves the others usable; the date reset clears this.
        if self.state.get("date") != self._today():
            self.state = {"date": self._today(), "requests": 0}
        self.state["blocked_models"] = sorted(set(self.state.get("blocked_models", [])) | {model})
        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)
