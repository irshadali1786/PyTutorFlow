"""Entry point:  python -m app [init | cli | students | serve | add-student | set-email]"""
from __future__ import annotations

import argparse
import sys

from app import config
from app.cli import run_cli
from app.curriculum.loader import CurriculumError, load_curriculum
from app.database.database import connect, init_db
from app.progress.engine import LearningEngine


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):          # avoid Windows console encoding crashes
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="python -m app", description="PyTutorFlow - Gmail-based Python tutor (FastAPI + n8n + SQLite)")
    parser.add_argument("command", nargs="?", default="cli", choices=["init", "cli", "students", "serve", "add-student", "set-email"],
                        help="init = create DB + load curriculum | cli = interactive test for an EXISTING student | students = list progress | "
                             "serve = start the API for n8n | add-student NAME --email ADDR = create a student | "
                             "set-email NAME ADDR = attach a Gmail address to a student")
    parser.add_argument("--db", default=None, help=f"SQLite file (default: {config.DB_PATH})")
    parser.add_argument("name", nargs="?", default=None, help="student name (for add-student)")
    parser.add_argument("address", nargs="?", default=None, help="email address (for set-email)")
    parser.add_argument("--email", default=None, help="student's Gmail address for add-student")
    parser.add_argument("--student", default=None, help="student name for the cli command")
    parser.add_argument("--time", default=None, help="daily lesson time HH:MM for add-student (default 08:00)")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    init_db(conn)
    try:
        counts = load_curriculum(conn)
    except CurriculumError as exc:
        print(f"CURRICULUM ERROR: {exc}")
        return 2

    if args.command == "init":
        print(f"Database ready: {args.db or config.DB_PATH}")
        print("Curriculum loaded: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        return 0

    if args.command == "serve":
        if not config.API_KEY:
            print("API_KEY is not set. Copy .env.example to .env and put a long random string after API_KEY=")
            return 2
        conn.close()
        import uvicorn
        uvicorn.run("app.api.main:app", host=config.API_HOST, port=config.API_PORT)
        return 0

    if args.command == "add-student":
        from app.service.tutor import TutorService
        if not args.name:
            print('Usage: python -m app add-student "Asha" --email asha@example.com [--time 08:00]')
            return 2
        try:
            info = TutorService(conn).create_student_with_code(args.name, args.time, args.email)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"Student created: {info['name']} (id {info['student_id']})")
        if info.get("email"):
            print(f"Email: {info['email']}")
        else:
            print("No email given: attach one with  python -m app set-email \"NAME\" ADDRESS")
        print(info["instructions"])
        return 0

    if args.command == "set-email":
        from app.service.tutor import TutorService
        if not args.name or not args.address:
            print('Usage: python -m app set-email "Asha" asha@example.com')
            return 2
        student = LearningEngine(conn).find_student(args.name)
        if student is None:
            print(f"ERROR: no student named '{args.name}'. List them with: python -m app students")
            return 2
        try:
            info = TutorService(conn).set_student_email(student["id"], args.address)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"Email saved: {info['name']} -> {info['email']}")
        return 0

    engine = LearningEngine(conn)
    if args.command == "students":
        students = engine.list_students()
        if not students:
            print("No students yet. Create one: python -m app add-student \"Asha\" --email asha@example.com")
        for s in students:
            p = engine.progress_summary(s["id"])
            print(f"{p['name']:<15} lesson={p['current_lesson'] or 'DONE':<10} "
                  f"{p['lessons_completed']}/{p['lessons_total']} ({p['percent']}%) "
                  f"mastered={len(p['mastered_concepts'])} weak={len(p['weak_concepts'])} streak={p['streak_days']}")
        return 0

    print(f"Using database: {args.db or config.DB_PATH}")
    run_cli(engine, student_name=args.student)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
