from src.constants.enums import ObservationState
from src.utils.formatting import timestamp


def review_view(item):
    return {'decision': item.decision, 'note': item.note, 'reviewer': item.reviewer,
            'at': timestamp(item.created_at)} if item else None


def run_view(item, review=None, *, include_result=False):
    view = {'id': item.id, 'title': item.title, 'owner': item.owner, 'status': item.status,
            'parameters': item.parameters, 'sample_size': item.sample_size,
            'snapshot_id': item.last_observation_id, 'created_at': timestamp(item.created_at),
            'finished_at': timestamp(item.finished_at), 'review': review_view(review), 'error': item.error}
    if include_result:
        view['result'] = item.result
    return view


def observation_view(item):
    return {'id': item.id, 'data': item.payload, 'ticket': item.ticket, 'revision': item.revision,
            'state': item.state, 'state_label': _STATE_LABELS.get(item.state, item.state),
            'created_at': timestamp(item.created_at)}


_STATE_LABELS = {ObservationState.EFFECTIVE: '已生效', ObservationState.SUPERSEDED: '已被更正',
                 ObservationState.PENDING: '待补齐前一版'}


def workbench_view(snapshot):
    return {'version': snapshot['version'], 'result': snapshot['result'], 'pending': snapshot['pending'],
            'updated_at': timestamp(snapshot['updated_at'])}


def schedule_view(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'interval_minutes': item.interval_minutes, 'next_at': timestamp(item.next_at), 'active': item.active}


def delivery_view(delivery, schedule):
    return {'id': delivery.id, 'schedule_id': schedule.id, 'recipient': schedule.recipient,
            'run_id': schedule.run_id, 'at': timestamp(delivery.delivered_at),
            'scheduled_at': timestamp(delivery.scheduled_at)}
