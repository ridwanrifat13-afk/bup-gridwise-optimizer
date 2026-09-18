import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional in production
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")
GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))  # -1 = model default
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))     # per call
LLM_TOTAL_BUDGET_SECONDS = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "22"))  # all retries (judge timeout is 30s)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
