import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional in production
    pass


def _env(name: str, default: str) -> str:
    # dashboard-pasted values can carry a trailing newline, which breaks model URLs
    return (os.getenv(name) or default).strip() or default


OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
OPENROUTER_FALLBACK_MODEL = _env("OPENROUTER_FALLBACK_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
OPENROUTER_REASONING_EFFORT = _env("OPENROUTER_REASONING_EFFORT", "none")
FORCE_FALLBACK = _env("FORCE_FALLBACK", "0").lower() in ("1", "true", "yes")

# Keep legacy names for backward compatibility with existing tests
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = _env("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))  # per call
LLM_TOTAL_BUDGET_SECONDS = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "25"))  # all retries (well below 60s Vercel timeout)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
