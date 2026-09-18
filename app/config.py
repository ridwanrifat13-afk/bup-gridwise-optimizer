import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional in production
    pass

def _env(name: str, default: str) -> str:
    # dashboard-pasted values can carry a trailing newline, which breaks model URLs
    return (os.getenv(name) or default).strip() or default


GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
# Gem-series names change often; these aliases were verified available on the
# linked Google AI Studio account (Sep 2026). legacy 2.5-* are gated to new users.
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = _env("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))  # -1 = model default
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))     # per call
LLM_TOTAL_BUDGET_SECONDS = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "22"))  # all retries (judge timeout is 30s)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
