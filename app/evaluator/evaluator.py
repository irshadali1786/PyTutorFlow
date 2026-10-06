"""Deterministic (rule-based) evaluation of an exercise submission. No AI involved.

evaluate_exercise(exercise, answer) -> EvaluationResult
  * quiz exercises : answer text is compared with the accepted answers.
  * code exercises : code is checked, run in the sandbox against each test case,
                     and the output is compared. Pass/fail is decided ONLY by rules.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from app.evaluator.sandbox import SandboxConfig, check_code, run_code

ERROR_HELP = {
    "SyntaxError": "Python could not understand how a line is written. Check brackets ( ), quotes \" \" and spelling.",
    "IndentationError": "Lines must start at the very left edge here. Remove the extra spaces at the start of the line.",
    "NameError": "Python does not know a word you used. Text must be inside quotes, and spelling/capital letters must match.",
    "TypeError": "You mixed two things that do not fit together (for example text and a number).",
    "ValueError": "A value was the right kind of thing but had a wrong content.",
    "ZeroDivisionError": "You divided a number by zero, which is not possible.",
    "IndexError": "You asked for a position that does not exist.",
    "KeyError": "You asked for a key that does not exist.",
    "AttributeError": "You used a name that does not belong to that thing.",
    "EOFError": "Your program asked for input but none was available.",
    "ModuleNotFoundError": "Python could not find that module.",
}


@dataclass
class EvaluationResult:
    passed: bool
    score: float
    status: str          # passed | wrong_output | wrong_answer | syntax_error | runtime_error | timeout | ...
    feedback: str
    mistakes: list[dict] = field(default_factory=list)   # [{"type":..., "message":...}]
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def _short(text: str, n: int = 300) -> str:
    return text if len(text) <= n else text[:n] + " ..."


def _fail(status: str, feedback: str, mistake_type: str, score: float = 0.0, details: dict | None = None) -> EvaluationResult:
    return EvaluationResult(False, score, status, feedback, [{"type": mistake_type, "message": feedback}], details or {})


def explain_error(stderr: str) -> tuple[str, str]:
    """Turn a Python traceback into (error_name, beginner-friendly message)."""
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    last = lines[-1] if lines else "UnknownError"
    name, _, detail = last.partition(":")
    name = name.strip().split(".")[-1]
    line_no = None
    for m in re.finditer(r'student_code\.py", line (\d+)', stderr):
        line_no = int(m.group(1))
    help_text = ERROR_HELP.get(name, "Python stopped because of an error in your program.")
    if name == "NameError":
        m = re.search(r"name '(\w+)' is not defined", detail)
        if m and m.group(1).lower() == "print" and m.group(1) != "print":
            help_text = "Python is case-sensitive. Write print in small letters: print(...)"
    where = f" (line {line_no})" if line_no else ""
    return name, f"Python reported {name}{where}: {detail.strip()}\n{help_text}".strip()


def _count_calls(code: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            counts[node.func.id] = counts.get(node.func.id, 0) + 1
    return counts


def _compare(case: dict, stdout: str) -> tuple[bool, str, str]:
    """Return (ok, mistake_type, message)."""
    match = case.get("match", "exact")
    got = _norm(stdout)
    exp = _norm(case.get("expected_output", ""))
    if match == "non_empty":
        ok = bool(got.strip())
    elif match == "contains":
        ok = exp in got
    else:
        ok = got == exp
    if ok:
        return True, "", ""
    if not got.strip():
        return False, "no_output", "Your program printed nothing. Did you use print()?"
    if match == "exact" and got.lower() == exp.lower():
        return False, "case_mismatch", "Very close! The letters are right, but capital/small letters differ.\n" \
                                       f"Expected: {_short(exp)}\nYou printed: {_short(got)}"
    if match == "exact" and "".join(got.split()) == "".join(exp.split()):
        return False, "whitespace_mismatch", "Very close! The text is right, but spaces or line breaks differ.\n" \
                                             f"Expected: {_short(exp)}\nYou printed: {_short(got)}"
    return False, "wrong_output", f"The output is not what the task asks for.\nExpected: {_short(exp)}\nYou printed: {_short(got)}"


# ----------------------------------------------------------------- evaluators
def _evaluate_quiz(exercise: dict, answer: str) -> EvaluationResult:
    given = (answer or "").strip().lower().rstrip(".)").lstrip("(")
    if not given:
        return _fail("empty", "You sent an empty answer. Send a letter such as a, b or c.", "empty_submission")
    accepted = {a for case in exercise["test_cases"] for a in case.get("accepted", [])}
    if given in accepted:
        return EvaluationResult(True, 100.0, "passed", "Correct! Well done.")
    return _fail("wrong_answer", "Not quite. Read the lesson once more and try again. You can ask for a hint.", "wrong_answer")


def _evaluate_code(exercise: dict, answer: str, cfg: SandboxConfig | None) -> EvaluationResult:
    code = answer or ""
    if not code.strip():
        return _fail("empty", "You sent an empty answer. Type your code and send it again.", "empty_submission")
    cfg = cfg or SandboxConfig.from_env()

    violation = check_code(code, cfg)
    if violation:
        if violation.kind == "syntax_error":
            help_text = ERROR_HELP.get(violation.error_name, ERROR_HELP["SyntaxError"])
            where = f" (line {violation.line})" if violation.line else ""
            return _fail("syntax_error", f"Python found a problem before running your code{where}: {violation.message}\n{help_text}", "syntax_error")
        return _fail("blocked", f"{violation.message} Use only what the lessons taught you.", violation.kind)

    criteria = exercise.get("passing_criteria") or {}
    min_score = float(criteria.get("min_score", 100))
    cases = exercise["test_cases"]
    passed_cases = 0
    details = {"cases": []}
    first_problem: tuple[str, str] | None = None

    for i, case in enumerate(cases, 1):
        run = run_code(code, case.get("stdin", ""), cfg)
        if run.status == "timeout":
            return _fail("timeout", f"Your program ran for more than {cfg.timeout_seconds:g} seconds. It may be stuck in a loop that never ends.", "timeout", _score(passed_cases, cases), details)
        if run.status == "output_limit":
            return _fail("output_limit", "Your program printed far too much text. Check for a loop that never stops.", "output_limit", _score(passed_cases, cases), details)
        if run.status == "runtime_error":
            name, msg = explain_error(run.stderr)
            return _fail("runtime_error", msg, name or "runtime_error", _score(passed_cases, cases), details)
        ok, mtype, msg = _compare(case, run.stdout)
        details["cases"].append({"case": i, "passed": ok})
        if ok:
            passed_cases += 1
        elif first_problem is None:
            first_problem = (mtype, msg)

    score = _score(passed_cases, cases)
    details["passed_cases"], details["total_cases"] = passed_cases, len(cases)

    missing = [f"{fn}() at least {n} time(s)" for fn, n in (criteria.get("required_calls") or {}).items()
               if _count_calls(code).get(fn, 0) < int(n)]
    if missing:
        return _fail("missing_requirement", "Your program must use " + ", ".join(missing) + ".", "missing_requirement", score, details)

    if score >= min_score:
        return EvaluationResult(True, score, "passed", "Correct! Your program works.", [], details)
    mtype, msg = first_problem or ("wrong_output", "The output is not correct.")
    return _fail("wrong_output", msg, mtype, score, details)


def _score(passed: int, cases: list) -> float:
    return round(100.0 * passed / len(cases), 1) if cases else 0.0


def evaluate_exercise(exercise: dict, answer: str, sandbox_cfg: SandboxConfig | None = None) -> EvaluationResult:
    if exercise["kind"] == "quiz":
        return _evaluate_quiz(exercise, answer)
    return _evaluate_code(exercise, answer, sandbox_cfg)
