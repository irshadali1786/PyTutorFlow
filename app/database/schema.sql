-- =====================================================================
-- PyTutorFlow schema (SQLite). Safe to run repeatedly.
-- Foreign keys are enforced (PRAGMA foreign_keys=ON is set per connection).
-- =====================================================================

-- ---------- CONTENT (shared by all students, loaded from YAML) --------
CREATE TABLE IF NOT EXISTS phases (
    id          TEXT PRIMARY KEY,                -- e.g. 'phase-01'
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
    id        TEXT PRIMARY KEY,
    phase_id  TEXT NOT NULL REFERENCES phases(id),
    title     TEXT NOT NULL,
    position  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_modules_phase ON modules(phase_id, position);

CREATE TABLE IF NOT EXISTS topics (
    id        TEXT PRIMARY KEY,
    module_id TEXT NOT NULL REFERENCES modules(id),
    title     TEXT NOT NULL,
    position  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_module ON topics(module_id, position);

CREATE TABLE IF NOT EXISTS concepts (
    id            TEXT PRIMARY KEY,              -- e.g. 'print-function'
    topic_id      TEXT NOT NULL REFERENCES topics(id),
    title         TEXT NOT NULL,
    position      INTEGER NOT NULL,
    mastery_rules TEXT                           -- JSON overrides, NULL = use defaults
);
CREATE INDEX IF NOT EXISTS idx_concepts_topic ON concepts(topic_id, position);

CREATE TABLE IF NOT EXISTS lessons (
    id           TEXT PRIMARY KEY,               -- e.g. 'print-01'
    concept_id   TEXT NOT NULL REFERENCES concepts(id),
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,                  -- the lesson text sent to the student
    example_code TEXT NOT NULL DEFAULT '',
    position     INTEGER NOT NULL,               -- order inside its concept
    seq          INTEGER NOT NULL                -- global order across the whole curriculum
);
CREATE INDEX IF NOT EXISTS idx_lessons_concept ON lessons(concept_id, position);
CREATE INDEX IF NOT EXISTS idx_lessons_seq ON lessons(seq);

-- A lesson is unlocked only when ALL its prerequisite lessons are completed.
CREATE TABLE IF NOT EXISTS lesson_prerequisites (
    lesson_id          TEXT NOT NULL REFERENCES lessons(id),
    requires_lesson_id TEXT NOT NULL REFERENCES lessons(id),
    PRIMARY KEY (lesson_id, requires_lesson_id)
);
CREATE INDEX IF NOT EXISTS idx_prereq_requires ON lesson_prerequisites(requires_lesson_id);

CREATE TABLE IF NOT EXISTS exercises (
    id                TEXT PRIMARY KEY,          -- e.g. 'print-01-ex1'
    lesson_id         TEXT NOT NULL REFERENCES lessons(id),
    concept_id        TEXT NOT NULL REFERENCES concepts(id),
    kind              TEXT NOT NULL CHECK (kind IN ('code', 'quiz')),
    question          TEXT NOT NULL,
    difficulty        INTEGER NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
    expected_behavior TEXT NOT NULL DEFAULT '',
    test_cases        TEXT NOT NULL,             -- JSON list
    hints             TEXT NOT NULL DEFAULT '[]',-- JSON list, shown one at a time
    passing_criteria  TEXT NOT NULL DEFAULT '{}',-- JSON object
    position          INTEGER NOT NULL           -- order inside its lesson
);
CREATE INDEX IF NOT EXISTS idx_exercises_lesson ON exercises(lesson_id, position);
CREATE INDEX IF NOT EXISTS idx_exercises_concept ON exercises(concept_id);

-- ---------- STUDENTS ---------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    telegram_chat_id    INTEGER UNIQUE,          -- filled in at the Telegram step
    is_active           INTEGER NOT NULL DEFAULT 1,
    current_lesson_id   TEXT REFERENCES lessons(id),    -- NULL = curriculum finished
    current_exercise_id TEXT REFERENCES exercises(id),
    streak_days         INTEGER NOT NULL DEFAULT 0,
    last_active_date    TEXT,                    -- YYYY-MM-DD, used for the streak
    last_activity_at    TEXT,
    created_at          TEXT NOT NULL
);

-- ---------- PER-STUDENT PROGRESS --------------------------------------
-- One row per (student, lesson): in_progress / completed
CREATE TABLE IF NOT EXISTS lesson_progress (
    student_id   INTEGER NOT NULL REFERENCES students(id),
    lesson_id    TEXT    NOT NULL REFERENCES lessons(id),
    status       TEXT    NOT NULL CHECK (status IN ('in_progress', 'completed')),
    started_at   TEXT    NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (student_id, lesson_id)
);
CREATE INDEX IF NOT EXISTS idx_lesson_progress_status ON lesson_progress(student_id, status);

-- One row per (student, concept): mastery tracking
CREATE TABLE IF NOT EXISTS progress (
    student_id           INTEGER NOT NULL REFERENCES students(id),
    concept_id           TEXT    NOT NULL REFERENCES concepts(id),
    status               TEXT    NOT NULL DEFAULT 'in_progress'
                         CHECK (status IN ('not_started', 'in_progress', 'weak', 'mastered')),
    passes               INTEGER NOT NULL DEFAULT 0,   -- distinct exercises passed
    fails                INTEGER NOT NULL DEFAULT 0,   -- total failed submissions
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    best_score           REAL    NOT NULL DEFAULT 0,
    mastery_score        REAL    NOT NULL DEFAULT 0,   -- 0-100, progress towards mastery
    started_at           TEXT,
    mastered_at          TEXT,
    last_activity_at     TEXT,
    PRIMARY KEY (student_id, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_status ON progress(student_id, status);

-- One row per (student, exercise): running totals
CREATE TABLE IF NOT EXISTS attempts (
    student_id       INTEGER NOT NULL REFERENCES students(id),
    exercise_id      TEXT    NOT NULL REFERENCES exercises(id),
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    hints_used       INTEGER NOT NULL DEFAULT 0,
    passed           INTEGER NOT NULL DEFAULT 0,
    best_score       REAL    NOT NULL DEFAULT 0,
    first_passed_at  TEXT,
    last_attempt_at  TEXT,
    PRIMARY KEY (student_id, exercise_id)
);

-- ---------- HISTORY (append-only) ---------------------------------------
CREATE TABLE IF NOT EXISTS submissions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(id),
    exercise_id    TEXT    NOT NULL REFERENCES exercises(id),
    attempt_number INTEGER NOT NULL,
    code           TEXT    NOT NULL,
    submitted_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_student_ex ON submissions(student_id, exercise_id);
CREATE INDEX IF NOT EXISTS idx_submissions_time ON submissions(student_id, submitted_at);

-- 'evaluator' lets a later AI evaluator add its own rows next to the rule-based one.
CREATE TABLE IF NOT EXISTS evaluations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    evaluator     TEXT    NOT NULL DEFAULT 'rule_based',
    passed        INTEGER NOT NULL,
    score         REAL    NOT NULL,
    status        TEXT    NOT NULL,
    feedback      TEXT    NOT NULL,
    details       TEXT    NOT NULL DEFAULT '{}',   -- JSON
    evaluated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluations_submission ON evaluations(submission_id);

CREATE TABLE IF NOT EXISTS mistakes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL REFERENCES students(id),
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    exercise_id   TEXT    NOT NULL REFERENCES exercises(id),
    concept_id    TEXT    NOT NULL REFERENCES concepts(id),
    error_type    TEXT    NOT NULL,
    message       TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mistakes_student_concept ON mistakes(student_id, concept_id);
CREATE INDEX IF NOT EXISTS idx_mistakes_student_type ON mistakes(student_id, error_type);
