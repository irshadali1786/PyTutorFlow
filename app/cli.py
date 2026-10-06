"""Interactive command-line test flow (no Gmail or n8n needed).

An existing student opens the CLI -> receives lesson -> submits code -> gets evaluation ->
progress updates -> next lesson becomes available.
"""
from __future__ import annotations

import textwrap
from typing import Callable

from app.progress.engine import LearningEngine, NoActiveLessonError, PrerequisiteError

BANNER = """
==============================================
  PYTUTORFLOW - CLI test mode
==============================================
Commands (type them instead of an answer):
  /hint      get the next hint
  /lesson    show the lesson again
  /progress  show your progress
  /skip      try to jump to the next lesson (tests the prerequisite lock)
  /switch    change student
  /quit      exit
"""


def _line(out, char="-", n=46):
    out(char * n)


def show_lesson(engine: LearningEngine, lesson: dict, out: Callable = print) -> None:
    _line(out, "=")
    out(f"LESSON [{lesson['id']}]  {lesson['title']}")
    _line(out, "=")
    out(lesson["content"].rstrip())
    if lesson["example_code"].strip():
        out("\nExample code:")
        out(textwrap.indent(lesson["example_code"].rstrip(), "    "))


def show_exercise(exercise: dict, out: Callable = print) -> None:
    _line(out)
    out(f"EXERCISE [{exercise['id']}]  (difficulty {exercise['difficulty']})")
    out(exercise["question"].rstrip())
    if exercise["kind"] == "quiz":
        out("Send just a letter, e.g.  a")
    else:
        out("Type or paste your code. Finish with a line that says only: END")
    _line(out)


def show_progress(engine: LearningEngine, student_id: int, out: Callable = print) -> None:
    p = engine.progress_summary(student_id)
    _line(out)
    out(f"Progress for {p['name']}")
    out(f"  Current lesson   : {p['current_lesson'] or '(finished)'}")
    out(f"  Current exercise : {p['current_exercise'] or '-'}")
    out(f"  Lessons completed: {p['lessons_completed']}/{p['lessons_total']}  ({p['percent']}%)")
    out(f"  Mastered concepts: {', '.join(p['mastered_concepts']) or '-'}")
    out(f"  Weak concepts    : {', '.join(p['weak_concepts']) or '-'}")
    out(f"  Attempts / passed: {p['total_attempts']} / {p['exercises_passed']}")
    out(f"  Average score    : {p['average_score'] if p['average_score'] is not None else '-'}")
    out(f"  Streak (days)    : {p['streak_days']}")
    _line(out)


def _read_code(first_line: str, input_fn: Callable) -> str:
    lines = [first_line]
    while True:
        try:
            line = input_fn("")
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def _pick_student(engine: LearningEngine, input_fn: Callable, out: Callable, name: str | None) -> int | None:
    """Look up an EXISTING student. This function never creates one.

    Students are created only with the explicit command:
        python -m app add-student "Asha" --email asha@example.com
    """
    if not name:
        try:
            name = input_fn("Student name: ").strip()
        except EOFError:
            return None
    if not name:
        return None
    existing = engine.find_student(name)
    if existing:
        out(f"Welcome back, {existing['name']}!")
        return existing["id"]
    out(f"No student named '{name.strip()}'. Nothing was created.")
    known = [s["name"] for s in engine.list_students()]
    if known:
        out("Existing students: " + ", ".join(known))
    out(f'To create one: python -m app add-student "{name.strip()}" --email NAME@example.com')
    return None


def run_cli(engine: LearningEngine, input_fn: Callable = input, out: Callable = print,
            student_name: str | None = None) -> None:
    out(BANNER)
    student_id = _pick_student(engine, input_fn, out, student_name)
    if student_id is None:
        return
    shown_lesson = None

    while True:
        lesson = engine.get_current_lesson(student_id)
        if lesson is None:
            out("\nCongratulations! You finished every lesson that exists so far.")
            show_progress(engine, student_id, out)
            return
        if lesson["id"] != shown_lesson:
            show_lesson(engine, lesson, out)
            shown_lesson = lesson["id"]
        exercise = engine.get_current_exercise(student_id)
        show_exercise(exercise, out)

        try:
            raw = input_fn("> ")
        except EOFError:
            out("\n(input ended)")
            return
        cmd = raw.strip().lower()

        if cmd in ("/quit", "/exit"):
            out("Goodbye! Your progress is saved.")
            return
        if cmd == "/hint":
            out("HINT: " + (engine.get_hint(student_id) or "-"))
            continue
        if cmd == "/lesson":
            shown_lesson = None
            continue
        if cmd == "/progress":
            show_progress(engine, student_id, out)
            continue
        if cmd == "/switch":
            new_id = _pick_student(engine, input_fn, out, None)
            if new_id is not None:
                student_id, shown_lesson = new_id, None
            continue
        if cmd == "/skip":
            target = engine.lesson_after(lesson["id"])
            if target is None:
                out("There is no later lesson.")
                continue
            try:
                engine.set_current_lesson(student_id, target["id"])
            except PrerequisiteError as exc:
                out(f"LOCKED: {exc}")
            continue
        if cmd.startswith("/"):
            out("Unknown command.")
            continue

        answer = raw if exercise["kind"] == "quiz" else _read_code(raw, input_fn)
        try:
            res = engine.submit(student_id, answer)
        except (PrerequisiteError, NoActiveLessonError) as exc:
            out(f"Cannot submit: {exc}")
            continue

        ev = res.evaluation
        _line(out)
        out(f"RESULT: {'PASSED' if ev.passed else 'TRY AGAIN'}   score {ev.score:g}   (attempt {res.attempt_number})")
        out(ev.feedback)
        if res.concept_weak and not ev.passed:
            out("(This topic is marked WEAK for now. Use /hint and re-read the lesson.)")
        if res.concept_mastered:
            out("*** Concept mastered! ***")
        if res.lesson_completed:
            out("*** Lesson complete! ***")
            if res.next_lesson:
                out(f"Next lesson unlocked: [{res.next_lesson['id']}] {res.next_lesson['title']}")
        _line(out)
