"""Known-correct answers for the shipped starter curriculum (tests only - never sent to students)."""
SOLUTIONS = {
    "prog-01-ex1": "b", "prog-01-ex2": "a",
    "python-01-ex1": "b", "python-01-ex2": "a",
    "run-01-ex1": "b", "run-01-ex2": "a",
    "print-01-ex1": 'print("Hello World")',
    "print-01-ex2": 'print("Python is fun")',
    "print-02-ex1": 'print("Asha")',
    "print-02-ex2": 'print("Hello")\nprint("Python")',
}


def solve_current(engine, student_id):
    """Submit the correct answer for the student's current exercise."""
    ex = engine.get_current_exercise(student_id)
    return engine.submit(student_id, SOLUTIONS[ex["id"]])


def finish_lesson(engine, student_id):
    """Pass every exercise of the current lesson; returns the last SubmitResult."""
    lesson_id = engine.get_current_lesson(student_id)["id"]
    res = None
    while engine.get_current_lesson(student_id) and engine.get_current_lesson(student_id)["id"] == lesson_id:
        res = solve_current(engine, student_id)
    return res
