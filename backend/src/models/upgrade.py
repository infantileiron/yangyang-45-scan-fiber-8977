"""Additive schema upgrades for demo databases created before corrections existed.

``create_all`` only creates missing tables, so previously seeded volumes need the
new observation columns backfilled in place. Legacy rows keep their content and
become revision 1 of their ticket chain.
"""
import hashlib
import json

from sqlalchemy import inspect, select, text

from src.constants.enums import ObservationState


def _columns(connection, table):
    return {column['name'] for column in inspect(connection).get_columns(table)}


def upgrade(engine):
    inspector = inspect(engine)
    if 'observations' not in inspector.get_table_names():
        return
    with engine.begin() as connection:
        present = _columns(connection, 'observations')
        for name, ddl in (
            ('ticket', 'VARCHAR(96)'),
            ('revision', 'INTEGER DEFAULT 1'),
            ('state', "VARCHAR(16) DEFAULT 'effective'"),
        ):
            if name not in present:
                connection.execute(text(f'ALTER TABLE observations ADD COLUMN {name} {ddl}'))
        rows = connection.execute(text(
            "SELECT id, source_key, payload FROM observations "
            "WHERE ticket IS NULL OR revision IS NULL OR state IS NULL")).mappings().all()
        for row in rows:
            payload = row['payload']
            payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
            payload.setdefault('revision', 1)
            ticket = payload.get('ticket')
            source_key = 'data:' + hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            connection.execute(text(
                'UPDATE observations SET payload = :payload, source_key = :source_key, '
                'ticket = :ticket, revision = :revision, state = :state WHERE id = :id'),
                {'payload': json.dumps(payload, ensure_ascii=False), 'source_key': source_key,
                 'ticket': ticket, 'revision': payload['revision'], 'state': ObservationState.EFFECTIVE,
                 'id': row['id']})
