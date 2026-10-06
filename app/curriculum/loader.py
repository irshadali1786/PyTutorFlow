"""Load curriculum YAML files into SQLite.

* Content lives ONLY in YAML (app/curriculum/content/*.yaml). Edit YAML, run
  `python -m app init` again - no Python changes needed.
* Syncing is idempotent: running it twice changes nothing; edited text is updated.
* Student history is never deleted. Removed lessons simply stop being updated.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from app import config


class CurriculumError(ValueError):
    """Raised with a human-friendly message when a YAML file is wrong."""


EXERCISE_KINDS = ("code", "quiz")
CODE_MATCHES = ("exact", "non_empty", "contains")


def _need(d: dict, key: str, where: str, types=str):
    if not isinstance(d, dict) or key not in d or d[key] in (None, ""):
        raise CurriculumError(f"{where}: missing required field '{key}'")
    if not isinstance(d[key], types):
        raise CurriculumError(f"{where}: field '{key}' has the wrong type")
    return d[key]


def _unique(seen: set, kind: str, id_: str, where: str) -> None:
    if id_ in seen:
        raise CurriculumError(f"{where}: duplicate {kind} id '{id_}'")
    seen.add(id_)


def _normalise_exercise(ex: dict, where: str) -> dict:
    kind = ex.get("kind", "code")
    if kind not in EXERCISE_KINDS:
        raise CurriculumError(f"{where}: kind must be one of {EXERCISE_KINDS}")
    cases = ex.get("test_cases")
    if not isinstance(cases, list) or not cases:
        raise CurriculumError(f"{where}: needs at least one test case")
    norm_cases = []
    for i, case in enumerate(cases, 1):
        w = f"{where} test case {i}"
        if kind == "quiz":
            accepted = case.get("accepted")
            if not isinstance(accepted, list) or not accepted:
                raise CurriculumError(f"{w}: quiz cases need a non-empty 'accepted' list")
            norm_cases.append({"match": "choice", "accepted": [str(a).strip().lower() for a in accepted]})
        else:
            match = case.get("match", "exact")
            if match not in CODE_MATCHES:
                raise CurriculumError(f"{w}: match must be one of {CODE_MATCHES}")
            if match != "non_empty" and "expected_output" not in case:
                raise CurriculumError(f"{w}: missing 'expected_output'")
            norm_cases.append(
                {"stdin": str(case.get("stdin", "")), "expected_output": str(case.get("expected_output", "")), "match": match}
            )
    hints = ex.get("hints", [])
    if not isinstance(hints, list):
        raise CurriculumError(f"{where}: hints must be a list")
    criteria = ex.get("passing_criteria") or {}
    criteria.setdefault("min_score", 100)
    return {"kind": kind, "test_cases": norm_cases, "hints": [str(h) for h in hints], "passing_criteria": criteria}


def _flatten(docs: list[tuple[Path, dict]]) -> dict:
    """Validate every file and turn the nested YAML into flat row lists."""
    out = {k: [] for k in ("phases", "modules", "topics", "concepts", "lessons", "exercises", "prereqs")}
    seen = {k: set() for k in ("phase", "module", "topic", "concept", "lesson", "exercise")}
    explicit_requires: dict[str, list[str]] = {}
    seq = 0
    previous_lesson = None

    for phase_pos, (path, doc) in enumerate(docs, 1):
        w = path.name
        ph = _need(doc, "phase", w, dict)
        pid = _need(ph, "id", w)
        _unique(seen["phase"], "phase", pid, w)
        out["phases"].append((pid, _need(ph, "title", w), ph.get("description", ""), phase_pos))
        for m_pos, mod in enumerate(_need(ph, "modules", w, list), 1):
            mid = _need(mod, "id", f"{w} module")
            _unique(seen["module"], "module", mid, w)
            out["modules"].append((mid, pid, _need(mod, "title", f"{w} module {mid}"), m_pos))
            for t_pos, top in enumerate(_need(mod, "topics", f"{w} module {mid}", list), 1):
                tid = _need(top, "id", f"{w} topic")
                _unique(seen["topic"], "topic", tid, w)
                out["topics"].append((tid, mid, _need(top, "title", f"{w} topic {tid}"), t_pos))
                for c_pos, con in enumerate(_need(top, "concepts", f"{w} topic {tid}", list), 1):
                    cid = _need(con, "id", f"{w} concept")
                    _unique(seen["concept"], "concept", cid, w)
                    overrides = con.get("mastery")
                    out["concepts"].append(
                        (cid, tid, _need(con, "title", f"{w} concept {cid}"), c_pos,
                         json.dumps(overrides) if overrides else None)
                    )
                    for l_pos, les in enumerate(_need(con, "lessons", f"{w} concept {cid}", list), 1):
                        lid = _need(les, "id", f"{w} lesson")
                        _unique(seen["lesson"], "lesson", lid, w)
                        seq += 1
                        out["lessons"].append(
                            (lid, cid, _need(les, "title", f"{w} lesson {lid}"),
                             _need(les, "content", f"{w} lesson {lid}"),
                             les.get("example_code", ""), l_pos, seq)
                        )
                        if "requires" in les:
                            explicit_requires[lid] = list(les["requires"] or [])
                        elif previous_lesson:
                            explicit_requires[lid] = [previous_lesson]   # default: previous lesson
                        else:
                            explicit_requires[lid] = []
                        previous_lesson = lid
                        exercises = _need(les, "exercises", f"{w} lesson {lid}", list)
                        if not exercises:
                            raise CurriculumError(f"{w} lesson {lid}: needs at least one exercise")
                        for e_pos, ex in enumerate(exercises, 1):
                            eid = _need(ex, "id", f"{w} exercise in {lid}")
                            _unique(seen["exercise"], "exercise", eid, w)
                            ew = f"{w} exercise {eid}"
                            n = _normalise_exercise(ex, ew)
                            out["exercises"].append(
                                (eid, lid, cid, n["kind"], _need(ex, "question", ew),
                                 int(ex.get("difficulty", 1)), ex.get("expected_behavior", ""),
                                 json.dumps(n["test_cases"]), json.dumps(n["hints"]),
                                 json.dumps(n["passing_criteria"]), e_pos)
                            )
    for lid, reqs in explicit_requires.items():
        for r in reqs:
            if r not in seen["lesson"]:
                raise CurriculumError(f"lesson {lid}: requires unknown lesson '{r}'")
            if r == lid:
                raise CurriculumError(f"lesson {lid}: cannot require itself")
            out["prereqs"].append((lid, r))
    _check_no_cycles(explicit_requires)
    return out


def _check_no_cycles(requires: dict[str, list[str]]) -> None:
    """A circular prerequisite chain would lock students out forever - refuse it."""
    state: dict[str, int] = {}          # 1 = visiting, 2 = done

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise CurriculumError("circular prerequisites: " + " -> ".join(trail + [node]))
        state[node] = 1
        for nxt in requires.get(node, []):
            visit(nxt, trail + [node])
        state[node] = 2

    for lesson_id in requires:
        visit(lesson_id, [])


def load_curriculum(conn: sqlite3.Connection, content_dir: str | Path | None = None) -> dict:
    """Read every *.yaml file in content_dir (sorted by name) and sync it into the DB."""
    content_dir = Path(content_dir if content_dir is not None else config.CURRICULUM_DIR)
    files = sorted(content_dir.glob("*.yaml"))
    if not files:
        raise CurriculumError(f"No .yaml curriculum files found in {content_dir}")
    docs = []
    for f in files:
        try:
            docs.append((f, yaml.safe_load(f.read_text(encoding="utf-8"))))
        except yaml.YAMLError as exc:
            raise CurriculumError(f"{f.name}: invalid YAML - {exc}") from exc
    plan = _flatten(docs)

    with conn:
        conn.executemany(
            "INSERT INTO phases(id,title,description,position) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description, position=excluded.position",
            plan["phases"])
        conn.executemany(
            "INSERT INTO modules(id,phase_id,title,position) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET phase_id=excluded.phase_id, title=excluded.title, position=excluded.position",
            plan["modules"])
        conn.executemany(
            "INSERT INTO topics(id,module_id,title,position) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET module_id=excluded.module_id, title=excluded.title, position=excluded.position",
            plan["topics"])
        conn.executemany(
            "INSERT INTO concepts(id,topic_id,title,position,mastery_rules) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET topic_id=excluded.topic_id, title=excluded.title, "
            "position=excluded.position, mastery_rules=excluded.mastery_rules",
            plan["concepts"])
        conn.executemany(
            "INSERT INTO lessons(id,concept_id,title,content,example_code,position,seq) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET concept_id=excluded.concept_id, title=excluded.title, content=excluded.content, "
            "example_code=excluded.example_code, position=excluded.position, seq=excluded.seq",
            plan["lessons"])
        lesson_ids = [(row[0],) for row in plan["lessons"]]
        conn.executemany("DELETE FROM lesson_prerequisites WHERE lesson_id=?", lesson_ids)
        conn.executemany("INSERT INTO lesson_prerequisites(lesson_id,requires_lesson_id) VALUES(?,?)", plan["prereqs"])
        conn.executemany(
            "INSERT INTO exercises(id,lesson_id,concept_id,kind,question,difficulty,expected_behavior,"
            "test_cases,hints,passing_criteria,position) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET lesson_id=excluded.lesson_id, concept_id=excluded.concept_id, kind=excluded.kind, "
            "question=excluded.question, difficulty=excluded.difficulty, expected_behavior=excluded.expected_behavior, "
            "test_cases=excluded.test_cases, hints=excluded.hints, passing_criteria=excluded.passing_criteria, "
            "position=excluded.position",
            plan["exercises"])
    return {k: len(v) for k, v in plan.items()}
