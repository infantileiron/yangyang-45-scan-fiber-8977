import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_FILE = str(ROOT / 'tests' / '.pytest.db')
if os.path.exists(DB_FILE):
    os.unlink(DB_FILE)
os.environ['DATABASE_URL'] = f'sqlite:///{DB_FILE}'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:1/0'
os.environ['ARTIFACT_ROOT'] = str(ROOT / 'tests' / '.artifacts')
os.environ['SEED_DEMO'] = 'false'

import pytest  # noqa: E402

from src.models.base import Base, Session, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with Session.begin() as session:
        yield session
