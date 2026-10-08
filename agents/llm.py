"""Gemini client using only the Python standard library (works behind corporate proxies).

The LLM never sees the live stream. Agents call it only when an incident opens,
with a small JSON summary (alerts, a few log lines, lineage). Typical use is
2 calls per incident: root cause + final report.

Settings (in .env):
  GEMINI_API_KEY     your key from Google AI Studio
  GEMINI_MODEL       optional, default from config/settings.yaml (with fallbacks if retired)
  GEMINI_CA_BUNDLE   optional, path to your company's root certificate (Zscaler) if
                     Python reports CERTIFICATE_VERIFY_FAILED
  LLM_MODE=offline   skip Gemini entirely; agents use their built-in rules
HTTPS_PROXY / HTTP_PROXY are honoured automatically.
"""
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from common.io import log_event

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    def __init__(self, settings: dict, out_dir: Path):
        cfg = settings.get("llm", {})
        self.key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = os.environ.get("GEMINI_MODEL", cfg.get("model", "gemini-3.1-flash-lite"))
        self.fallbacks = [m for m in cfg.get("fallback_models", []) if m != self.model]
        self.timeout = cfg.get("timeout_seconds", 30)
        self.min_gap = cfg.get("min_seconds_between_calls", 4)
        self.offline = os.environ.get("LLM_MODE", "").lower() == "offline"
        self.out_dir = Path(out_dir)
        self.last_call = 0.0
        self.last_error = None
        self.calls = 0
        self._cache = {}
        ca = os.environ.get("GEMINI_CA_BUNDLE", "").strip()
        self.ssl_ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()

    @property
    def enabled(self) -> bool:
        return bool(self.key) and not self.offline

    @property
    def status(self) -> str:
        if self.offline:
            return "offline mode (rules only)"
        if not self.key:
            return "no GEMINI_API_KEY set (rules only)"
        if self.last_error:
            return f"Gemini error, using rules: {self.last_error}"
        return f"Gemini {self.model}"

    def generate_json(self, system: str, prompt: str):
        """Return a dict parsed from Gemini's JSON answer, or None if unavailable."""
        if not self.enabled:
            return None
        cache_key = hashlib.sha256((system + prompt).encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        wait = self.min_gap - (time.time() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            API.format(model=self.model), data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        self.last_call = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_ctx) as resp:
                data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            self.calls += 1
            self.last_error = None
            self._cache[cache_key] = result
            log_event(self.out_dir, "llm", "INFO", "Gemini call ok", model=self.model, calls=self.calls)
            return result
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            self.last_error = f"HTTP {e.code}"
            log_event(self.out_dir, "llm", "ERROR", f"Gemini HTTP {e.code}", model=self.model, detail=detail)
            if e.code == 404 and self.fallbacks:  # model retired: move to the next one and retry
                self.model = self.fallbacks.pop(0)
                log_event(self.out_dir, "llm", "WARN", f"Switching to model {self.model}")
                return self.generate_json(system, prompt)
        except ssl.SSLError as e:
            self.last_error = "SSL certificate problem (set GEMINI_CA_BUNDLE)"
            log_event(self.out_dir, "llm", "ERROR", "Gemini SSL error", detail=str(e))
        except urllib.error.URLError as e:
            reason = str(e.reason)
            self.last_error = ("SSL certificate problem (set GEMINI_CA_BUNDLE)"
                               if "CERTIFICATE" in reason.upper() else f"network: {reason[:80]}")
            log_event(self.out_dir, "llm", "ERROR", "Gemini network error", detail=reason)
        except (KeyError, IndexError, json.JSONDecodeError, TimeoutError) as e:
            self.last_error = "unexpected response"
            log_event(self.out_dir, "llm", "ERROR", "Gemini unexpected response", detail=str(e))
        return None


def check_connection(settings, out_dir) -> str:
    """Used by `python check_gemini.py` to test the key, proxy and certificates."""
    client = GeminiClient(settings, out_dir)
    if not client.enabled:
        return client.status
    out = client.generate_json("Reply with JSON only.", 'Return {"ok": true}')
    return f"OK: {client.model} answered {out}" if out else f"FAILED: {client.last_error} (see output/logs/llm.log)"
