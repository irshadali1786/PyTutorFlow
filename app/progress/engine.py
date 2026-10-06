"""The learning engine.

Student -> current lesson -> current exercise -> submission -> evaluation
        -> save result -> update progress -> decide next lesson.

Rules enforced here (never skipped):
  * A lesson is served only if ALL its prerequisite lessons are completed.
  * A lesson is completed only when ALL its exercises are passed.
  * A concept is mastered only by the (configurable) mastery rules.
  * Submitting something never moves a student forward by itself - only a pass does.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.evaluator.evaluator import EvaluationResult, evaluate_exercise
from app.progress.mastery import (MasteryRules, decide_status, load_rules,
                                  mastery_percent, required_passes)


class PrerequisiteError(Exception):
    """The student tried to open or submit work for a lesson that is still locked."""


class NoActiveLessonError(Exception):
    """The student has no current lesson (curriculum finished)."""


@dataclass
class SubmitResult:
    evaluation: EvaluationResult
    attempt_number: int
    lesson_completed: bool = False
    concept_mastered: bool = False
    concept_weak: bool = False
    next_lesson: dict | None = None
    next_exercise: dict | None = None
    curriculum_finished: bool = False


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class LearningEngine:
    def __init__(self, conn: sqlite3.Connection, rules: MasteryRules | None = None,
                 clock: Callable[[], datetime] | None = None,
                 evaluator: Callable[[dict, str], EvaluationResult] | None = None):
        self.conn = conn
        self.rules = rules or load_rules()
        self.clock = clock or datetime.now
        self.evaluator = evaluator or evaluate_exercise

    # ------------------------------------------------------------ students
    def create_student(self, name: str, telegram_chat_id: int | None = None) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Student name cannot be empty")
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO students(name, telegram_chat_id, created_at) VALUES(?,?,?)",
                    (name, telegram_chat_id, _stamp(self.clock())))
                student_id = cur.lastrowid
                first = self._next_lesson_for(student_id)
                if first is None:
                    raise ValueError("The curriculum is empty. Run: python -m app init")
                self.conn.execute("UPDATE students SET current_lesson_id=? WHERE id=?", (first["id"], student_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A student named '{name}' already exists (names are case-insensitive)") from exc
        return student_id

    def get_student(self, student_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if row is None:
            raise KeyError(f"No student with id {student_id}")
        return dict(row)

    def find_student(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM students WHERE name=? COLLATE NOCASE", ((name or "").strip(),)).fetchone()
        return dict(row) if row else None

    def list_students(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM students ORDER BY id")]

    # ------------------------------------------------------- prerequisites
    def missing_prerequisites(self, student_id: int, lesson_id: str) -> list[str]:
        rows = self.conn.execute(
            """SELECT p.requires_lesson_id FROM lesson_prerequisites p
               LEFT JOIN lesson_progress lp ON lp.lesson_id = p.requires_lesson_id
                    AND lp.student_id = ? AND lp.status = 'completed'
               WHERE p.lesson_id = ? AND lp.lesson_id IS NULL
               ORDER BY p.requires_lesson_id""", (student_id, lesson_id)).fetchall()
        return [r[0] for r in rows]

    def lesson_unlocked(self, student_id: int, lesson_id: str) -> bool:
        return not self.missing_prerequisites(student_id, lesson_id)

    def _next_lesson_for(self, student_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT l.* FROM lessons l
               LEFT JOIN lesson_progress lp ON lp.lesson_id = l.id AND lp.student_id = ? AND lp.status = 'completed'
               WHERE lp.lesson_id IS NULL
                 AND NOT EXISTS (
                      SELECT 1 FROM lesson_prerequisites p
                      LEFT JOIN lesson_progress pp ON pp.lesson_id = p.requires_lesson_id
                           AND pp.student_id = ? AND pp.status = 'completed'
                      WHERE p.lesson_id = l.id AND pp.lesson_id IS NULL)
               ORDER BY l.seq LIMIT 1""", (student_id, student_id)).fetchone()
        return dict(row) if row else None

    def lesson_after(self, lesson_id: str) -> dict | None:
        """The lesson that follows in the global order (used to test skipping)."""
        row = self.conn.execute(
            "SELECT * FROM lessons WHERE seq > (SELECT seq FROM lessons WHERE id=?) ORDER BY seq LIMIT 1",
            (lesson_id,)).fetchone()
        return dict(row) if row else None

    def set_current_lesson(self, student_id: int, lesson_id: str) -> dict:
        """Manually jump to a lesson. Refused (PrerequisiteError) if it is locked."""
        lesson = self.conn.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        if lesson is None:
            raise KeyError(f"No lesson '{lesson_id}'")
        done = self.conn.execute(
            "SELECT 1 FROM lesson_progress WHERE student_id=? AND lesson_id=? AND status='completed'",
            (student_id, lesson_id)).fetchone()
        if done:
            raise ValueError(f"Lesson '{lesson_id}' is already completed")
        missing = self.missing_prerequisites(student_id, lesson_id)
        if missing:
            raise PrerequisiteError(f"Lesson '{lesson_id}' is locked. Finish first: {', '.join(missing)}")
        with self.conn:
            self.conn.execute("UPDATE students SET current_lesson_id=?, current_exercise_id=NULL WHERE id=?",
                              (lesson_id, student_id))
        return dict(lesson)

    # ----------------------------------------------------- current lesson
    def get_current_lesson(self, student_id: int) -> dict | None:
        lesson_id = self.get_student(student_id)["current_lesson_id"]
        if not lesson_id:
            return None
        lesson = self.conn.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        return dict(lesson) if lesson else None

    @staticmethod
    def _exercise_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["test_cases"] = json.loads(d["test_cases"])
        d["hints"] = json.loads(d["hints"])
        d["passing_criteria"] = json.loads(d["passing_criteria"])
        return d

    def get_current_exercise(self, student_id: int) -> dict | None:
        student = self.get_student(student_id)
        if not student["current_lesson_id"]:
            return None
        row = self.conn.execute(
            """SELECT e.* FROM exercises e
               LEFT JOIN attempts a ON a.exercise_id = e.id AND a.student_id = ?
               WHERE e.lesson_id = ? AND COALESCE(a.passed, 0) = 0
               ORDER BY e.position LIMIT 1""", (student_id, student["current_lesson_id"])).fetchone()
        new_id = row["id"] if row else None
        if new_id != student["current_exercise_id"]:
            with self.conn:
                self.conn.execute("UPDATE students SET current_exercise_id=? WHERE id=?", (new_id, student_id))
        return self._exercise_dict(row) if row else None

    def get_hint(self, student_id: int) -> str | None:
        exercise = self.get_current_exercise(student_id)
        if exercise is None:
            return None
        hints = exercise["hints"]
        if not hints:
            return "No hint is available for this exercise. Read the lesson again."
        row = self.conn.execute("SELECT hints_used FROM attempts WHERE student_id=? AND exercise_id=?",
                                (student_id, exercise["id"])).fetchone()
        used = row["hints_used"] if row else 0
        if used < len(hints):
            with self.conn:
                self.conn.execute(
                    """INSERT INTO attempts(student_id, exercise_id, attempt_count, hints_used, passed, best_score, last_attempt_at)
                       VALUES(?,?,0,1,0,0,?)
                       ON CONFLICT(student_id, exercise_id) DO UPDATE SET hints_used = hints_used + 1""",
                    (student_id, exercise["id"], _stamp(self.clock())))
        return hints[min(used, len(hints) - 1)]

    # -------------------------------------------------------------- submit
    def submit(self, student_id: int, answer: str) -> SubmitResult:
        student = self.get_student(student_id)
        lesson = self.get_current_lesson(student_id)
        if lesson is None:
            raise NoActiveLessonError("No active lesson - the curriculum is finished.")
        missing = self.missing_prerequisites(student_id, lesson["id"])
        if missing:
            raise PrerequisiteError(f"Lesson '{lesson['id']}' is locked. Finish first: {', '.join(missing)}")
        exercise = self.get_current_exercise(student_id)
        if exercise is None:
            raise RuntimeError(f"Inconsistent state: lesson '{lesson['id']}' has no unpassed exercise")

        result = self.evaluator(exercise, answer)          # may take a moment (runs code)
        now = self.clock()
        stamp = _stamp(now)
        concept_id = exercise["concept_id"]
        rules = self._rules_for(concept_id)
        lesson_completed = concept_mastered = False
        concept_weak = False

        with self.conn:
            prev = self.conn.execute("SELECT attempt_count FROM attempts WHERE student_id=? AND exercise_id=?",
                                     (student_id, exercise["id"])).fetchone()
            attempt_number = (prev["attempt_count"] if prev else 0) + 1
            self.conn.execute(
                """INSERT INTO attempts(student_id, exercise_id, attempt_count, hints_used, passed, best_score,
                                        first_passed_at, last_attempt_at)
                   VALUES(?,?,1,0,?,?,?,?)
                   ON CONFLICT(student_id, exercise_id) DO UPDATE SET
                     attempt_count = attempt_count + 1,
                     passed = MAX(attempts.passed, excluded.passed),
                     best_score = MAX(attempts.best_score, excluded.best_score),
                     first_passed_at = COALESCE(attempts.first_passed_at, excluded.first_passed_at),
                     last_attempt_at = excluded.last_attempt_at""",
                (student_id, exercise["id"], int(result.passed), result.score,
                 stamp if result.passed else None, stamp))
            sub_id = self.conn.execute(
                "INSERT INTO submissions(student_id, exercise_id, attempt_number, code, submitted_at) VALUES(?,?,?,?,?)",
                (student_id, exercise["id"], attempt_number, answer or "", stamp)).lastrowid
            self.conn.execute(
                "INSERT INTO evaluations(submission_id, evaluator, passed, score, status, feedback, details, evaluated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (sub_id, "rule_based", int(result.passed), result.score, result.status, result.feedback,
                 json.dumps(result.details), stamp))
            for m in result.mistakes:
                self.conn.execute(
                    "INSERT INTO mistakes(student_id, submission_id, exercise_id, concept_id, error_type, message, created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (student_id, sub_id, exercise["id"], concept_id, m["type"], m["message"], stamp))

            self.conn.execute(
                "INSERT OR IGNORE INTO lesson_progress(student_id, lesson_id, status, started_at) VALUES(?,?, 'in_progress', ?)",
                (student_id, lesson["id"], stamp))
            self.conn.execute(
                "INSERT OR IGNORE INTO progress(student_id, concept_id, status, started_at, last_activity_at) "
                "VALUES(?,?, 'in_progress', ?, ?)", (student_id, concept_id, stamp, stamp))
            if result.passed:
                self.conn.execute(
                    "UPDATE progress SET passes = passes + 1, consecutive_failures = 0, "
                    "best_score = MAX(best_score, ?), last_activity_at = ? WHERE student_id=? AND concept_id=?",
                    (result.score, stamp, student_id, concept_id))
            else:
                self.conn.execute(
                    "UPDATE progress SET fails = fails + 1, consecutive_failures = consecutive_failures + 1, "
                    "best_score = MAX(best_score, ?), last_activity_at = ? WHERE student_id=? AND concept_id=?",
                    (result.score, stamp, student_id, concept_id))

            if result.passed:
                remaining = self.conn.execute(
                    """SELECT COUNT(*) FROM exercises e
                       LEFT JOIN attempts a ON a.exercise_id = e.id AND a.student_id = ?
                       WHERE e.lesson_id = ? AND COALESCE(a.passed,0) = 0""", (student_id, lesson["id"])).fetchone()[0]
                if remaining == 0:
                    lesson_completed = True
                    self.conn.execute(
                        "UPDATE lesson_progress SET status='completed', completed_at=? WHERE student_id=? AND lesson_id=?",
                        (stamp, student_id, lesson["id"]))

            prog = self.conn.execute("SELECT * FROM progress WHERE student_id=? AND concept_id=?",
                                     (student_id, concept_id)).fetchone()
            ex_count = self.conn.execute("SELECT COUNT(*) FROM exercises WHERE concept_id=?", (concept_id,)).fetchone()[0]
            unfinished = self.conn.execute(
                """SELECT COUNT(*) FROM lessons l
                   LEFT JOIN lesson_progress lp ON lp.lesson_id = l.id AND lp.student_id = ? AND lp.status='completed'
                   WHERE l.concept_id = ? AND lp.lesson_id IS NULL""", (student_id, concept_id)).fetchone()[0]
            required = required_passes(rules, ex_count)
            status = decide_status(passes=prog["passes"], fails=prog["fails"], required=required,
                                   lessons_done=(unfinished == 0), consecutive_failures=prog["consecutive_failures"],
                                   rules=rules)
            concept_mastered = status == "mastered" and prog["status"] != "mastered"
            concept_weak = status == "weak"
            self.conn.execute(
                "UPDATE progress SET status=?, mastery_score=?, mastered_at=COALESCE(mastered_at, ?) "
                "WHERE student_id=? AND concept_id=?",
                (status, mastery_percent(prog["passes"], required), stamp if status == "mastered" else None,
                 student_id, concept_id))

            today = now.date().isoformat()
            last = student["last_active_date"]
            if last == today:
                streak = student["streak_days"]
            elif last == (now.date() - timedelta(days=1)).isoformat():
                streak = student["streak_days"] + 1
            else:
                streak = 1
            self.conn.execute(
                "UPDATE students SET streak_days=?, last_active_date=?, last_activity_at=? WHERE id=?",
                (streak, today, stamp, student_id))

            next_lesson = None
            if lesson_completed:
                next_lesson = self._next_lesson_for(student_id)
                self.conn.execute("UPDATE students SET current_lesson_id=?, current_exercise_id=NULL WHERE id=?",
                                  (next_lesson["id"] if next_lesson else None, student_id))

        next_exercise = self.get_current_exercise(student_id)
        return SubmitResult(
            evaluation=result, attempt_number=attempt_number, lesson_completed=lesson_completed,
            concept_mastered=concept_mastered, concept_weak=concept_weak,
            next_lesson=next_lesson, next_exercise=next_exercise,
            curriculum_finished=lesson_completed and next_lesson is None)

    def _rules_for(self, concept_id: str) -> MasteryRules:
        row = self.conn.execute("SELECT mastery_rules FROM concepts WHERE id=?", (concept_id,)).fetchone()
        overrides = json.loads(row["mastery_rules"]) if row and row["mastery_rules"] else None
        return self.rules.merged(overrides)

    # ------------------------------------------------------------- reports
    def progress_summary(self, student_id: int) -> dict:
        student = self.get_student(student_id)
        total = self.conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        completed = [r[0] for r in self.conn.execute(
            "SELECT lesson_id FROM lesson_progress WHERE student_id=? AND status='completed' ORDER BY completed_at, lesson_id",
            (student_id,))]

        def concepts(status: str) -> list[str]:
            return [r[0] for r in self.conn.execute(
                "SELECT concept_id FROM progress WHERE student_id=? AND status=? ORDER BY concept_id",
                (student_id, status))]

        att = self.conn.execute(
            "SELECT COALESCE(SUM(attempt_count),0), COALESCE(SUM(passed),0) FROM attempts WHERE student_id=?",
            (student_id,)).fetchone()
        avg = self.conn.execute(
            "SELECT AVG(e.score) FROM evaluations e JOIN submissions s ON s.id = e.submission_id WHERE s.student_id=?",
            (student_id,)).fetchone()[0]
        return {
            "student_id": student_id, "name": student["name"],
            "current_lesson": student["current_lesson_id"], "current_exercise": student["current_exercise_id"],
            "lessons_completed": len(completed), "lessons_total": total,
            "percent": round(100.0 * len(completed) / total, 1) if total else 0.0,
            "completed_lessons": completed,
            "mastered_concepts": concepts("mastered"), "weak_concepts": concepts("weak"),
            "in_progress_concepts": concepts("in_progress"),
            "total_attempts": att[0], "exercises_passed": att[1],
            "average_score": round(avg, 1) if avg is not None else None,
            "streak_days": student["streak_days"], "last_activity_at": student["last_activity_at"],
        }
