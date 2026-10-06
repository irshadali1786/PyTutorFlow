"""Small checks for the Gmail channel: sender -> student, Message-ID dedup, quoted-reply cleanup."""
from datetime import datetime

import pytest

from app.progress.mastery import MasteryRules
from app.service.normalize import clean_email_body, parse_sender
from app.service.settings import ServiceSettings
from app.service.tutor import TutorService, reset_throttles


@pytest.fixture
def svc(conn):
    reset_throttles()
    return TutorService(conn, settings=ServiceSettings(), clock=lambda: datetime(2026, 10, 5, 9, 0),
                        rules=MasteryRules(passes_required=2, weak_after_consecutive_failures=3))


def test_parse_sender_and_quoted_reply_cleanup():
    assert parse_sender("Asha K <Asha.K@example.com>") == "asha.k@example.com"
    body = "print('hi')\n\nOn Mon, 5 Oct 2026 at 08:00, Tutor <t@example.com> wrote:\n> LESSON 1\n> more"
    assert clean_email_body(body) == "print('hi')"


def test_known_sender_is_graded_and_duplicate_is_ignored(svc):
    svc.create_student_with_code("Asha", email="asha@example.com")
    svc.build_daily_message(1); svc.record_daily_result(1, True)
    r = svc.handle_email("Asha <ASHA@example.com>", "<id-1@mail>", "Re: lesson", "b\n\n> quoted")
    assert r.outcome == "PASS" and r.send and r.to == "asha@example.com" and r.subject == "Re: lesson"
    again = svc.handle_email("asha@example.com", "<id-1@mail>", "Re: lesson", "b")
    assert again.outcome == "DUPLICATE" and not again.send


def test_unknown_sender_gets_clear_answer_once_per_day(svc):
    r = svc.handle_email("stranger@example.com", "m1", "hi", "hello")
    assert r.outcome == "UNKNOWN_USER" and r.send and "not registered" in r.body and r.admin_alert
    r2 = svc.handle_email("stranger@example.com", "m2", "hi", "hello")
    assert r2.outcome == "UNKNOWN_USER" and not r2.send


def test_robots_are_never_answered(svc):
    assert not svc.handle_email("no-reply@accounts.google.com", "m9", "x", "y").send
