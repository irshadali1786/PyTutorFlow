from app.database.database import SCHEMA_FILE, V2_STUDENT_COLUMNS, V3_STUDENT_COLUMNS, connect, init_db


def columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(students)")}


def test_fresh_database_is_v3():
    c = connect(":memory:")
    init_db(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    assert {name for name, _ in V2_STUDENT_COLUMNS} <= columns(c)
    assert {name for name, _ in V3_STUDENT_COLUMNS} <= columns(c)
    assert c.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 0


def test_migration_is_idempotent():
    c = connect(":memory:")
    init_db(c)
    init_db(c)
    init_db(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v1_database_is_upgraded_and_keeps_data():
    c = connect(":memory:")
    c.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))          # an old Step 2 database
    c.execute("INSERT INTO phases VALUES('p','P','',1)")
    c.execute("INSERT INTO modules VALUES('m','p','M',1)")
    c.execute("INSERT INTO topics VALUES('t','m','T',1)")
    c.execute("INSERT INTO concepts VALUES('c','t','C',1,NULL)")
    c.execute("INSERT INTO lessons VALUES('l1','c','L','x','',1,1)")
    c.execute("INSERT INTO students(name,current_lesson_id,last_activity_at,created_at) VALUES('Old','l1','2026-01-02T10:00:00','2026-01-01T09:00:00')")
    c.execute("INSERT INTO students(name,current_lesson_id,created_at) VALUES('Fresh','l1','2026-01-01T09:00:00')")
    c.commit()
    assert "join_code" not in columns(c)
    init_db(c)
    assert "join_code" in columns(c)
    rows = {r["name"]: r for r in c.execute("SELECT * FROM students")}
    assert rows["Old"]["lesson_delivered_at"] == "2026-01-02T10:00:00"     # already mid-lesson -> not locked out
    assert rows["Fresh"]["lesson_delivered_at"] is None                    # never started -> still to be delivered
    assert rows["Old"]["preferred_send_time"] == "08:00"
