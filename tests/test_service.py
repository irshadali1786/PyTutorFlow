import itertools
from datetime import datetime, timedelta

import pytest

from app.progress.mastery import MasteryRules
from app.service.settings import ServiceSettings
from app.service.tutor import TutorService, reset_throttles
from tests.solutions import SOLUTIONS


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 5, 7, 0))           # a Monday, 07:00 (before the 08:00 slot)


@pytest.fixture
def svc(conn, clock):
    reset_throttles()
    return TutorService(conn, settings=ServiceSettings(), clock=clock,
                        rules=MasteryRules(passes_required=2, weak_after_consecutive_failures=3))


_ids = itertools.count(1000)


def say(svc, chat_id, text, **kw):
    return svc.handle_incoming({"update_id": next(_ids), "chat_id": chat_id, "text": text, **kw})


def join(svc, name, chat_id):
    info = svc.create_student_with_code(name)
    r = say(svc, chat_id, f"/start {info['join_code']}")
    assert r.outcome == "COMMAND"
    return info["student_id"]


def correct(svc, sid):
    return SOLUTIONS[svc.engine.get_current_exercise(sid)["id"]]


def release_and_finish_lesson(svc, sid, chat_id):
    say(svc, chat_id, "/next")
    r = None
    lesson = svc.engine.get_current_lesson(sid)["id"]
    while svc.engine.get_current_lesson(sid) and svc.engine.get_current_lesson(sid)["id"] == lesson:
        r = say(svc, chat_id, correct(svc, sid))
    return r


# ------------------------------------------------------------- onboarding
def test_join_code_binds_chat(svc):
    info = svc.create_student_with_code("Asha")
    assert len(info["join_code"]) == 6
    r = say(svc, 111, f"/start {info['join_code'].lower()}", username="asha_tg")      # case-insensitive
    assert r.outcome == "COMMAND" and "Welcome, Asha" in r.messages[0] and "joined" in r.admin_alert
    s = svc.engine.get_student(info["student_id"])
    assert s["telegram_chat_id"] == 111 and s["telegram_username"] == "asha_tg" and s["join_code"] is None


def test_join_code_is_single_use(svc):
    info = svc.create_student_with_code("Asha")
    say(svc, 111, f"/start {info['join_code']}")
    r = say(svc, 222, f"/start {info['join_code']}")
    assert r.outcome == "BAD_INPUT" and svc._student_by_chat(222) is None


def test_expired_and_wrong_codes(svc, clock):
    info = svc.create_student_with_code("Asha")
    assert say(svc, 111, "/start WRONG1").outcome == "BAD_INPUT"
    clock.now += timedelta(hours=73)
    assert say(svc, 111, f"/start {info['join_code']}").outcome == "BAD_INPUT"
    new = svc.regenerate_join_code(info["student_id"])
    assert say(svc, 111, f"/start {new['join_code']}").outcome == "COMMAND"


def test_unknown_user_is_never_served_and_alert_is_throttled(svc):
    r1 = say(svc, 999, 'print("hi")')
    r2 = say(svc, 999, "hello again")
    assert r1.outcome == r2.outcome == "UNKNOWN_USER"
    assert r1.admin_alert and r2.admin_alert is None
    assert svc.conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_chat_already_linked_cannot_take_second_student(svc):
    join(svc, "Asha", 111)
    other = svc.create_student_with_code("Ravi")
    r = say(svc, 111, f"/start {other['join_code']}")                 # 111 is already Asha -> treated as /start
    assert svc.engine.get_student(other["student_id"])["telegram_chat_id"] is None
    assert r.outcome == "COMMAND"


def test_bad_send_time_and_duplicate_name(svc):
    with pytest.raises(ValueError):
        svc.create_student_with_code("X", "8am")
    svc.create_student_with_code("Asha", "09:30")
    with pytest.raises(ValueError):
        svc.create_student_with_code("asha")


# ------------------------------------------------------------- gating + commands
def test_answers_are_refused_until_lesson_is_delivered(svc):
    sid = join(svc, "Asha", 111)
    r = say(svc, 111, "b")
    assert r.outcome == "BAD_INPUT" and "/next" in r.messages[0]
    assert svc.conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_next_releases_first_lesson(svc):
    sid = join(svc, "Asha", 111)
    r = say(svc, 111, "/next")
    assert r.outcome == "COMMAND" and "LESSON 1 of 5" in r.messages[0] and "TASK 1 of 2" in r.messages[-1]
    assert svc.engine.get_student(sid)["lesson_delivered_at"]
    again = say(svc, 111, "/next")
    assert "still working" in again.messages[0]


def test_commands_help_progress_lesson_hint(svc):
    sid = join(svc, "Asha", 111)
    assert "/hint" in say(svc, 111, "/help").messages[0]
    assert "not released" in say(svc, 111, "/lesson").messages[0]
    say(svc, 111, "/next")
    assert "LESSON 1" in say(svc, 111, "/lesson").messages[0]
    h1, h2 = say(svc, 111, "/hint").messages[0], say(svc, 111, "/hint").messages[0]
    assert h1.startswith("💡") and h1 != h2
    assert "Lessons finished: 0 of 5" in say(svc, 111, "/progress").messages[0]
    assert say(svc, 111, "/bogus").outcome == "BAD_INPUT"
    assert say(svc, 111, "/hint@MyTutorBot").messages[0].startswith("💡")     # group-style command suffix


def test_non_text_message(svc):
    join(svc, "Asha", 111)
    say(svc, 111, "/next")
    assert say(svc, 111, "").outcome == "BAD_INPUT"


# ------------------------------------------------------------- outcomes
def test_pass_then_mastered_flow(svc):
    sid = join(svc, "Asha", 111)
    say(svc, 111, "/next")
    r1 = say(svc, 111, "b")
    assert r1.outcome == "PASS" and "TASK 2 of 2" in r1.messages[-1] and r1.admin_alert is None
    r2 = say(svc, 111, "a")
    assert r2.outcome == "MASTERED" and "mastered: What is programming?" in r2.messages[1]
    assert "mastered 'What is programming?'" in r2.admin_alert
    s = svc.engine.get_student(sid)
    assert s["current_lesson_id"] == "python-01" and s["lesson_delivered_at"] is None     # next lesson waits for release


def test_retry_adds_hint_from_second_attempt(svc):
    join(svc, "Asha", 111)
    say(svc, 111, "/next")
    r1 = say(svc, 111, "c")
    assert r1.outcome == "RETRY" and not any("Hint" in m for m in r1.messages)
    r2 = say(svc, 111, "a")
    assert r2.outcome == "RETRY" and any(m.startswith("💡 Hint:") for m in r2.messages)


def test_weak_after_three_misses_alerts_tutor(svc):
    join(svc, "Asha", 111)
    say(svc, 111, "/next")
    say(svc, 111, "c"); say(svc, 111, "c")
    r = say(svc, 111, "c")
    assert r.outcome == "WEAK" and "struggling" in r.admin_alert and any("/lesson" in m for m in r.messages)


def test_curly_quotes_from_phone_still_pass_and_student_is_told(svc):
    sid = join(svc, "Asha", 111)
    for _ in range(3):
        release_and_finish_lesson(svc, sid, 111)
        svc.conn.execute("UPDATE students SET released_today=0, released_date=NULL WHERE id=?", (sid,)); svc.conn.commit()
    say(svc, 111, "/next")
    assert svc.engine.get_current_exercise(sid)["id"] == "print-01-ex1"
    r = say(svc, 111, "print(“Hello World”)")
    assert r.outcome == "PASS"
    wrong = say(svc, 111, "print(“Hello”)")
    assert wrong.outcome == "RETRY" and "smart punctuation" in wrong.messages[0]


def test_blocked_code_is_never_run_and_is_friendly(svc, tmp_path):
    sid = join(svc, "Asha", 111)
    for _ in range(3):
        release_and_finish_lesson(svc, sid, 111)
        svc.conn.execute("UPDATE students SET released_today=0, released_date=NULL WHERE id=?", (sid,)); svc.conn.commit()
    say(svc, 111, "/next")
    marker = tmp_path / "pwned.txt"
    r = say(svc, 111, f"open(r'{marker}','w').write('x')")
    assert r.outcome == "RETRY" and "not allowed" in r.messages[0] and not marker.exists()


def test_full_curriculum_ends_with_finished(svc, clock):
    sid = join(svc, "Asha", 111)
    last = None
    for day in range(5):
        clock.now = datetime(2026, 10, 5 + day, 9, 0)
        last = release_and_finish_lesson(svc, sid, 111)
    assert last.outcome == "FINISHED" and "finished the whole curriculum" in last.admin_alert
    assert say(svc, 111, "anything").outcome == "FINISHED"
    assert say(svc, 111, "/next").outcome == "FINISHED"


def test_max_lessons_per_day(svc):
    sid = join(svc, "Asha", 111)
    release_and_finish_lesson(svc, sid, 111)
    release_and_finish_lesson(svc, sid, 111)
    r = say(svc, 111, "/next")
    assert "2 lessons today" in r.messages[0]
    assert svc.engine.get_student(sid)["lesson_delivered_at"] is None
    svc.clock.now += timedelta(days=1)
    assert "LESSON 3" in say(svc, 111, "/next").messages[0]


def test_rate_limit(conn, clock):
    reset_throttles()
    s = TutorService(conn, settings=ServiceSettings(rate_limit_per_minute=3), clock=clock)
    join(s, "Asha", 111)
    say(s, 111, "/next")
    outcomes = [say(s, 111, "c").outcome for _ in range(5)]
    assert outcomes[:3] != ["BAD_INPUT"] * 3 and outcomes[-1] == "BAD_INPUT"


# ------------------------------------------------------------- idempotency + errors
def test_duplicate_update_is_ignored_and_offset_advances(svc):
    join(svc, "Asha", 111)
    say(svc, 111, "/next")
    upd = {"update_id": 5000, "chat_id": 111, "text": "b"}
    assert svc.handle_incoming(upd).outcome == "PASS"
    dup = svc.handle_incoming(upd)
    assert dup.outcome == "DUPLICATE" and dup.messages == []
    assert svc.conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
    assert svc.next_offset() == 5001


def test_offset_starts_at_zero(svc):
    assert svc.next_offset() == 0


def test_internal_error_becomes_error_outcome(svc, monkeypatch):
    join(svc, "Asha", 111)
    say(svc, 111, "/next")
    monkeypatch.setattr(svc.engine, "submit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = say(svc, 111, "b")
    assert r.outcome == "ERROR" and "send your message again" in r.messages[0] and "boom" in r.admin_alert


# ------------------------------------------------------------- two students
def test_two_students_are_independent(svc, clock):
    a = join(svc, "Asha", 111)
    b = join(svc, "Ravi", 222)
    for _ in range(3):
        release_and_finish_lesson(svc, a, 111)
        svc.conn.execute("UPDATE students SET released_today=0, released_date=NULL WHERE id=?", (a,)); svc.conn.commit()
    say(svc, 222, "/next")
    say(svc, 222, "c")
    assert svc.engine.get_current_lesson(a)["id"] == "print-01"
    assert svc.engine.get_current_lesson(b)["id"] == "prog-01"
    assert "Lessons finished: 3 of 5" in say(svc, 111, "/progress").messages[0]
    assert "Lessons finished: 0 of 5" in say(svc, 222, "/progress").messages[0]
    assert say(svc, 111, "/next").outcome == "COMMAND"
    assert svc.engine.get_student(b)["telegram_chat_id"] == 222


# ------------------------------------------------------------- daily + missed lessons
def test_daily_not_due_before_send_time(svc):
    join(svc, "Asha", 111)
    assert svc.due_students() == []                                   # 07:00 < 08:00


def test_daily_flow_lesson_then_not_due_again(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 8, 0)
    due = svc.due_students()
    assert [d["student_id"] for d in due] == [sid] and due[0]["chat_id"] == 111
    d = svc.build_daily_message(sid)
    assert d.kind == "LESSON" and "Good morning, Asha" in d.messages[0] and "LESSON 1 of 5" in d.messages[1]
    assert svc.engine.get_student(sid)["lesson_delivered_at"] is None           # not delivered until n8n confirms
    assert svc.record_daily_result(sid, True)["ok"]
    s = svc.engine.get_student(sid)
    assert s["lesson_delivered_at"] and s["last_daily_on"] == "2026-10-05"
    assert svc.due_students() == []
    assert say(svc, 111, "b").outcome == "PASS"                              # answers work after delivery


def test_missed_morning_is_caught_up_when_pc_wakes_late(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 10, 40)                                # PC was off at 08:00
    assert [d["student_id"] for d in svc.due_students()] == [sid]


def test_unfinished_lesson_gets_reminder_not_new_lesson(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 8, 0)
    svc.build_daily_message(sid); svc.record_daily_result(sid, True)
    say(svc, 111, "b")                                                       # 1 of 2 tasks done
    clock.now = datetime(2026, 10, 6, 8, 0)
    d = svc.build_daily_message(sid)
    assert d.kind == "REMINDER" and "middle of" in d.messages[0] and "TASK 2 of 2" in d.messages[1]
    assert svc.engine.get_current_lesson(sid)["id"] == "prog-01"


def test_inactivity_alert_on_day_3_and_6_only(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 8, 0)
    svc.build_daily_message(sid); svc.record_daily_result(sid, True)
    alerts = {}
    for day in range(1, 8):
        clock.now = datetime(2026, 10, 5 + day, 8, 0)
        alerts[day] = svc.build_daily_message(sid).admin_alert
    assert [d for d, a in alerts.items() if a] == [3, 6]


def test_delivery_failures_retry_then_give_up_and_alert(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 8, 0)
    r1 = svc.record_daily_result(sid, False, "network down")
    assert r1["admin_alert"] is None and svc.due_students()                  # still due: will retry
    svc.record_daily_result(sid, False, "network down")
    r3 = svc.record_daily_result(sid, False, "network down")
    assert "Could not deliver" in r3["admin_alert"]
    assert svc.due_students() == []                                          # stops today
    clock.now = datetime(2026, 10, 6, 8, 0)
    assert len(svc.due_students()) == 1                                      # tries again tomorrow


def test_unlinked_and_finished_students_are_not_due(svc, clock):
    svc.create_student_with_code("Unlinked")
    clock.now = datetime(2026, 10, 5, 9, 0)
    assert svc.due_students() == []


def test_next_lesson_after_mastery_arrives_next_morning(svc, clock):
    sid = join(svc, "Asha", 111)
    clock.now = datetime(2026, 10, 5, 8, 0)
    svc.build_daily_message(sid); svc.record_daily_result(sid, True)
    say(svc, 111, "b"); assert say(svc, 111, "a").outcome == "MASTERED"
    assert svc.due_students() == []                                          # already got today's message
    clock.now = datetime(2026, 10, 6, 8, 0)
    d = svc.build_daily_message(sid)
    assert d.kind == "LESSON" and "LESSON 2 of 5" in d.messages[1]


def test_overview(svc):
    join(svc, "Asha", 111)
    svc.create_student_with_code("Ravi")
    ov = {s["name"]: s for s in svc.students_overview()}
    assert ov["Asha"]["linked"] and not ov["Ravi"]["linked"] and ov["Ravi"]["join_code_pending"]
