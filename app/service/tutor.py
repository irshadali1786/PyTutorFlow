"""TutorService: everything n8n needs, built on top of the (unchanged) LearningEngine.

n8n only moves messages around. All decisions live here:
  * who is this Telegram chat?            * is a lesson released / delivered?
  * what does the student's message mean? * who is due for a daily message?
  * which message text should be sent?    * should the tutor be alerted?

Gmail channel: handle_email() is the entry point for incoming mail (sender email -> student,
Gmail Message-ID -> de-duplication) and returns a ready-to-send reply. The older Telegram
handler (handle_incoming) is kept in the code but no n8n workflow calls it any more.

Outcome words returned to n8n (see handle_incoming / handle_email):
  PASS, LESSON_DONE, MASTERED, RETRY, WEAK, FINISHED, COMMAND, BAD_INPUT,
  UNKNOWN_USER, DUPLICATE, ERROR
"""
from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.evaluator.evaluator import EvaluationResult, evaluate_exercise
from app.progress.engine import LearningEngine
from app.progress.mastery import MasteryRules
from app.service import messages as msg
from app.service.normalize import clean_email_body, clean_telegram_code, extract_choice, notes_footer, parse_sender
from app.service.settings import ServiceSettings

log = logging.getLogger("tutor")

JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I to avoid typos
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# One code run at a time: protects your PC and keeps timing predictable.
_EVAL_LOCK = threading.Semaphore(1)
# In-memory, per-process throttles (restart clears them - that is fine).
_RECENT: dict[int, deque] = {}
_ALERTED_UNKNOWN: set[tuple] = set()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_AUTOMATED = re.compile(r"(^|[._-])(no-?reply|donotreply|do-not-reply|mailer-daemon|postmaster|bounce[s]?)([._-]|@)", re.IGNORECASE)


def reset_throttles() -> None:
    """Used by tests."""
    _RECENT.clear()
    _ALERTED_UNKNOWN.clear()


def _locked_evaluator(exercise: dict, answer: str) -> EvaluationResult:
    if not _EVAL_LOCK.acquire(timeout=60):
        raise TimeoutError("code runner is busy")
    try:
        return evaluate_exercise(exercise, answer)
    finally:
        _EVAL_LOCK.release()


@dataclass
class Reply:
    outcome: str
    chat_id: int | None = None
    messages: list[str] = field(default_factory=list)
    admin_alert: str | None = None


@dataclass
class EmailReply:
    """Ready-to-send answer for n8n. send=False means: do not email anything."""
    outcome: str
    send: bool = False
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    admin_alert: str | None = None


@dataclass
class DailyMessage:
    student_id: int
    chat_id: int | None
    kind: str                       # LESSON | REMINDER | NONE
    messages: list[str] = field(default_factory=list)
    admin_alert: str | None = None
    email: str | None = None        # Gmail delivery: address, subject and plain-text body
    subject: str | None = None
    body: str | None = None


def _local_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)


class TutorService:
    def __init__(self, conn: sqlite3.Connection, settings: ServiceSettings | None = None,
                 clock: Callable[[], datetime] | None = None, rules: MasteryRules | None = None,
                 engine: LearningEngine | None = None):
        self.conn = conn
        self.settings = settings or ServiceSettings()
        tz = self.settings.timezone
        self.clock = clock or (lambda: _local_now(tz))           # naive datetime in the app timezone
        self.engine = engine or LearningEngine(conn, rules=rules, clock=self.clock, evaluator=_locked_evaluator)

    # ------------------------------------------------------------ helpers
    def _stamp(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _today(self) -> str:
        return self.clock().date().isoformat()

    def _student_by_chat(self, chat_id: int | None) -> dict | None:
        if chat_id is None:
            return None
        row = self.conn.execute("SELECT * FROM students WHERE telegram_chat_id=? AND is_active=1", (chat_id,)).fetchone()
        return dict(row) if row else None

    def _student_by_email(self, addr: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM students WHERE lower(email)=? AND is_active=1", (addr,)).fetchone()
        return dict(row) if row else None

    def _fresh(self, student_id: int) -> dict:
        return self.engine.get_student(student_id)

    def _totals(self, student_id: int) -> tuple[int, int]:
        """(number of the lesson the student is on, total lessons)."""
        done = self.conn.execute(
            "SELECT COUNT(*) FROM lesson_progress WHERE student_id=? AND status='completed'", (student_id,)).fetchone()[0]
        total = self.conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        return done + 1, total

    def _concept_title(self, concept_id: str) -> str:
        row = self.conn.execute("SELECT title FROM concepts WHERE id=?", (concept_id,)).fetchone()
        return row["title"] if row else concept_id

    def _exercise_text(self, exercise: dict) -> str:
        count = self.conn.execute("SELECT COUNT(*) FROM exercises WHERE lesson_id=?", (exercise["lesson_id"],)).fetchone()[0]
        return msg.exercise_message(exercise, exercise["position"], count)

    def _lesson_messages(self, student_id: int, lesson: dict) -> list[str]:
        number, total = self._totals(student_id)
        out = msg.split_message(msg.lesson_message(lesson, number, total))
        exercise = self.engine.get_current_exercise(student_id)
        if exercise:
            out.append(self._exercise_text(exercise))
        return out

    @staticmethod
    def _parse_command(text: str) -> tuple[str | None, str]:
        if not text.startswith("/"):
            return None, ""
        head, _, rest = text.partition(" ")
        cmd = head.split("@")[0].lower()
        return cmd, rest.strip()

    def _finished_reply(self, chat_id: int) -> Reply:
        return Reply("FINISHED", chat_id, ["🏆 You have finished every lesson that exists so far. "
                                           "Your tutor will add more soon. Great work!"])

    # -------------------------------------------------------- admin: students
    @staticmethod
    def _valid_email(email: str) -> str:
        addr = parse_sender(email) if "<" in (email or "") else (email or "").strip().lower()
        if not _EMAIL_RE.match(addr):
            raise ValueError("email must look like name@example.com")
        return addr

    def set_student_email(self, student_id: int, email: str) -> dict:
        """Attach (or change) the Gmail address a student writes from."""
        addr = self._valid_email(email)
        student = self._fresh(student_id)
        try:
            with self.conn:
                self.conn.execute("UPDATE students SET email=? WHERE id=?", (addr, student_id))
        except sqlite3.IntegrityError:
            raise ValueError(f"The email {addr} already belongs to another student")
        return {"student_id": student_id, "name": student["name"], "email": addr}

    def create_student_with_code(self, name: str, send_time: str | None = None, email: str | None = None) -> dict:
        send_time = send_time or self.settings.default_send_time
        if not _TIME_RE.match(send_time):
            raise ValueError("send_time must look like 08:00 (24-hour HH:MM)")
        addr = self._valid_email(email) if email else None
        if addr and self._student_by_email(addr):
            raise ValueError(f"The email {addr} already belongs to another student")
        student_id = self.engine.create_student(name)
        self.conn.execute("UPDATE students SET preferred_send_time=? WHERE id=?", (send_time, student_id))
        self.conn.commit()
        info = self.regenerate_join_code(student_id)
        if addr:
            self.set_student_email(student_id, addr)
            info["email"] = addr
            info["instructions"] = (f"{info['name']} will receive lessons at {addr} (daily at {send_time}) "
                                    "and answers by replying to those emails.")
        return info

    def regenerate_join_code(self, student_id: int) -> dict:
        student = self._fresh(student_id)
        expires = (self.clock() + timedelta(hours=self.settings.join_code_ttl_hours)).isoformat(timespec="seconds")
        for _ in range(20):
            code = "".join(secrets.choice(JOIN_ALPHABET) for _ in range(6))
            try:
                with self.conn:
                    self.conn.execute("UPDATE students SET join_code=?, join_code_expires_at=? WHERE id=?",
                                      (code, expires, student_id))
                return {"student_id": student_id, "name": student["name"], "join_code": code,
                        "expires_at": expires, "instructions": f"Ask {student['name']} to send the bot:  /start {code}"}
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not generate a unique join code")

    def students_overview(self) -> list[dict]:
        out = []
        now = self.clock()
        for s in self.engine.list_students():
            p = self.engine.progress_summary(s["id"])
            last = s["last_activity_at"] or s["lesson_delivered_at"] or s["created_at"]
            p.update({
                "linked": s["telegram_chat_id"] is not None,
                "email": s.get("email"),
                "preferred_send_time": s["preferred_send_time"],
                "lesson_delivered": bool(s["lesson_delivered_at"]),
                "days_inactive": (now.date() - datetime.fromisoformat(last).date()).days,
                "join_code_pending": bool(s["join_code"]),
            })
            out.append(p)
        return out

    # --------------------------------------------------- telegram: incoming
    def next_offset(self) -> int:
        row = self.conn.execute("SELECT MAX(update_id) FROM processed_updates").fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def _claim_update(self, update_id: int, chat_id: int | None) -> bool:
        with self.conn:
            cur = self.conn.execute("INSERT OR IGNORE INTO processed_updates(update_id, chat_id, received_at) VALUES(?,?,?)",
                                    (update_id, chat_id, self._stamp()))
        return cur.rowcount == 1

    def handle_incoming(self, update: dict) -> Reply:
        """Handle ONE Telegram message. Never raises: failures become outcome ERROR.

        The update is recorded BEFORE it is processed, so a retry can never grade the
        same message twice (at-most-once). If processing crashes the student gets an
        apology and simply resends.
        """
        chat_id = update.get("chat_id")
        try:
            if not self._claim_update(int(update["update_id"]), chat_id):
                return Reply("DUPLICATE", chat_id)
            return self._route(update)
        except Exception as exc:                                  # noqa: BLE001 - deliberate catch-all
            log.exception("error while handling update")
            return Reply("ERROR", chat_id,
                         ["Sorry, something went wrong on my side. Please send your message again in a minute."],
                         f"Error handling update {update.get('update_id')} from chat {chat_id}: {exc!r}")

    def _route(self, update: dict) -> Reply:
        chat_id = update.get("chat_id")
        text = (update.get("text") or "").strip()
        username = update.get("username")
        cmd, arg = self._parse_command(text)
        student = self._student_by_chat(chat_id)

        if student is None:
            if cmd == "/start" and arg:
                return self._bind(chat_id, username, arg)
            return self._unknown_user(chat_id, username)

        if cmd == "/start":
            return Reply("COMMAND", chat_id, [f"Welcome back, {student['name']}! 🙂 Type /lesson to see where you are, or /help."])
        if cmd:
            return self._command(student, cmd)
        if not text:
            return Reply("BAD_INPUT", chat_id, ["I can only read text messages. Please type your answer as text. 🙂"])
        return self._answer(student, text)

    def _unknown_user(self, chat_id: int | None, username: str | None) -> Reply:
        key = (chat_id or 0, self._today())
        alert = None
        if key not in _ALERTED_UNKNOWN:
            _ALERTED_UNKNOWN.add(key)
            alert = f"Unknown Telegram user messaged the bot (chat_id={chat_id}, username=@{username or '-'})."
        return Reply("UNKNOWN_USER", chat_id, [msg.UNKNOWN_USER_TEXT], alert)

    def _bind(self, chat_id: int, username: str | None, code: str) -> Reply:
        code = code.strip().upper()
        row = self.conn.execute("SELECT * FROM students WHERE join_code=? AND is_active=1", (code,)).fetchone()
        expired = bool(row and row["join_code_expires_at"] and
                       datetime.fromisoformat(row["join_code_expires_at"]) < self.clock())
        if row is None or expired:
            return Reply("BAD_INPUT", chat_id,
                         ["That code is not valid or has expired. Please ask your tutor for a new one."],
                         f"Failed join attempt with code '{code[:12]}' from chat_id={chat_id} (@{username or '-'}).")
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE students SET telegram_chat_id=?, telegram_username=?, join_code=NULL, join_code_expires_at=NULL WHERE id=?",
                    (chat_id, username, row["id"]))
        except sqlite3.IntegrityError:
            return Reply("BAD_INPUT", chat_id, ["This Telegram account is already linked to a student."])
        return Reply("COMMAND", chat_id, [msg.welcome_message(row["name"], row["preferred_send_time"])],
                     f"✅ {row['name']} joined the bot.")


    # ------------------------------------------------------- gmail: incoming
    def _claim_email(self, message_id: str, sender: str) -> bool:
        with self.conn:
            cur = self.conn.execute("INSERT OR IGNORE INTO processed_emails(message_id, sender, received_at) VALUES(?,?,?)",
                                    (message_id, sender, self._stamp()))
        return cur.rowcount == 1

    @staticmethod
    def _reply_subject(subject: str | None) -> str:
        s = (subject or "").strip()
        if not s:
            return "Python Tutor"
        return s if s.lower().startswith("re:") else "Re: " + s

    def _email_reply(self, outcome: str, to: str, subject: str | None, messages: list[str],
                     admin_alert: str | None = None) -> EmailReply:
        texts = [m for m in messages if m and m.strip()]
        if not texts:
            return EmailReply(outcome, False, to, None, None, admin_alert)
        return EmailReply(outcome, True, to, self._reply_subject(subject), "\n\n".join(texts) + msg.EMAIL_FOOTER, admin_alert)

    def handle_email(self, sender: str, message_id: str, subject: str | None, body: str | None) -> EmailReply:
        """Handle ONE incoming Gmail message. Never raises: failures become outcome ERROR.

        The Message-ID is recorded BEFORE the message is processed, so the same email can never be
        evaluated twice (at-most-once). Unknown senders get a clear (throttled) answer, not an error.
        """
        addr = parse_sender(sender)
        mid = (message_id or "").strip()
        try:
            if not mid:
                return EmailReply("BAD_INPUT", False, addr or None, None, None,
                                  f"Email from {addr or 'unknown'} had no message id and was ignored.")
            if not self._claim_email(mid, addr):
                return EmailReply("DUPLICATE", False, addr or None)
            return self._route_email(addr, subject, body)
        except Exception as exc:                                  # noqa: BLE001 - deliberate catch-all
            log.exception("error while handling email")
            alert = f"Error handling email {mid} from {addr or 'unknown'}: {exc!r}"
            return self._email_reply("ERROR", addr, subject,
                                     ["Sorry, something went wrong on my side. Please send your message again in a minute."]
                                     if addr and not _AUTOMATED.search(addr) else [], alert)

    def _route_email(self, addr: str, subject: str | None, body: str | None) -> EmailReply:
        if not addr or _AUTOMATED.search(addr):                   # never answer robots: avoids mail loops
            return EmailReply("IGNORED", False, addr or None)
        student = self._student_by_email(addr)
        if student is None:
            key = (addr, self._today())
            first_today = key not in _ALERTED_UNKNOWN
            _ALERTED_UNKNOWN.add(key)
            return self._email_reply("UNKNOWN_USER", addr, subject,
                                     [msg.UNKNOWN_EMAIL_TEXT] if first_today else [],
                                     f"Unknown sender wrote to the tutor mailbox: {addr}" if first_today else None)

        text = clean_email_body(body)
        if not text and (subject or "").strip().startswith("/"):
            text = subject.strip()
        if not text:
            return self._email_reply("BAD_INPUT", addr, subject,
                                     ["I could not find any text in your email. Please reply with your answer typed in the email body."])
        cmd, _arg = self._parse_command(text.split("\n", 1)[0].strip())
        if cmd == "/start":
            reply = Reply("COMMAND", None, [f"Welcome back, {student['name']}! Reply /lesson to see where you are, or /help."])
        elif cmd:
            reply = self._command(student, cmd)
        else:
            reply = self._answer(student, text)
        return self._email_reply(reply.outcome, addr, subject, reply.messages, reply.admin_alert)

    # ---- commands
    def _command(self, student: dict, cmd: str) -> Reply:
        chat_id, sid = student["telegram_chat_id"], student["id"]
        if cmd == "/help":
            return Reply("COMMAND", chat_id, [msg.HELP_TEXT])
        if cmd == "/progress":
            lesson = self.engine.get_current_lesson(sid)
            return Reply("COMMAND", chat_id, [msg.progress_message(self.engine.progress_summary(sid), lesson["title"] if lesson else None)])
        if cmd == "/next":
            return self._cmd_next(student)
        if cmd in ("/lesson", "/hint"):
            lesson = self.engine.get_current_lesson(sid)
            if lesson is None:
                return self._finished_reply(chat_id)
            if not student["lesson_delivered_at"]:
                return Reply("COMMAND", chat_id, [self._not_released_text(student)])
            if cmd == "/lesson":
                return Reply("COMMAND", chat_id, self._lesson_messages(sid, lesson))
            hint = self.engine.get_hint(sid)
            return Reply("COMMAND", chat_id, [f"💡 Hint: {hint}" if hint else "No hint available right now."])
        return Reply("BAD_INPUT", chat_id, ["I do not know that command. Type /help to see what I understand."])

    def _not_released_text(self, student: dict) -> str:
        return (f"Your next lesson is not released yet. It arrives tomorrow at {student['preferred_send_time']}. "
                "Want to go now? Type /next")

    def _cmd_next(self, student: dict) -> Reply:
        chat_id, sid = student["telegram_chat_id"], student["id"]
        lesson = self.engine.get_current_lesson(sid)
        if lesson is None:
            return self._finished_reply(chat_id)
        if student["lesson_delivered_at"]:
            exercise = self.engine.get_current_exercise(sid)
            out = ["You are still working on this lesson - let's finish it first. 🙂"]
            if exercise:
                out.append(self._exercise_text(exercise))
            return Reply("COMMAND", chat_id, out)
        today = self._today()
        used = student["released_today"] if student["released_date"] == today else 0
        if used >= self.settings.max_lessons_per_day:
            return Reply("COMMAND", chat_id, [
                f"That is {used} lessons today - great work! 👏 Rest your brain. "
                f"Your next lesson arrives tomorrow at {student['preferred_send_time']}."])
        self._mark_delivered(sid, used + 1)
        return Reply("COMMAND", chat_id, self._lesson_messages(sid, lesson))

    def _mark_delivered(self, student_id: int, released_today: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE students SET lesson_delivered_at=?, last_daily_on=?, released_date=?, released_today=? WHERE id=?",
                (self._stamp(), self._today(), self._today(), released_today, student_id))

    # ---- answers
    def _rate_limited(self, key: int) -> bool:
        window = _RECENT.setdefault(key, deque())
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.settings.rate_limit_per_minute:
            return True
        window.append(now)
        return False

    def _answer(self, student: dict, text: str) -> Reply:
        chat_id, sid = student["telegram_chat_id"], student["id"]
        if student["current_lesson_id"] is None:
            return self._finished_reply(chat_id)
        if not student["lesson_delivered_at"]:
            return Reply("BAD_INPUT", chat_id, [self._not_released_text(student)])
        if self._rate_limited(sid):
            return Reply("BAD_INPUT", chat_id, ["You are sending messages very fast. Take a breath and try again in a minute. 🙂"])

        exercise = self.engine.get_current_exercise(sid)
        if exercise["kind"] == "quiz":
            answer, notes = extract_choice(text), []
        else:
            answer, notes = clean_telegram_code(text)

        res = self.engine.submit(sid, answer)
        ev = res.evaluation
        concept = self._concept_title(exercise["concept_id"])
        name = student["name"]

        if ev.passed:
            if res.lesson_completed:
                with self.conn:
                    self.conn.execute("UPDATE students SET lesson_delivered_at=NULL WHERE id=?", (sid,))
            head = f"✅ {ev.feedback}"
            if res.curriculum_finished:
                return Reply("FINISHED", chat_id,
                             [head, "🏆 You finished every lesson that exists so far. Your tutor will add more soon!"],
                             f"🏆 {name} finished the whole curriculum.")
            if res.concept_mastered:
                nxt = res.next_lesson["title"]
                p = self.engine.progress_summary(sid)
                return Reply("MASTERED", chat_id,
                             [head, f"🎉 Lesson complete - you have mastered: {concept}!\n\n"
                                    f"Next lesson: {nxt}\nIt arrives tomorrow at {student['preferred_send_time']}, "
                                    "or type /next to start it now."],
                             f"🎉 {name} mastered '{concept}' ({p['lessons_completed']}/{p['lessons_total']} lessons).")
            if res.lesson_completed:
                nxt = res.next_lesson["title"]
                return Reply("LESSON_DONE", chat_id,
                             [head, f"🎉 Lesson complete!\n\nNext lesson: {nxt}\nIt arrives tomorrow at "
                                    f"{student['preferred_send_time']}, or type /next to start it now."])
            out = [head]
            if res.next_exercise:
                out.append(self._exercise_text(res.next_exercise))
            return Reply("PASS", chat_id, out)

        # --- not passed
        feedback = f"❌ {ev.feedback}"
        footer = notes_footer(notes)
        if footer:
            feedback += "\n\n" + footer
        out = [feedback]
        if res.attempt_number >= self.settings.auto_hint_after_attempts or res.concept_weak:
            hint = self.engine.get_hint(sid)
            if hint:
                out.append(f"💡 Hint: {hint}")
        if res.concept_weak:
            out.append("Don't worry - this part is tricky for everyone. Read the lesson again with /lesson, "
                       "then try once more. Mistakes are how we learn. 💪")
            return Reply("WEAK", chat_id, out,
                         f"⚠️ {name} is struggling with '{concept}' ({exercise['id']}, attempt {res.attempt_number}).")
        out.append("Try again - send your corrected answer.")
        return Reply("RETRY", chat_id, out)

    # --------------------------------------------------------------- daily
    def due_students(self, email_only: bool = False) -> list[dict]:
        """Students whose daily/catch-up message is due. email_only=True (used by the API/n8n) = Gmail students only."""
        now = self.clock()
        today, hhmm = now.date().isoformat(), now.strftime("%H:%M")
        channel = "email IS NOT NULL" if email_only else "(telegram_chat_id IS NOT NULL OR email IS NOT NULL)"
        rows = self.conn.execute(
            f"SELECT * FROM students WHERE is_active=1 AND {channel} AND current_lesson_id IS NOT NULL "
            "AND (last_daily_on IS NULL OR last_daily_on != ?) AND preferred_send_time <= ? ORDER BY id",
            (today, hhmm)).fetchall()
        out = []
        for r in rows:
            if r["daily_failure_on"] == today and r["daily_failures"] >= self.settings.daily_max_failures:
                continue                                              # gave up for today; retry tomorrow
            out.append({"student_id": r["id"], "name": r["name"], "chat_id": r["telegram_chat_id"], "email": r["email"]})
        return out

    def build_daily_message(self, student_id: int) -> DailyMessage:
        student = self._fresh(student_id)
        chat_id = student["telegram_chat_id"]
        email = student.get("email")
        lesson = self.engine.get_current_lesson(student_id)
        if lesson is None or (chat_id is None and not email):
            return DailyMessage(student_id, chat_id, "NONE", email=email)
        name = student["name"]
        if not student["lesson_delivered_at"]:
            out = [f"Good morning, {name}! ☀️ Here is today's lesson."] + self._lesson_messages(student_id, lesson)
            return DailyMessage(student_id, chat_id, "LESSON", out, email=email,
                                subject=f"Python lesson: {lesson['title']}", body="\n\n".join(out) + msg.EMAIL_FOOTER)

        last = student["last_activity_at"] or student["lesson_delivered_at"]
        days = (self.clock().date() - datetime.fromisoformat(last).date()).days
        exercise = self.engine.get_current_exercise(student_id)
        intro = (f"Good morning, {name}! ☀️ You are in the middle of: {lesson['title']}. No rush - here is your task again:"
                 if days < 2 else
                 f"Hi {name}! 🙂 It has been a few days - no problem at all. Let's pick up where you left off: {lesson['title']}.")
        out = [intro] + ([self._exercise_text(exercise)] if exercise else [])
        n = self.settings.inactive_alert_days
        alert = None
        if days >= n and (days - n) % n == 0:
            alert = f"⏰ {name} has not replied for {days} days (stuck on {lesson['id']})."
        return DailyMessage(student_id, chat_id, "REMINDER", out, alert, email=email,
                            subject=f"Python reminder: {lesson['title']}", body="\n\n".join(out) + msg.EMAIL_FOOTER)

    def record_daily_result(self, student_id: int, delivered: bool, error: str | None = None) -> dict:
        student = self._fresh(student_id)
        today = self._today()
        if delivered:
            with self.conn:
                self.conn.execute("UPDATE students SET last_daily_on=?, daily_failures=0, daily_failure_on=NULL WHERE id=?",
                                  (today, student_id))
            if student["current_lesson_id"] and not student["lesson_delivered_at"]:
                used = student["released_today"] if student["released_date"] == today else 0
                self._mark_delivered(student_id, used + 1)
            return {"ok": True, "admin_alert": None}
        failures = (student["daily_failures"] if student["daily_failure_on"] == today else 0) + 1
        with self.conn:
            self.conn.execute("UPDATE students SET daily_failures=?, daily_failure_on=? WHERE id=?", (failures, today, student_id))
        alert = None
        if failures == self.settings.daily_max_failures:
            alert = (f"❗ Could not deliver today's message to {student['name']} after {failures} tries. "
                     f"Last error: {(error or 'unknown')[:200]}")
        return {"ok": True, "admin_alert": alert}
