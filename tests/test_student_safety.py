"""Duplicate protection for students (names and Gmail addresses)."""
import pytest

from app.service.tutor import TutorService


def test_duplicate_name_is_rejected_case_insensitively(engine):
    engine.create_student("Mahi")
    with pytest.raises(ValueError, match="already exists"):
        engine.create_student("MAHI")
    assert len(engine.list_students()) == 1


def test_find_student_is_case_insensitive(engine):
    sid = engine.create_student("Mahi")
    assert engine.find_student(" mahi ")["id"] == sid
    assert engine.find_student("Nobody") is None


def test_duplicate_email_is_rejected_case_insensitively(conn):
    svc = TutorService(conn)
    svc.create_student_with_code("Asha", None, "asha@example.com")
    with pytest.raises(ValueError, match="already belongs"):
        svc.create_student_with_code("Ravi", None, "ASHA@example.com")
    assert [s["name"] for s in svc.engine.list_students()] == ["Asha"]    # no half-created Ravi


def test_set_email_cannot_steal_another_students_address(conn):
    svc = TutorService(conn)
    svc.create_student_with_code("Asha", None, "asha@example.com")
    ravi = svc.create_student_with_code("Ravi", None, "ravi@example.com")
    with pytest.raises(ValueError, match="already belongs"):
        svc.set_student_email(ravi["student_id"], "asha@example.com")
