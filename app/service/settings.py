from __future__ import annotations

from dataclasses import dataclass

from app import config


@dataclass(frozen=True)
class ServiceSettings:
    timezone: str = config.APP_TIMEZONE
    default_send_time: str = config.DEFAULT_SEND_TIME
    max_lessons_per_day: int = config.MAX_LESSONS_PER_DAY
    inactive_alert_days: int = config.INACTIVE_ALERT_DAYS
    rate_limit_per_minute: int = config.RATE_LIMIT_PER_MINUTE
    join_code_ttl_hours: int = config.JOIN_CODE_TTL_HOURS
    daily_max_failures: int = config.DAILY_MAX_FAILURES
    auto_hint_after_attempts: int = config.AUTO_HINT_AFTER_ATTEMPTS
