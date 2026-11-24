import csv
import hashlib
import io
import json
import logging
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.constants.enums import ObservationState
from src.domain import analysis
from src.models.base import utc_now
from src.models.workflow import AuditEvent, Observation, WorkbenchCell, WorkbenchMeta
from src.services.errors import BusinessError
from src.services.validation import validated

logger = logging.getLogger(__name__)

MAX_OBSERVATIONS = 5000
DEFAULT_REVISION = 1


def clean_observation(raw):
    row = validated(SPEC['fields'], raw)
    try:
        if hasattr(analysis, 'validate'):
            analysis.validate(row)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc
    row.setdefault('revision', DEFAULT_REVISION)
    return row


def _canonical(row):
    key = dict(row)
    key.setdefault('revision', DEFAULT_REVISION)
    return key


def content_key(row):
    key = _canonical(row)
    return 'data:' + hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_csv(raw):
    if len(raw) > 512 * 1024:
        raise BusinessError('CSV 文件上限 512 KB', 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        header = reader.fieldnames or []
        known = {item['key'] for item in SPEC['fields']}
        required = {item['key'] for item in SPEC['fields'] if item.get('required', True)}
        if len(set(header)) != len(header) or not required.issubset(header) or not set(header).issubset(known):
            raise BusinessError('CSV 表头与导入模板不匹配')
        rows = []
        for row in reader:
            try:
                if None in row or any(value is None for value in row.values()):
                    raise BusinessError('列数与表头不匹配')
                rows.append(clean_observation(row))
            except BusinessError as exc:
                raise BusinessError(f'CSV 第 {reader.line_num} 行，{exc}') from exc
            if len(rows) > 500:
                raise BusinessError('单次导入上限 500 条')
        if not rows:
            raise BusinessError('CSV 里没有观测记录')
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BusinessError('请上传 UTF-8 编码的有效 CSV') from exc


def _chains(rows):
    chains = defaultdict(dict)
    for item in rows:
        if item.ticket is not None and item.revision is not None:
            chains[item.ticket][item.revision] = item
    return chains


def _contiguous_max(chain):
    revision = 0
    while revision + 1 in chain:
        revision += 1
    return revision


def _effective(chain):
    revision = _contiguous_max(chain)
    return chain.get(revision) if revision else None


def _scanner_of(item):
    return item.payload.get('scanner') if item is not None else None


def _sync_states(chains):
    """Mark the highest contiguous revision of each ticket effective; later gaps wait.

    Earlier contiguous revisions are superseded (they no longer count), revisions
    beyond a missing predecessor stay pending until the chain is filled.
    """
    activated = 0
    for chain in chains.values():
        latest = _contiguous_max(chain)
        for revision, item in chain.items():
            if revision == latest:
                state = ObservationState.EFFECTIVE
            elif revision < latest:
                state = ObservationState.SUPERSEDED
            else:
                state = ObservationState.PENDING
            if item.state != state:
                if state == ObservationState.EFFECTIVE and item.state == ObservationState.PENDING:
                    activated += 1
                item.state = state
    return activated


def publish_workbench(db, scanners=None):
    """Publish per-scanner pair counts, rewriting affected scanner cells atomically.

    ``scanners=None`` rebuilds every scanner (first publish or a full rebuild).
    Affected scanner cells and the rendered result carry one shared version and
    are written in the same savepoint, so a correction that moves a ticket
    between scanners never exposes the old or new side on its own.
    """
    minimum = validated(SPEC['parameters'], {}, defaults=True)['minimum']
    payloads = list(db.scalars(select(Observation.payload).where(
        Observation.state == ObservationState.EFFECTIVE)))
    groups = analysis.group_by_scanner(payloads)
    cells = {cell.scanner: cell for cell in db.scalars(select(WorkbenchCell).with_for_update())}
    targets = set(groups) | set(cells) if scanners is None else set(scanners)
    now = utc_now()
    for scanner in sorted(targets):
        group = groups.get(scanner, [])
        cell = cells.get(scanner)
        if not group:
            if cell is not None:
                db.delete(cell)
                cells.pop(scanner, None)
            continue
        metric = analysis.scanner_metrics(scanner, group, minimum)
        if cell is None:
            cell = WorkbenchCell(scanner=scanner, metric=metric, version=0, updated_at=now)
            db.add(cell)
            cells[scanner] = cell
        cell.metric = metric
        cell.updated_at = now
    meta = db.get(WorkbenchMeta, 1, with_for_update=True)
    if meta is None:
        meta = WorkbenchMeta(id=1, version=0)
        db.add(meta)
    meta.version = (meta.version or 0) + 1
    meta.result = analysis.render_result([cell.metric for cell in cells.values()])
    meta.stale = False
    meta.updated_at = now
    for scanner in targets:
        if scanner in cells:
            cells[scanner].version = meta.version
    db.flush()
    return meta.version


def current_workbench(db):
    """Return the published workbench snapshot; derive a live one before the first publish."""
    pending = db.scalar(select(func.count(Observation.id)).where(
        Observation.state == ObservationState.PENDING)) or 0
    meta = db.get(WorkbenchMeta, 1)
    if meta is not None and meta.result is not None:
        return {'version': meta.version, 'result': meta.result, 'pending': pending,
                'updated_at': meta.updated_at}
    minimum = validated(SPEC['parameters'], {}, defaults=True)['minimum']
    payloads = list(db.scalars(select(Observation.payload).where(
        Observation.state == ObservationState.EFFECTIVE)))
    groups = analysis.group_by_scanner(payloads)
    result = analysis.render_result(
        [analysis.scanner_metrics(scanner, group, minimum) for scanner, group in groups.items()])
    return {'version': 0, 'result': result, 'pending': pending, 'updated_at': None}


def ingest(db, rows, actor, action):
    """Ingest observations as revision chains keyed by ticket.

    Retries carry identical content and add nothing. When a ticket already holds
    a revision with different content, that ticket keeps its stored revision while
    the rest of the batch still lands. Revisions beyond a missing predecessor are
    suspended as pending and join the chain once the gap is filled.
    """
    keyed = {}
    for row in rows:
        row = _canonical(clean_observation(row))
        keyed.setdefault(content_key(row), row)
    if not keyed:
        return {'items': [], 'added': 0, 'skipped': 0, 'conflicts': 0, 'pending': 0,
                'activated': 0, 'affected': [], 'recompute_failed': False}
    existing_keys = set(db.scalars(select(Observation.source_key).where(
        Observation.source_key.in_(keyed))))
    candidates = [row for key, row in keyed.items() if key not in existing_keys]
    skipped = len(rows) - len(candidates)

    tickets = {row['ticket'] for row in candidates}
    stored = []
    if tickets:
        stored = list(db.scalars(select(Observation).where(Observation.ticket.in_(tickets))
                                 .order_by(Observation.ticket, Observation.revision)
                                 .with_for_update()))
    chains = _chains(stored)
    before = {ticket: _effective(chains.get(ticket, {})) for ticket in tickets}
    before_payload = {ticket: (item.payload if item is not None else None) for ticket, item in before.items()}

    added = []
    conflicts = 0
    for row in candidates:
        ticket, revision = row['ticket'], row['revision']
        chain = chains[ticket]
        current = chain.get(revision)
        if current is not None:
            if current.payload == row:
                skipped += 1
            else:
                conflicts += 1
            continue
        item = Observation(source_key=content_key(row), payload=row, ticket=ticket,
                           revision=revision, state=ObservationState.PENDING)
        db.add(item)
        chain[revision] = item
        added.append(item)
    db.flush()

    activated = _sync_states(chains)
    db.flush()

    affected = set()
    for ticket in tickets:
        old = before_payload.get(ticket)
        after = _effective(chains[ticket])
        if old == (after.payload if after is not None else None):
            continue
        for side in (before.get(ticket), after):
            scanner = _scanner_of(side)
            if scanner:
                affected.add(scanner)

    if db.scalar(select(func.count(Observation.id))) > MAX_OBSERVATIONS:
        raise BusinessError('当前项目最多保留 5000 条观测记录', 409)

    published = db.get(WorkbenchMeta, 1)
    recompute_failed = False
    rebuild = published is None or published.stale
    if affected or (added and rebuild):
        try:
            with db.begin_nested():
                publish_workbench(db, None if rebuild else sorted(affected))
        except Exception:
            # A failed recompute keeps the previously published workbench version.
            recompute_failed = True
            logger.exception('Workbench recompute failed; keeping the previous version')
            meta = db.get(WorkbenchMeta, 1)
            if meta is not None:
                # Force the next successful ingest to rebuild every scanner.
                meta.stale = True
    if added:
        detail = f'新增 {len(added)} 条观测'
        if conflicts:
            detail += f'，{conflicts} 条同版冲突保留原记录'
        pending = sum(1 for item in added if item.state == ObservationState.PENDING)
        if pending:
            detail += f'，{pending} 条待补齐前一版'
        db.add(AuditEvent(actor=actor, action=action, detail=detail[:200]))
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('有相同记录刚刚写入，请刷新后重试', 409) from exc
    if recompute_failed:
        db.add(AuditEvent(actor=actor, action='workbench.failed', detail='重算失败，保留旧版'))
    return {'items': added, 'added': len(added), 'skipped': skipped, 'conflicts': conflicts,
            'pending': sum(1 for item in added if item.state == ObservationState.PENDING),
            'activated': activated, 'affected': sorted(affected), 'recompute_failed': recompute_failed}

