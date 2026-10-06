import pytest

from app import config
from app.curriculum.loader import load_curriculum
from app.database.database import connect, init_db
from app.progress.engine import LearningEngine
from app.progress.mastery import MasteryRules


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    load_curriculum(c, config.CURRICULUM_DIR)
    yield c
    c.close()


@pytest.fixture
def engine(conn):
    # Explicit rules so tests do not depend on the YAML file contents.
    return LearningEngine(conn, rules=MasteryRules(passes_required=2, weak_after_consecutive_failures=3))
