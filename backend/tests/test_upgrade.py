import json

import pytest
from sqlalchemy import text

from src.models.base import engine
from src.models.upgrade import upgrade


@pytest.fixture
def legacy_table():
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE IF EXISTS observations'))
        connection.execute(text("""
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY,
                source_key VARCHAR(96) UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                created_at DATETIME
            )"""))
        payload = {'ticket': 'MQ-001', 'scanner': '东列 A', 'fiber': '轻', 'redo': '仅一次'}
        connection.execute(text(
            'INSERT INTO observations (id, source_key, payload) VALUES (1, :key, :payload)'),
            {'key': 'data:legacy', 'payload': json.dumps(payload, ensure_ascii=False)})
    yield
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE IF EXISTS observations'))


def test_upgrade_backfills_correction_columns(legacy_table):
    upgrade(engine)
    with engine.connect() as connection:
        row = connection.execute(text(
            'SELECT ticket, revision, state, payload, source_key FROM observations WHERE id = 1')).one()
    payload = json.loads(row.payload)
    assert row.ticket == 'MQ-001'
    assert row.revision == 1
    assert row.state == 'effective'
    assert payload['revision'] == 1
    assert row.source_key.startswith('data:') and row.source_key != 'data:legacy'

    # Running the upgrade again is a no-op.
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM observations')).scalar() == 1
