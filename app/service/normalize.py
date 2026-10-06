"""Clean up text that arrives from a phone keyboard / Telegram before it is graded.

Phones silently replace straight quotes " ' with curly quotes, and chat apps add
Markdown fences or odd spaces. For a beginner that is the #1 reason correct code fails.
"""
from __future__ import annotations

import re
import textwrap

_QUOTES = {
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
}
_ODD_SPACES = dict.fromkeys(
    map(ord, "               　"), " ")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)

_FENCE = re.compile(r"```(?:[ \t]*(?:python3?|py)[ \t]*\r?\n)?(.*?)```", re.DOTALL | re.IGNORECASE)
_OPEN_FENCE = re.compile(r"^```(?:[ \t]*(?:python3?|py)[ \t]*\r?\n)?", re.IGNORECASE)
_CHOICE = re.compile(r"^\(?\s*([A-Za-z])\s*[\).:\-]?(\s|$)")


def clean_telegram_code(text: str) -> tuple[str, list[str]]:
    """Return (cleaned_code, notes). notes may contain: code_fence, curly_quotes, odd_spaces."""
    notes: list[str] = []
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.translate(_ZERO_WIDTH)

    stripped = s.strip()
    m = _FENCE.search(stripped)
    if m:
        s = m.group(1)
        notes.append("code_fence")
    elif stripped.startswith("```"):
        s = _OPEN_FENCE.sub("", stripped, count=1)
        notes.append("code_fence")
    elif len(stripped) > 2 and stripped.startswith("`") and stripped.endswith("`") and stripped.count("`") == 2:
        s = stripped[1:-1]
        notes.append("code_fence")

    if any(ch in s for ch in _QUOTES):
        s = "".join(_QUOTES.get(ch, ch) for ch in s)
        notes.append("curly_quotes")
    if s.translate(_ODD_SPACES) != s:
        s = s.translate(_ODD_SPACES)
        notes.append("odd_spaces")
    s = s.replace("…", "...")                       # phone "..." autocorrect

    s = textwrap.dedent(s.strip("\n"))
    return s.rstrip(), notes


_ADDR = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_QUOTE_HEADER = re.compile(r"\n[ \t]*On\s[^\n]{0,200}(?:\n[^\n]{0,200})?wrote:[ \t]*(?:\n|$)", re.IGNORECASE)
_ORIGINAL_MSG = re.compile(r"\n[ \t]*(?:-{2,}\s*Original Message\s*-{2,}|_{5,}|From:\s.+\n\s*Sent:\s)", re.IGNORECASE)


def parse_sender(raw: str | None) -> str:
    """'Asha <Asha@example.com>' -> 'asha@example.com' ('' if no address found)."""
    m = _ADDR.search(raw or "")
    return m.group(0).lower() if m else ""


def clean_email_body(body: str | None) -> str:
    """Keep only what the student typed: drop the quoted lesson, '> ' lines and phone signatures."""
    s = "\n" + (body or "").replace("\r\n", "\n").replace("\r", "\n")
    for pat in (_QUOTE_HEADER, _ORIGINAL_MSG):
        m = pat.search(s)
        if m:
            s = s[:m.start()]
    lines = [ln for ln in s.split("\n") if not ln.lstrip().startswith(">")]
    while lines and re.match(r"^\s*(sent from my .*|get outlook for .*|--\s*)$", lines[-1], re.IGNORECASE):
        lines.pop()
    return "\n".join(lines).strip("\n").rstrip()


def extract_choice(text: str) -> str:
    """For quiz answers: 'B', 'b)', '(b)', 'B) Writing instructions' -> 'b'."""
    first = (text or "").strip().split("\n")[0].strip().strip("`*_ ")
    m = _CHOICE.match(first)
    return m.group(1).lower() if m else first


NOTE_TEXT = {
    "curly_quotes": ("ℹ️ Your phone changed the straight quotes \" ' into curly quotes. "
                     "I fixed it this time. To avoid it, turn off 'smart punctuation' in your keyboard settings."),
}


def notes_footer(notes: list[str]) -> str:
    return "\n\n".join(NOTE_TEXT[n] for n in notes if n in NOTE_TEXT)
