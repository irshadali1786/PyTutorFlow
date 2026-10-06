"""Gmail channel: daily-lesson scheduling (email_only) and the email-answer flow, end to end through TutorService.

These cover what n8n relies on: GET /daily/due -> daily-message -> daily-result, and POST /email/incoming.
"""
from datetime import datetime, timedelta

import pytest

from app.progress.mastery import MasteryRules
from app.service.normalize import clean_email_body
from app.service.settings import ServiceSettings
from app.service.tutor import TutorService, reset_throttles

DAY = datetime(2026, 10, 5, 7, 0)


@pytest.fixture
def now():
    return [DAY]


@pytest.fixture
def svc(conn, now):
    reset_throttles()
    return TutorService(conn, settings=ServiceSettings(), clock=lambda: now[0],
                        rules=MasteryRules(passes_required=2, weak_after_consecutive_failures=3))


def due_ids(svc):
    return [s["student_id"] for s in svc.due_students(email_only=True)]


# ------------------------------------------------------------------ daily lesson flow
def test_send_time_is_respected(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=7, minute=59)
    assert due_ids(svc) == []
    now[0] = DAY.replace(hour=8, minute=0)
    assert due_ids(svc) == [sid]


def test_catch_up_when_machine_was_off_at_send_time(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=15, minute=30)                      # n8n/PC came back late
    assert due_ids(svc) == [sid]


def test_delivered_lesson_is_not_sent_again_same_day(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=8, minute=5)
    m = svc.build_daily_message(sid)
    assert m.kind == "LESSON" and m.subject.startswith("Python lesson:") and "What is programming?" in m.subject
    assert svc.record_daily_result(sid, True)["ok"]
    assert due_ids(svc) == []
    now[0] = DAY.replace(hour=23, minute=0)
    assert due_ids(svc) == []                                     # still not due later the same day


def test_unanswered_lesson_gets_reminder_next_day_not_a_new_lesson(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=8, minute=0)
    svc.build_daily_message(sid); svc.record_daily_result(sid, True)
    now[0] = DAY + timedelta(days=1, hours=1)
    assert due_ids(svc) == [sid]
    m = svc.build_daily_message(sid)
    assert m.kind == "REMINDER" and svc.engine.get_current_lesson(sid)["id"] == "prog-01"


def test_inactive_and_non_email_students_are_skipped(svc, conn, now):
    a = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    b = svc.create_student_with_code("Ravi", "08:00", "ravi@example.com")["student_id"]
    c = svc.engine.create_student("NoEmail")                       # no Gmail address -> never emailed
    conn.execute("UPDATE students SET is_active=0 WHERE id=?", (b,))
    conn.commit()
    now[0] = DAY.replace(hour=9)
    assert due_ids(svc) == [a]
    assert c not in due_ids(svc)


def test_failed_delivery_retries_then_gives_up_for_the_day_with_one_alert(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=8, minute=0)
    alerts = [svc.record_daily_result(sid, False, "smtp down")["admin_alert"] for _ in range(3)]
    assert alerts[:2] == [None, None] and "Asha" in alerts[2]
    assert due_ids(svc) == []                                      # gave up today
    now[0] = DAY + timedelta(days=1, hours=1)
    assert due_ids(svc) == [sid]                                   # tries again tomorrow


def test_failed_delivery_does_not_mark_lesson_delivered(svc, now):
    sid = svc.create_student_with_code("Asha", "08:00", "asha@example.com")["student_id"]
    now[0] = DAY.replace(hour=8)
    svc.build_daily_message(sid); svc.record_daily_result(sid, False, "x")
    assert svc._fresh(sid)["lesson_delivered_at"] is None


# ------------------------------------------------------------------ email answer flow
def delivered_student(svc, now, name="Asha", email="asha@example.com"):
    sid = svc.create_student_with_code(name, "08:00", email)["student_id"]
    now[0] = DAY.replace(hour=8, minute=0)
    svc.build_daily_message(sid); svc.record_daily_result(sid, True)
    return sid


def test_gmail_style_reply_with_quoted_lesson_is_graded(svc, now):
    delivered_student(svc, now)
    quoted = "b\n\nOn Mon, 5 Oct 2026 at 08:00, PyTutorFlow <tutor@example.com> wrote:\n> Python lesson\n> What is programming?"
    r = svc.handle_email("Asha K <Asha@example.com>", "<m1@mail.gmail.com>", "Python lesson: What is programming?", quoted)
    assert r.outcome == "PASS" and r.send and r.to == "asha@example.com"
    assert r.subject == "Re: Python lesson: What is programming?"


def test_quote_cleanup_variants():
    assert clean_email_body("a\n\n> old line\n> more") == "a"
    assert clean_email_body("a\n\n-----Original Message-----\nFrom: x\nSent: y") == "a"
    assert clean_email_body("a\n\nSent from my iPhone") == "a"
    assert clean_email_body("print('hi')\n\nOn Mon, 5 Oct 2026 at 08:00, T <t@x.com> wrote:\n> q") == "print('hi')"


def test_full_lesson_by_email_updates_progress_and_unlocks_next_lesson(svc, now, conn):
    sid = delivered_student(svc, now)
    r1 = svc.handle_email("asha@example.com", "<a1>", "Re: lesson", "b")
    r2 = svc.handle_email("asha@example.com", "<a2>", "Re: lesson", "a")
    assert r1.outcome == "PASS" and r2.outcome in ("LESSON_DONE", "MASTERED")
    p = svc.engine.progress_summary(sid)
    assert p["lessons_completed"] == 1 and p["exercises_passed"] == 2 and p["total_attempts"] == 2
    assert svc.engine.get_current_lesson(sid)["id"] == "python-01"            # "Next lesson: What is Python?"
    assert "What is Python?" in (r2.body or "")
    # the next lesson goes out with the next daily email, not immediately
    assert svc._fresh(sid)["lesson_delivered_at"] is None


def test_wrong_answer_gets_feedback_and_does_not_advance(svc, now):
    sid = delivered_student(svc, now)
    r = svc.handle_email("asha@example.com", "<w1>", "Re: lesson", "c")
    assert r.outcome == "RETRY" and r.send
    assert svc.engine.progress_summary(sid)["lessons_completed"] == 0


def test_same_message_id_is_never_graded_twice(svc, now):
    sid = delivered_student(svc, now)
    first = svc.handle_email("asha@example.com", "<dup>", "Re: lesson", "b")
    again = svc.handle_email("ASHA@example.com", "<dup>", "Re: lesson", "b")
    assert first.outcome == "PASS" and again.outcome == "DUPLICATE" and not again.send
    assert svc.engine.progress_summary(sid)["total_attempts"] == 1


def test_missing_message_id_is_rejected_not_graded(svc, now):
    sid = delivered_student(svc, now)
    r = svc.handle_email("asha@example.com", "  ", "Re: lesson", "b")
    assert r.outcome == "BAD_INPUT" and not r.send
    assert svc.engine.progress_summary(sid)["total_attempts"] == 0


def test_inactive_student_email_is_treated_as_unknown(svc, now, conn):
    sid = delivered_student(svc, now)
    conn.execute("UPDATE students SET is_active=0 WHERE id=?", (sid,)); conn.commit()
    r = svc.handle_email("asha@example.com", "<i1>", "Re: lesson", "b")
    assert r.outcome == "UNKNOWN_USER"
    assert svc.engine.progress_summary(sid)["total_attempts"] == 0


def test_commands_work_by_email_and_empty_body_is_friendly(svc, now):
    delivered_student(svc, now)
    assert svc.handle_email("asha@example.com", "<c1>", "Re: lesson", "/progress").outcome == "COMMAND"
    assert svc.handle_email("asha@example.com", "<c2>", "Re: lesson", "> only a quote").outcome == "BAD_INPUT"
