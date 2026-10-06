from app.cli import run_cli


def run_script(engine, lines, student="Asha"):
    feed = iter(lines)
    out = []

    def fake_input(prompt=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    run_cli(engine, input_fn=fake_input, out=out.append, student_name=student)
    return "\n".join(out)


def test_cli_full_flow(engine):
    engine.create_student("Asha")
    script = [
        "c",                                   # wrong answer
        "/hint",
        "b", "a",                              # finish prog-01
        "/skip",                               # prerequisite lock demo (python-01 is already current, so skip target is run-01)
        "b", "a",                              # python-01
        "b", "a",                              # run-01
        'print("Hello World")', "END",         # print-01-ex1
        'print("Python is fun")', "END",       # print-01-ex2
        "/progress",
        "/quit",
    ]
    text = run_script(engine, script)
    assert "TRY AGAIN" in text and "HINT:" in text
    assert "Concept mastered" in text and "Lesson complete" in text
    assert "LOCKED:" in text
    assert "Next lesson unlocked: [print-02]" in text
    assert "Lessons completed: 4/5" in text


def test_cli_resumes_existing_student(engine):
    engine.create_student("Asha")
    run_script(engine, ["b", "/quit"])
    text = run_script(engine, ["/quit"])
    assert "Welcome back" in text and "prog-01-ex2" in text


def test_cli_uses_existing_student_and_creates_no_duplicate(engine):
    engine.create_student("Mahi")
    text = run_script(engine, ["/quit"], student="Mahi")
    assert "Welcome back, Mahi" in text
    assert "New student created" not in text
    assert len(engine.list_students()) == 1


def test_cli_lookup_is_case_insensitive_and_ignores_spaces(engine):
    engine.create_student("Mahi")
    text = run_script(engine, ["/quit"], student="  mAHI ")
    assert "Welcome back, Mahi" in text
    assert len(engine.list_students()) == 1


def test_cli_never_creates_unknown_student(engine):
    engine.create_student("Asha")
    text = run_script(engine, [], student="Ghost")
    assert "No student named 'Ghost'" in text
    assert "add-student" in text and "Asha" in text
    assert "New student created" not in text
    assert [s["name"] for s in engine.list_students()] == ["Asha"]


def test_cli_prompted_name_for_unknown_student_is_not_created(engine):
    text = run_script(engine, ["Nobody"], student=None)
    assert "Nothing was created" in text
    assert engine.list_students() == []
