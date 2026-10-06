import pytest

from app import config
from app.curriculum.loader import CurriculumError, load_curriculum
from app.database.database import connect, init_db


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_curriculum_loads_full_hierarchy(conn):
    assert count(conn, "phases") == 1
    assert count(conn, "modules") == 2
    assert count(conn, "topics") == 3
    assert count(conn, "concepts") == 5
    assert count(conn, "lessons") == 5
    assert count(conn, "exercises") == 10


def test_lessons_are_ordered_globally(conn):
    ids = [r[0] for r in conn.execute("SELECT id FROM lessons ORDER BY seq")]
    assert ids == ["prog-01", "python-01", "run-01", "print-01", "print-02"]


def test_default_prerequisite_is_previous_lesson(conn):
    rows = conn.execute("SELECT lesson_id, requires_lesson_id FROM lesson_prerequisites ORDER BY lesson_id").fetchall()
    pairs = {(r[0], r[1]) for r in rows}
    assert ("print-01", "run-01") in pairs
    assert not any(r[0] == "prog-01" for r in rows)


def test_loading_twice_changes_nothing(conn):
    before = [count(conn, t) for t in ("lessons", "exercises", "lesson_prerequisites")]
    load_curriculum(conn, config.CURRICULUM_DIR)
    assert before == [count(conn, t) for t in ("lessons", "exercises", "lesson_prerequisites")]


def test_bad_yaml_gives_friendly_error(tmp_path):
    (tmp_path / "bad.yaml").write_text("phase:\n  id: x\n", encoding="utf-8")
    c = connect(":memory:")
    init_db(c)
    with pytest.raises(CurriculumError) as err:
        load_curriculum(c, tmp_path)
    assert "title" in str(err.value)


def test_unknown_prerequisite_is_rejected(tmp_path):
    (tmp_path / "p.yaml").write_text("""
phase:
  id: p
  title: P
  modules:
    - id: m
      title: M
      topics:
        - id: t
          title: T
          concepts:
            - id: c
              title: C
              lessons:
                - id: l1
                  title: L
                  content: hi
                  requires: [ghost]
                  exercises:
                    - id: e1
                      question: q
                      test_cases: [{match: choice, accepted: [a]}]
                      kind: quiz
""", encoding="utf-8")
    c = connect(":memory:")
    init_db(c)
    with pytest.raises(CurriculumError):
        load_curriculum(c, tmp_path)


def test_circular_prerequisites_are_rejected(tmp_path):
    lesson = """
                - id: {id}
                  title: L
                  content: hi
                  requires: [{req}]
                  exercises:
                    - id: {id}-e
                      kind: quiz
                      question: q
                      test_cases: [{{match: choice, accepted: [a]}}]
"""
    (tmp_path / "p.yaml").write_text("""
phase:
  id: p
  title: P
  modules:
    - id: m
      title: M
      topics:
        - id: t
          title: T
          concepts:
            - id: c
              title: C
              lessons:""" + lesson.format(id="a", req="b") + lesson.format(id="b", req="a"), encoding="utf-8")
    c = connect(":memory:")
    init_db(c)
    with pytest.raises(CurriculumError) as err:
        load_curriculum(c, tmp_path)
    assert "circular" in str(err.value)
