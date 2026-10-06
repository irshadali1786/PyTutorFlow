import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from app.progress.engine import LearningEngine, NoActiveLessonError, PrerequisiteError
from app.progress.mastery import MasteryRules, decide_status, required_passes
from tests.solutions import SOLUTIONS, finish_lesson, solve_current


# ---------------------------------------------------------------- students
def test_create_student(engine):
    sid = engine.create_student("Asha")
    s = engine.get_student(sid)
    assert s["name"] == "Asha" and s["streak_days"] == 0 and s["is_active"] == 1


def test_duplicate_student_is_rejected(engine):
    engine.create_student("Asha")
    with pytest.raises(ValueError):
        engine.create_student("asha")           # names are case-insensitive
    with pytest.raises(ValueError):
        engine.create_student("   ")


def test_first_lesson_is_assigned(engine):
    sid = engine.create_student("Asha")
    assert engine.get_current_lesson(sid)["id"] == "prog-01"
    assert engine.get_current_exercise(sid)["id"] == "prog-01-ex1"


# ---------------------------------------------------------------- submissions
def test_correct_submission_passes_and_is_saved(engine, conn):
    sid = engine.create_student("Asha")
    res = solve_current(engine, sid)
    assert res.evaluation.passed and res.attempt_number == 1
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
    ev = conn.execute("SELECT passed, score, evaluator FROM evaluations").fetchone()
    assert (ev["passed"], ev["score"], ev["evaluator"]) == (1, 100.0, "rule_based")
    assert engine.get_current_exercise(sid)["id"] == "prog-01-ex2"      # next exercise, same lesson


def test_incorrect_submission_keeps_student_on_same_exercise(engine, conn):
    sid = engine.create_student("Asha")
    res = engine.submit(sid, "c")
    assert not res.evaluation.passed and not res.lesson_completed
    assert engine.get_current_exercise(sid)["id"] == "prog-01-ex1"
    assert engine.get_current_lesson(sid)["id"] == "prog-01"
    m = conn.execute("SELECT error_type, concept_id FROM mistakes").fetchone()
    assert (m["error_type"], m["concept_id"]) == ("wrong_answer", "what-is-programming")
    att = conn.execute("SELECT attempt_count, passed FROM attempts WHERE student_id=?", (sid,)).fetchone()
    assert (att["attempt_count"], att["passed"]) == (1, 0)


def test_attempt_numbers_increase(engine):
    sid = engine.create_student("Asha")
    assert engine.submit(sid, "c").attempt_number == 1
    assert engine.submit(sid, "a").attempt_number == 2
    assert engine.submit(sid, "b").attempt_number == 3


def test_hints_come_one_at_a_time(engine, conn):
    sid = engine.create_student("Asha")
    h1, h2 = engine.get_hint(sid), engine.get_hint(sid)
    assert h1 != h2
    used = conn.execute("SELECT hints_used FROM attempts WHERE student_id=?", (sid,)).fetchone()[0]
    assert used == 2


# ---------------------------------------------------------------- progress
def test_progress_updates_after_pass_and_fail(engine):
    sid = engine.create_student("Asha")
    engine.submit(sid, "c")
    solve_current(engine, sid)
    p = engine.progress_summary(sid)
    assert p["total_attempts"] == 2 and p["exercises_passed"] == 1
    assert p["current_lesson"] == "prog-01" and p["lessons_completed"] == 0
    assert p["in_progress_concepts"] == ["what-is-programming"]
    assert p["average_score"] == 50.0


def test_streak_counts_consecutive_days(conn):
    now = [datetime(2026, 10, 1, 9, 0)]
    eng = LearningEngine(conn, rules=MasteryRules(), clock=lambda: now[0])
    sid = eng.create_student("Asha")
    solve_current(eng, sid)
    assert eng.get_student(sid)["streak_days"] == 1
    solve_current(eng, sid)                                   # same day -> unchanged
    assert eng.get_student(sid)["streak_days"] == 1
    now[0] += timedelta(days=1)
    solve_current(eng, sid)
    assert eng.get_student(sid)["streak_days"] == 2
    now[0] += timedelta(days=3)                               # missed days -> restarts
    solve_current(eng, sid)
    assert eng.get_student(sid)["streak_days"] == 1


# ---------------------------------------------------------------- mastery + moving on
def test_lesson_completion_masters_concept_and_unlocks_next(engine):
    sid = engine.create_student("Asha")
    first = solve_current(engine, sid)
    assert not first.lesson_completed and not first.concept_mastered      # 1 of 2 passes
    second = solve_current(engine, sid)
    assert second.lesson_completed and second.concept_mastered
    assert second.next_lesson["id"] == "python-01"
    assert engine.get_current_lesson(sid)["id"] == "python-01"
    p = engine.progress_summary(sid)
    assert p["mastered_concepts"] == ["what-is-programming"] and p["lessons_completed"] == 1


def test_mastery_rule_is_configurable(conn):
    strict = LearningEngine(conn, rules=MasteryRules(passes_required=5))   # more than exist -> capped at 2
    sid = strict.create_student("Asha")
    solve_current(strict, sid)
    assert solve_current(strict, sid).concept_mastered
    assert required_passes(MasteryRules(passes_required=5), 2) == 2
    assert required_passes(MasteryRules(passes_required=1), 2) == 1


def test_per_concept_override_from_yaml(conn):
    conn.execute("UPDATE concepts SET mastery_rules=? WHERE id='what-is-programming'", (json.dumps({"passes_required": 1}),))
    conn.commit()
    eng = LearningEngine(conn, rules=MasteryRules(passes_required=2))
    sid = eng.create_student("Asha")
    first = solve_current(eng, sid)
    assert not first.concept_mastered        # lesson still has an unpassed exercise
    assert solve_current(eng, sid).concept_mastered


def test_decide_status_rules():
    r = MasteryRules(passes_required=2, weak_after_consecutive_failures=3)
    kw = dict(rules=r, required=2)
    assert decide_status(passes=0, fails=0, lessons_done=False, consecutive_failures=0, **kw) == "not_started"
    assert decide_status(passes=1, fails=0, lessons_done=False, consecutive_failures=0, **kw) == "in_progress"
    assert decide_status(passes=1, fails=3, lessons_done=False, consecutive_failures=3, **kw) == "weak"
    assert decide_status(passes=2, fails=0, lessons_done=False, consecutive_failures=0, **kw) == "in_progress"
    assert decide_status(passes=2, fails=0, lessons_done=True, consecutive_failures=0, **kw) == "mastered"


def test_weak_concept_flag_and_recovery(engine):
    sid = engine.create_student("Asha")
    for _ in range(3):
        res = engine.submit(sid, "c")
    assert res.concept_weak
    assert engine.progress_summary(sid)["weak_concepts"] == ["what-is-programming"]
    solve_current(engine, sid)
    assert engine.progress_summary(sid)["weak_concepts"] == []


def test_walk_through_entire_starter_curriculum(engine):
    sid = engine.create_student("Asha")
    order = []
    while engine.get_current_lesson(sid):
        order.append(engine.get_current_lesson(sid)["id"])
        res = finish_lesson(engine, sid)
        assert res.lesson_completed
    assert order == ["prog-01", "python-01", "run-01", "print-01", "print-02"]
    p = engine.progress_summary(sid)
    assert p["percent"] == 100.0 and len(p["mastered_concepts"]) == 5
    with pytest.raises(NoActiveLessonError):
        engine.submit(sid, "x")


# ---------------------------------------------------------------- prerequisites
def test_cannot_jump_to_locked_lesson(engine):
    sid = engine.create_student("Asha")
    with pytest.raises(PrerequisiteError):
        engine.set_current_lesson(sid, "print-01")
    with pytest.raises(PrerequisiteError):
        engine.set_current_lesson(sid, engine.lesson_after("prog-01")["id"])
    assert engine.get_current_lesson(sid)["id"] == "prog-01"


def test_submit_is_refused_if_state_points_at_locked_lesson(engine, conn):
    sid = engine.create_student("Asha")
    conn.execute("UPDATE students SET current_lesson_id='print-01' WHERE id=?", (sid,))   # tampering
    conn.commit()
    with pytest.raises(PrerequisiteError):
        engine.submit(sid, 'print("Hello World")')
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_failed_attempts_never_unlock_next_lesson(engine):
    sid = engine.create_student("Asha")
    for _ in range(10):
        engine.submit(sid, "c")
    assert engine.get_current_lesson(sid)["id"] == "prog-01"
    assert engine.missing_prerequisites(sid, "python-01") == ["prog-01"]


def test_explicit_requires_overrides_default(conn):
    conn.execute("DELETE FROM lesson_prerequisites WHERE lesson_id IN ('python-01','run-01')")
    conn.execute("INSERT INTO lesson_prerequisites VALUES ('run-01','prog-01')")
    conn.execute("INSERT INTO lesson_prerequisites VALUES ('python-01','run-01')")
    conn.commit()
    eng = LearningEngine(conn, rules=MasteryRules())
    sid = eng.create_student("Asha")
    finish_lesson(eng, sid)                    # prog-01 done
    assert eng.get_current_lesson(sid)["id"] == "run-01"   # python-01 still locked behind run-01
    assert not eng.lesson_unlocked(sid, "python-01")


# ---------------------------------------------------------------- independence
def test_two_students_progress_independently(engine):
    a = engine.create_student("Student A")
    b = engine.create_student("Student B")
    for _ in range(3):                         # A completes three lessons
        finish_lesson(engine, a)
    engine.submit(b, "c")                      # B only fails once
    assert engine.get_current_lesson(a)["id"] == "print-01"
    assert engine.get_current_lesson(b)["id"] == "prog-01"
    pa, pb = engine.progress_summary(a), engine.progress_summary(b)
    assert pa["lessons_completed"] == 3 and pb["lessons_completed"] == 0
    assert pa["weak_concepts"] == [] and pb["total_attempts"] == 1
    assert len(pa["mastered_concepts"]) == 3 and pb["mastered_concepts"] == []
    assert engine.lesson_unlocked(a, "print-01") and not engine.lesson_unlocked(b, "print-01")


def test_code_exercises_end_to_end(engine):
    sid = engine.create_student("Asha")
    for _ in range(3):
        finish_lesson(engine, sid)             # reach print-01 (code exercises)
    bad = engine.submit(sid, 'Print("Hello World")')
    assert not bad.evaluation.passed and bad.evaluation.status == "runtime_error"
    good = engine.submit(sid, SOLUTIONS["print-01-ex1"])
    assert good.evaluation.passed and good.next_exercise["id"] == "print-01-ex2"
