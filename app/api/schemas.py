"""Request/response shapes. Kept flat and simple so n8n expressions stay short."""
from __future__ import annotations

from pydantic import BaseModel, Field


class IncomingUpdate(BaseModel):
    update_id: int
    chat_id: int
    from_id: int | None = None
    username: str | None = None
    text: str | None = None
    message_id: int | None = None
    date: int | None = None


class ReplyOut(BaseModel):
    outcome: str
    chat_id: int | None = None
    messages: list[str] = Field(default_factory=list)
    admin_alert: str | None = None


class DailyResultIn(BaseModel):
    delivered: bool
    error: str | None = None


class NewStudent(BaseModel):
    name: str
    send_time: str | None = None
    email: str | None = None


class SetEmail(BaseModel):
    email: str


class IncomingEmail(BaseModel):
    """What n8n sends for ONE Gmail message."""
    sender: str
    message_id: str
    subject: str | None = None
    body: str | None = None


class EmailReplyOut(BaseModel):
    outcome: str
    send: bool = False
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    admin_alert: str | None = None
