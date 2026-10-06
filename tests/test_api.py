import itertools

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.service.tutor import reset_throttles
from tests.solutions import SOLUTIONS

KEY = {"X-API-Key": "test-key"}
_ids = itertools.count(1)


@pytest.fixture
def client(tmp_path, monkeypatch):
    reset_throttles()
    monkeypatch.setattr("app.config.BACKUP_DIR", tmp_path / "backups")
    return TestClient(create_app(db_path=tmp_path / "api.db", api_key="test-key"))


def incoming(client, chat_id, text):
    r = client.post("/telegram/incoming", headers=KEY,
                    json={"update_id": next(_ids), "chat_id": chat_id, "from_id": chat_id, "username": "u", "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_is_open_and_other_endpoints_need_key(client):
    assert client.get("/health").json()["ok"] is True
    for method, url in (("get", "/bot/offset"), ("get", "/daily/due"), ("get", "/admin/students")):
        assert getattr(client, method)(url).status_code == 401
        assert getattr(client, method)(url, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/telegram/incoming", json={"update_id": 1, "chat_id": 1, "text": "x"}).status_code == 401


def test_app_refuses_to_start_without_api_key(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(db_path=tmp_path / "x.db", api_key="")


def test_full_student_journey_over_http(client):
    created = client.post("/admin/students", headers=KEY, json={"name": "Asha", "send_time": "08:00"})
    assert created.status_code == 201
    code = created.json()["join_code"]
    assert client.post("/admin/students", headers=KEY, json={"name": "asha"}).status_code == 409

    start_id = next(_ids)
    r = client.post("/telegram/incoming", headers=KEY, json={"update_id": start_id, "chat_id": 111, "text": f"/start {code}"})
    assert r.json()["outcome"] == "COMMAND"
    assert client.get("/bot/offset", headers=KEY).json()["offset"] == start_id + 1      # Telegram offset = last id + 1

    first = incoming(client, 111, "/next")
    assert "LESSON 1 of 5" in first["messages"][0]
    assert incoming(client, 111, "c")["outcome"] == "RETRY"
    assert incoming(client, 111, "b")["outcome"] == "PASS"
    mastered = incoming(client, 111, "a")
    assert mastered["outcome"] == "MASTERED" and mastered["admin_alert"]

    overview = client.get("/admin/students", headers=KEY).json()["students"][0]
    assert overview["lessons_completed"] == 1 and overview["current_lesson"] == "python-01"


def test_duplicate_update_over_http(client):
    code = client.post("/admin/students", headers=KEY, json={"name": "Asha"}).json()["join_code"]
    body = {"update_id": 777, "chat_id": 5, "text": f"/start {code}"}
    assert client.post("/telegram/incoming", headers=KEY, json=body).json()["outcome"] == "COMMAND"
    again = client.post("/telegram/incoming", headers=KEY, json=body).json()
    assert again["outcome"] == "DUPLICATE" and again["messages"] == []


def test_n8n_style_string_numbers_and_missing_text_are_accepted(client):
    r = client.post("/telegram/incoming", headers=KEY, json={"update_id": "900", "chat_id": "42", "text": None})
    assert r.status_code == 200 and r.json()["outcome"] == "UNKNOWN_USER"


def test_daily_endpoints(client):
    sid = client.post("/admin/students", headers=KEY, json={"name": "Asha", "send_time": "00:00"}).json()["student_id"]
    assert client.get("/daily/due", headers=KEY).json()["count"] == 0          # no email yet
    assert client.post(f"/admin/students/{sid}/email", headers=KEY, json={"email": "Asha@example.com"}).json()["email"] == "asha@example.com"
    due = client.get("/daily/due", headers=KEY).json()
    assert due["count"] == 1 and due["students"][0]["email"] == "asha@example.com"   # send_time 00:00 -> always past
    msg = client.post(f"/students/{sid}/daily-message", headers=KEY).json()
    assert msg["kind"] == "LESSON" and msg["email"] == "asha@example.com" and msg["subject"] and msg["body"]
    ok = client.post(f"/students/{sid}/daily-result", headers=KEY, json={"delivered": True})
    assert ok.json()["ok"] is True
    assert client.get("/daily/due", headers=KEY).json()["count"] == 0
    r = client.post("/email/incoming", headers=KEY, json={"sender": "Asha <asha@example.com>", "message_id": "m-1",
                                                           "subject": "Re: Python lesson", "body": "b"})
    assert r.json()["outcome"] == "PASS" and r.json()["send"] is True and r.json()["to"] == "asha@example.com"
    assert client.post(f"/students/999/daily-message", headers=KEY).status_code == 404
    assert client.post("/students/999/daily-result", headers=KEY, json={"delivered": True}).status_code == 404


def test_curriculum_reload_and_backup(client, tmp_path):
    r = client.post("/admin/curriculum/reload", headers=KEY)
    assert r.json()["counts"]["lessons"] == 5
    b = client.post("/admin/backup", headers=KEY).json()
    assert b["ok"] and (tmp_path / "backups").exists() and len(list((tmp_path / "backups").glob("*.db"))) == 1
