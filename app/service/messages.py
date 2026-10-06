"""Plain-text message builders for Telegram (no Markdown, so nothing can break parsing)."""
from __future__ import annotations

import textwrap

TELEGRAM_LIMIT = 3500        # real limit is 4096; keep a safety margin

HELP_TEXT = (
    "Here is what I understand:\n\n"
    "/hint - a small clue for your current task (write it as the only line of your reply)\n"
    "/lesson - show today's lesson again\n"
    "/next - start your next lesson now (if one is ready)\n"
    "/progress - see how you are doing\n"
    "/help - this list\n\n"
    "To answer a task, just send your answer as a normal message."
)

UNKNOWN_EMAIL_TEXT = (
    "Hi! This is a private Python tutor.\n"
    "This email address is not registered as a student. "
    "Please ask your tutor to add your email address, then write to me again."
)

EMAIL_FOOTER = "\n\n--\nPython Tutor - just reply to this email. Commands: /hint /lesson /next /progress /help"

UNKNOWN_USER_TEXT = (
    "Hi! I am a private tutor bot. 🙂\n"
    "Ask your tutor for a join code, then send me:\n\n/start YOURCODE"
)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on blank lines (then single lines) so each part fits in one Telegram message."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        while len(para) > limit:                     # one huge paragraph: cut at a line break
            cut = para.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(para[:cut].rstrip())
            para = para[cut:].lstrip("\n")
        current = para
    if current:
        parts.append(current)
    return parts


def lesson_message(lesson: dict, number: int, total: int) -> str:
    out = [f"📘 LESSON {number} of {total}: {lesson['title']}", "", lesson["content"].rstrip()]
    if (lesson.get("example_code") or "").strip():
        out += ["", "Example:", textwrap.indent(lesson["example_code"].rstrip(), "    ")]
    return "\n".join(out)


def exercise_message(exercise: dict, index: int, count: int) -> str:
    out = [f"📝 TASK {index} of {count}", "", exercise["question"].rstrip(), ""]
    if exercise["kind"] == "quiz":
        out.append("Reply to this email with just the letter, for example: b")
    else:
        out.append("Reply to this email with your code. Stuck? Reply with just: /hint")
    return "\n".join(out)


def welcome_message(name: str, send_time: str) -> str:
    return (
        f"Welcome, {name}! 🎉 You are connected.\n\n"
        f"Every day at {send_time} I will send you one small lesson and a task. "
        "Take your time - there is no rush.\n\n"
        "Want to start right now? Type /next\n"
        "Need the list of commands? Type /help"
    )


def progress_message(summary: dict, lesson_title: str | None) -> str:
    lines = [
        f"📊 Progress for {summary['name']}",
        f"Lessons finished: {summary['lessons_completed']} of {summary['lessons_total']} ({summary['percent']}%)",
        f"Topics mastered: {len(summary['mastered_concepts'])}",
        f"Day streak: {summary['streak_days']}",
    ]
    if lesson_title:
        lines.append(f"Current lesson: {lesson_title}")
    else:
        lines.append("You have finished every lesson that exists so far! 🏆")
    if summary["weak_concepts"]:
        lines.append("We will practise a bit more on: " + ", ".join(summary["weak_concepts"]))
    return "\n".join(lines)
