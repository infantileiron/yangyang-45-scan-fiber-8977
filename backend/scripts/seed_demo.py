import json
from collections import Counter

from sqlalchemy import func, select

from src.domain.analysis import seed
from src.models.base import Base, Session, engine
from src.models.upgrade import upgrade
from src.models.workflow import AnalysisRun, Observation, Review, Schedule, WorkbenchMeta
from src.services.observations import clean_observation, content_key, publish_workbench


def seed_data():
    upgrade(engine)
    Base.metadata.create_all(engine)
    rows = [clean_observation(row) for row in seed()]
    counts = Counter(content_key(row) for row in rows)
    seen = Counter()
    with Session.begin() as db:
        keys = set(db.scalars(select(Observation.source_key)))
        for row in rows:
            key = content_key(row)
            seen[key] += 1
            if counts[key] > 1 and seen[key] > 1:
                key = f'{key}:{seen[key]}'
            if key not in keys:
                db.add(Observation(source_key=key, payload=row, ticket=row['ticket'],
                                   revision=row.get('revision', 1)))
        db.flush()
        if db.scalar(select(func.count(Observation.id))) and db.get(WorkbenchMeta, 1) is None:
            publish_workbench(db)
        return {model.__tablename__: db.scalar(select(func.count(model.id)))
                for model in (Observation, AnalysisRun, Review, Schedule)}


if __name__ == '__main__':
    print(json.dumps(seed_data(), sort_keys=True))
