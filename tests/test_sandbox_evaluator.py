from app.evaluator.evaluator import evaluate_exercise
from app.evaluator.sandbox import SandboxConfig, check_code, run_code

CFG = SandboxConfig(timeout_seconds=2, max_output_bytes=5000, allowed_modules=frozenset({"math"}))

HELLO = {"kind": "code",
         "test_cases": [{"stdin": "", "expected_output": "Hello World", "match": "exact"}],
         "passing_criteria": {"min_score": 100, "required_calls": {"print": 1}}}


def ev(code, ex=HELLO):
    return evaluate_exercise(ex, code, CFG)


def test_correct_code_passes():
    r = ev('print("Hello World")')
    assert r.passed and r.score == 100 and r.status == "passed"


def test_trailing_spaces_and_blank_lines_are_ignored_but_inner_ones_are_not():
    assert ev('print("Hello World   ")\nprint()').passed          # harmless trailing whitespace
    two_lines = {**HELLO, "test_cases": [{"stdin": "", "expected_output": "A\nB", "match": "exact"}]}
    assert not ev('print("A")\nprint()\nprint("B")', two_lines).passed  # blank line in the middle matters


def test_wrong_output_fails_and_shows_expected():
    r = ev('print("Hello")')
    assert not r.passed and r.status == "wrong_output" and "Hello World" in r.feedback


def test_case_mismatch_is_detected():
    r = ev('print("hello world")')
    assert r.mistakes[0]["type"] == "case_mismatch"


def test_print_with_capital_p_gives_helpful_message():
    r = ev('Print("Hello World")')
    assert r.status == "runtime_error" and "small letters" in r.feedback


def test_syntax_error_is_reported_without_running():
    r = ev('print("Hello World"')
    assert r.status == "syntax_error" and "line 1" in r.feedback


def test_empty_submission():
    assert ev("   ").status == "empty"


def test_required_call_missing():
    ex = dict(HELLO, test_cases=[{"stdin": "", "match": "non_empty"}])
    r = ev("x = 1", ex)
    assert not r.passed
    r2 = evaluate_exercise({**HELLO, "test_cases": [{"stdin": "", "expected_output": "", "match": "exact"}]}, "x = 1", CFG)
    assert r2.status == "missing_requirement"


def test_stdin_is_supported():
    ex = {"kind": "code", "test_cases": [{"stdin": "Asha\n", "expected_output": "Hi Asha", "match": "exact"}],
          "passing_criteria": {}}
    assert ev('name = input()\nprint("Hi " + name)', ex).passed


def test_quiz_answers():
    quiz = {"kind": "quiz", "test_cases": [{"match": "choice", "accepted": ["b"]}]}
    assert evaluate_exercise(quiz, " B) ").passed
    assert not evaluate_exercise(quiz, "a").passed
    assert evaluate_exercise(quiz, "").status == "empty"


# ---------------------------------------------------------------- security
def test_forbidden_imports_are_blocked():
    for code in ("import os", "import subprocess", "import shutil", "from os import path", "import sys",
                 "import socket", "__import__('os')"):
        assert check_code(code, CFG) is not None, code
    assert check_code("import math", CFG) is None


def test_dangerous_builtins_and_dunders_are_blocked():
    for code in ("open('x.txt','w')", "exec('1')", "eval('1')", "().__class__.__bases__",
                 "getattr(print, 'x')", "print.__globals__"):
        assert check_code(code, CFG) is not None, code


def test_blocked_code_is_never_executed(tmp_path):
    marker = tmp_path / "pwned.txt"
    r = ev(f"open(r'{marker}', 'w').write('x')\nprint('Hello World')")
    assert r.status == "blocked" and not marker.exists()


def test_infinite_loop_times_out():
    r = run_code("while True:\n    pass", cfg=CFG)
    assert r.status == "timeout" and r.duration < 6


def test_output_flood_is_stopped():
    r = run_code("while True:\n    print('x' * 1000)", cfg=CFG)
    assert r.status == "output_limit"
    assert len(r.stdout) <= CFG.max_output_bytes


def test_runtime_error_is_explained():
    r = ev("print(undefined_name)")
    assert r.status == "runtime_error" and "NameError" in r.feedback
