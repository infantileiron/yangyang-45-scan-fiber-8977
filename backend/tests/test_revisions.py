import pytest

from src.constants.enums import ObservationState
from src.domain import analysis
from src.models.workflow import Observation, WorkbenchMeta
from src.services.errors import BusinessError
from src.services.observations import ingest, publish_workbench


def row(ticket, scanner, fiber='轻', redo='仅一次', revision=1):
    return {'ticket': ticket, 'scanner': scanner, 'fiber': fiber, 'redo': redo, 'revision': revision}


def states(db):
    return {item.ticket: (item.revision, item.state) for item in db.query(Observation).all()}


def effective(db):
    return sorted((item.ticket, item.payload['scanner'], item.revision)
                  for item in db.query(Observation).all() if item.state == ObservationState.EFFECTIVE)


def test_retry_does_not_add_observations(db):
    first = ingest(db, [row('T1', '东列 A')], 'analyst', 'observation.created')
    same = ingest(db, [row('T1', '东列 A')], 'analyst', 'observation.created')
    assert first['added'] == 1
    assert same['added'] == 0 and same['skipped'] == 1
    assert db.query(Observation).count() == 1


def test_revision_requires_previous_suspended_until_filled(db):
    report = ingest(db, [row('T1', '东列 A', revision=2, fiber='重')], 'analyst', 'observation.created')
    assert report['pending'] == 1
    assert states(db)['T1'] == (2, ObservationState.PENDING)

    # A later correction still waits while the chain is broken.
    ingest(db, [row('T1', '东列 A', revision=3, redo='两次及以上')], 'analyst', 'observation.created')
    assert states(db)['T1'] in {(2, ObservationState.PENDING), (3, ObservationState.PENDING)}
    assert db.query(Observation).filter(Observation.state == ObservationState.EFFECTIVE).count() == 0

    filled = ingest(db, [row('T1', '东列 A', revision=1)], 'analyst', 'observation.created')
    assert filled['activated'] == 1
    assert sorted((i.revision, i.state) for i in db.query(Observation).all()) == [
        (1, ObservationState.SUPERSEDED), (2, ObservationState.SUPERSEDED),
        (3, ObservationState.EFFECTIVE)]
    assert effective(db) == [('T1', '东列 A', 3)]


def test_same_revision_conflict_keeps_effective_version_others_land(db):
    ingest(db, [row('T1', '东列 A')], 'analyst', 'observation.created')
    ingest(db, [row('T1', '东列 A', revision=2, fiber='重')], 'analyst', 'observation.created')
    # Same revision 2, different content: keep stored version 2, but T2 still lands.
    report = ingest(db, [row('T1', '东列 A', revision=2, fiber='无'), row('T2', '西列 B')],
                    'analyst', 'observation.created')
    assert report['conflicts'] == 1
    assert report['added'] == 1
    winner = db.query(Observation).filter(Observation.ticket == 'T1',
                                          Observation.revision == 2).one()
    assert winner.payload['fiber'] == '重'
    assert effective(db) == [('T1', '东列 A', 2), ('T2', '西列 B', 1)]


def test_move_between_scanners_switches_both_in_one_publish(db):
    ingest(db, [row('T1', '东列 A'), row('T2', '东列 A'), row('T3', '西列 B'),
                row('T4', '西列 B')], 'analyst', 'observation.created')
    publish_workbench(db)
    before = db.get(WorkbenchMeta, 1)
    old_version, old_result = before.version, before.result

    report = ingest(db, [row('T1', '西列 B', revision=2, fiber='重')], 'analyst', 'observation.created')
    assert report['affected'] == ['东列 A', '西列 B']
    after = db.get(WorkbenchMeta, 1)
    assert after.version == old_version + 1
    detail = {item['scanner']: item for item in after.result['rows']}
    assert detail['东列 A']['n'] == 1
    assert detail['西列 B']['n'] == 3
    # Old published version was untouched before the switch.
    assert {item['scanner']: item['n'] for item in old_result['rows']}['东列 A'] == 2


def test_failed_recompute_keeps_previous_version_and_old_analysis(db, monkeypatch):
    ingest(db, [row('T1', '东列 A'), row('T2', '东列 A')], 'analyst', 'observation.created')
    publish_workbench(db)
    before = db.get(WorkbenchMeta, 1)
    snapshot = (before.version, before.result)

    def explode(*_args, **_kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(analysis, 'scanner_metrics', explode)
    report = ingest(db, [row('T1', '西列 B', revision=2, fiber='重')], 'analyst', 'observation.created')
    assert report['recompute_failed'] is True

    db.expire_all()
    after = db.get(WorkbenchMeta, 1)
    assert (after.version, after.result) == snapshot
    assert db.query(Observation).filter(Observation.state == ObservationState.EFFECTIVE).count() == 2
    # The correction itself is kept so a later successful recompute can pick it up.
    assert states(db)['T1'] == (2, ObservationState.EFFECTIVE)


def test_import_validation_of_revision(db):
    with pytest.raises(BusinessError):
        ingest(db, [row('T1', '东列 A', revision=0)], 'analyst', 'observation.created')


def test_workbench_counts_tied_pairs_and_short_station(db):
    rows = analysis.seed()
    ingest(db, rows, 'analyst', 'observation.imported')
    meta = db.get(WorkbenchMeta, 1)
    assert meta is not None
    cells = {item['scanner']: item for item in meta.result['rows']}
    assert cells['夜线 D']['n'] == 4
    assert cells['东列 A']['tied'] == 366
    assert cells['转拍 C']['tied'] == 36


def test_chain_arrives_out_of_order_within_one_batch(db):
    batch = [row('T1', '东列 A', revision=3, fiber='重'),
             row('T1', '东列 A', revision=1),
             row('T1', '东列 A', revision=2, fiber='轻')]
    report = ingest(db, batch, 'analyst', 'observation.imported')
    assert report['added'] == 3
    assert report['pending'] == 0
    assert effective(db) == [('T1', '东列 A', 3)]
    assert sorted(i.revision for i in db.query(Observation).all()) == [1, 2, 3]


def test_pending_correction_does_not_bump_published_version(db):
    ingest(db, [row('T1', '东列 A')], 'analyst', 'observation.created')
    before = db.get(WorkbenchMeta, 1).version
    ingest(db, [row('T1', '西列 B', revision=3)], 'analyst', 'observation.created')
    assert db.get(WorkbenchMeta, 1).version == before
    assert effective(db) == [('T1', '东列 A', 1)]


def test_old_run_snapshot_survives_later_correction(db):
    from src.domain.analysis import analyze
    ingest(db, [row(f'T{n}', '东列 A', fiber=fiber) for n, fiber in
                enumerate(['无', '轻', '重', '轻'])], 'analyst', 'observation.created')
    snapshot = [(item.id, item.payload) for item in db.query(Observation).all()]
    old = analyze([payload for _, payload in snapshot], {'minimum': 4})
    ingest(db, [row('T1', '西列 B', revision=2)], 'analyst', 'observation.created')
    again = analyze([payload for _, payload in snapshot], {'minimum': 4})
    assert old == again
    assert old['rows'][0]['n'] == 4


def test_rebuilds_every_scanner_after_recompute_failure(db, monkeypatch):
    ingest(db, [row('T1', '东列 A'), row('T2', '西列 B')], 'analyst', 'observation.created')
    publish_workbench(db)

    def explode(*_args, **_kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(analysis, 'scanner_metrics', explode)
    ingest(db, [row('T1', '西列 B', revision=2)], 'analyst', 'observation.created')
    assert db.get(WorkbenchMeta, 1).stale is True

    monkeypatch.undo()
    # A later correction only touches 东列 A, but the stale flag forces a full rebuild.
    ingest(db, [row('T2', '东列 A', revision=2)], 'analyst', 'observation.created')
    meta = db.get(WorkbenchMeta, 1)
    assert meta.stale is False
    assert {item['scanner']: item['n'] for item in meta.result['rows']} == {'东列 A': 1, '西列 B': 1}


def test_pending_rows_are_excluded_from_new_run_snapshot(db):
    from src.services.runs import create_run
    from src.schemas.requests import CreateRun

    ingest(db, [row('T1', '东列 A'), row('T2', '西列 B')], 'analyst', 'observation.created')
    ingest(db, [row('T3', '东列 A', revision=2)], 'analyst', 'observation.created')
    run = create_run(db, CreateRun(title='样本', parameters={}), 'analyst@demo.local')
    assert run.sample_size == 2
    ids = set(run.observation_ids)
    pending = db.query(Observation).filter(Observation.state == ObservationState.PENDING).all()
    assert pending and all(item.id not in ids for item in pending)
