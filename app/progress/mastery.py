"""Deterministic mastery rules. No AI. Numbers come from mastery_rules.yaml.

To change how mastery works later, edit the YAML (numbers) or this file (logic).
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path

import yaml

from app import config


@dataclass(frozen=True)
class MasteryRules:
    passes_required: int = 2
    weak_after_consecutive_failures: int = 3

    @classmethod
    def from_dict(cls, data: dict | None) -> "MasteryRules":
        data = data or {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown mastery rule(s): {sorted(unknown)}. Allowed: {sorted(known)}")
        return cls(**{k: int(v) for k, v in data.items()})

    def merged(self, overrides: dict | None) -> "MasteryRules":
        """Return a copy with per-concept overrides applied."""
        if not overrides:
            return self
        known = {f.name for f in fields(self)}
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(f"Unknown mastery rule(s): {sorted(unknown)}. Allowed: {sorted(known)}")
        return replace(self, **{k: int(v) for k, v in overrides.items()})


def load_rules(path: str | Path | None = None) -> MasteryRules:
    path = Path(path if path is not None else config.MASTERY_RULES_PATH)
    if not path.exists():
        return MasteryRules()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MasteryRules.from_dict(data.get("default"))


def required_passes(rules: MasteryRules, exercise_count: int) -> int:
    """Passes needed for a concept = configured number, but never more than the exercises that exist."""
    return max(1, min(rules.passes_required, exercise_count))


def mastery_percent(passes: int, required: int) -> float:
    if required <= 0:
        return 100.0
    return round(min(100.0, 100.0 * passes / required), 1)


def decide_status(*, passes: int, fails: int, required: int, lessons_done: bool,
                  consecutive_failures: int, rules: MasteryRules) -> str:
    """Concept status: not_started | in_progress | weak | mastered."""
    if lessons_done and passes >= required:
        return "mastered"
    if consecutive_failures >= rules.weak_after_consecutive_failures:
        return "weak"
    if passes == 0 and fails == 0:
        return "not_started"
    return "in_progress"
