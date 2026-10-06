"""Central settings. Everything has a safe default; override via environment or a .env file."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader (no extra dependency). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _path(env_name: str, default: str) -> Path:
    p = Path(os.environ.get(env_name, default))
    return p if p.is_absolute() else BASE_DIR / p


DB_PATH = _path("LEARNING_DB_PATH", "data/learning.db")
CURRICULUM_DIR = _path("CURRICULUM_DIR", "app/curriculum/content")
MASTERY_RULES_PATH = _path("MASTERY_RULES_PATH", "app/curriculum/mastery_rules.yaml")

SANDBOX_TIMEOUT_SECONDS = float(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "5"))
SANDBOX_MAX_OUTPUT_BYTES = int(os.environ.get("SANDBOX_MAX_OUTPUT_BYTES", "20000"))
SANDBOX_ALLOWED_MODULES = [
    m.strip()
    for m in os.environ.get(
        "SANDBOX_ALLOWED_MODULES",
        "math,random,datetime,time,string,statistics,collections,itertools,"
        "functools,json,re,decimal,fractions,typing,operator,textwrap,calendar",
    ).split(",")
    if m.strip()
]


# ---------------------------------------------------------------- n8n + API settings
def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


API_KEY = os.environ.get("API_KEY", "")
API_HOST = os.environ.get("API_HOST", "127.0.0.1")   # keep 127.0.0.1: local only
API_PORT = _int("API_PORT", 8000)
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
DEFAULT_SEND_TIME = os.environ.get("DEFAULT_SEND_TIME", "08:00")
MAX_LESSONS_PER_DAY = _int("MAX_LESSONS_PER_DAY", 2)
INACTIVE_ALERT_DAYS = _int("INACTIVE_ALERT_DAYS", 3)
RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 10)
JOIN_CODE_TTL_HOURS = _int("JOIN_CODE_TTL_HOURS", 72)
DAILY_MAX_FAILURES = _int("DAILY_MAX_FAILURES", 3)
AUTO_HINT_AFTER_ATTEMPTS = _int("AUTO_HINT_AFTER_ATTEMPTS", 2)
BACKUP_DIR = _path("BACKUP_DIR", "data/backups")
BACKUP_KEEP = _int("BACKUP_KEEP", 7)
