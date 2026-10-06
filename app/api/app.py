"""FastAPI application factory.

Run locally:  python -m app serve      (binds to 127.0.0.1 only)
All endpoints except /health need the header  X-API-Key: <API_KEY from .env>
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from app import __version__, config
from app.api.schemas import DailyResultIn, EmailReplyOut, IncomingEmail, IncomingUpdate, NewStudent, ReplyOut, SetEmail
from app.curriculum.loader import CurriculumError, load_curriculum
from app.database.database import connect, init_db
from app.service.settings import ServiceSettings
from app.service.tutor import TutorService


def create_app(db_path=None, api_key: str | None = None, settings: ServiceSettings | None = None,
               clock=None) -> FastAPI:
    key = api_key if api_key is not None else config.API_KEY
    if not key:
        raise RuntimeError("API_KEY is not set. Copy .env.example to .env and set API_KEY to a long random string.")
    path = db_path if db_path is not None else config.DB_PATH
    settings = settings or ServiceSettings()

    boot = connect(path, check_same_thread=False)         # prepare DB + curriculum once at start-up
    init_db(boot)
    load_curriculum(boot)
    boot.close()

    app = FastAPI(title="PyTutorFlow API", version=__version__)

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if not x_api_key or not secrets.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    def get_service():
        conn = connect(path, check_same_thread=False)
        try:
            yield TutorService(conn, settings=settings, clock=clock)
        finally:
            conn.close()

    @app.get("/health")
    def health():
        try:
            conn = connect(path, check_same_thread=False)
            try:
                lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
                students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
            finally:
                conn.close()
            return {"ok": True, "version": __version__, "lessons": lessons, "students": students}
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail=f"database problem: {exc}")

    api = APIRouter(dependencies=[Depends(require_key)])

    # --------------------------------------------------------------- Gmail
    @api.post("/email/incoming", response_model=EmailReplyOut)
    def email_incoming(mail: IncomingEmail, svc: TutorService = Depends(get_service)):
        r = svc.handle_email(mail.sender, mail.message_id, mail.subject, mail.body)
        return EmailReplyOut(**asdict(r))

    # ------------------------------------------- inactive legacy Telegram endpoints (no n8n workflow calls these)
    @api.get("/bot/offset")
    def bot_offset(svc: TutorService = Depends(get_service)):
        return {"offset": svc.next_offset()}

    @api.post("/telegram/incoming", response_model=ReplyOut)
    def telegram_incoming(update: IncomingUpdate, svc: TutorService = Depends(get_service)):
        return ReplyOut(**asdict(svc.handle_incoming(update.model_dump())))

    # --------------------------------------------------------------- daily
    @api.get("/daily/due")
    def daily_due(svc: TutorService = Depends(get_service)):
        students = svc.due_students(email_only=True)
        return {"count": len(students), "students": students}

    def _student_or_404(svc: TutorService, student_id: int) -> None:
        try:
            svc.engine.get_student(student_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No student with id {student_id}")

    @api.post("/students/{student_id}/daily-message")
    def daily_message(student_id: int, svc: TutorService = Depends(get_service)):
        _student_or_404(svc, student_id)
        return asdict(svc.build_daily_message(student_id))

    @api.post("/students/{student_id}/daily-result")
    def daily_result(student_id: int, body: DailyResultIn, svc: TutorService = Depends(get_service)):
        _student_or_404(svc, student_id)
        return svc.record_daily_result(student_id, body.delivered, body.error)

    # --------------------------------------------------------------- admin
    @api.post("/admin/students", status_code=201)
    def admin_add_student(body: NewStudent, svc: TutorService = Depends(get_service)):
        try:
            return svc.create_student_with_code(body.name, body.send_time, body.email)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @api.post("/admin/students/{student_id}/email")
    def admin_set_email(student_id: int, body: SetEmail, svc: TutorService = Depends(get_service)):
        _student_or_404(svc, student_id)
        try:
            return svc.set_student_email(student_id, body.email)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @api.get("/admin/students")
    def admin_students(svc: TutorService = Depends(get_service)):
        return {"students": svc.students_overview()}

    @api.post("/admin/students/{student_id}/join-code")
    def admin_new_code(student_id: int, svc: TutorService = Depends(get_service)):
        _student_or_404(svc, student_id)
        return svc.regenerate_join_code(student_id)

    @api.post("/admin/curriculum/reload")
    def admin_reload(svc: TutorService = Depends(get_service)):
        try:
            return {"ok": True, "counts": load_curriculum(svc.conn)}
        except CurriculumError as exc:
            raise HTTPException(status_code=422, detail=str(exc))     # old curriculum stays live

    @api.post("/admin/backup")
    def admin_backup(svc: TutorService = Depends(get_service)):
        if str(path) == ":memory:":
            raise HTTPException(status_code=400, detail="cannot back up an in-memory database")
        folder = Path(config.BACKUP_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"learning-{datetime.now():%Y%m%d-%H%M%S}.db"
        dest = sqlite3.connect(str(target))
        try:
            svc.conn.backup(dest)
        finally:
            dest.close()
        for old in sorted(folder.glob("learning-*.db"))[:-config.BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        return {"ok": True, "file": str(target)}

    app.include_router(api)
    return app
