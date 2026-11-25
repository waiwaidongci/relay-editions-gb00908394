from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.auth.dependencies import account, require
from app.auth.roles import Role
from app.clock import from_aware
from app.database import Session
from app.errors import Problem
from app.ledger.contracts import LedgerBatchRequest, describe_batch
from app.ledger.repository import (
    book_batch,
    edition_history,
    get_batch,
    list_batches,
    replay_states,
)


router = APIRouter(prefix='/ledger', tags=['Receipt ledger'])
analyst = require(Role.analyst)


@router.post('/batches')
def ingest_batch(payload: LedgerBatchRequest, response: Response, user=Depends(analyst)):
    batch, entries, accepted = book_batch(payload, user.email)
    response.status_code = 201 if accepted else 409
    return describe_batch(batch, entries)


@router.get('/batches')
def batches(limit: Annotated[int, Query(ge=1, le=100)] = 50, user=Depends(account)):
    with Session() as db:
        return list_batches(db, limit)


@router.get('/batches/{batch_id}')
def one_batch(batch_id: int, user=Depends(account)):
    with Session() as db:
        return get_batch(db, batch_id)


@router.get('/editions/{edition_key}/history')
def history(edition_key: str, user=Depends(account)):
    with Session() as db:
        return edition_history(db, edition_key)


@router.get('/replay')
def replay_view(as_of: Annotated[str, Query(min_length=5, max_length=40)], user=Depends(account)):
    """Rebuild what was known about every edition at a past entry time."""
    try:
        moment = datetime.fromisoformat(as_of)
    except ValueError:
        raise Problem(422, 'invalid_as_of', 'Use an ISO-8601 timestamp with offset') from None
    try:
        moment = from_aware(moment)
    except ValueError:
        raise Problem(422, 'invalid_as_of', 'as_of must carry a time zone offset') from None
    with Session() as db:
        states = replay_states(db, moment.replace(tzinfo=timezone.utc))
        return [
            {'edition_key': state.edition_key, 'kind': state.kind.value,
             'received_at': state.received_at.isoformat() if state.received_at else None,
             'source_ref': state.source_ref,
             'posted_at': state.posted_at.isoformat() if state.posted_at else None,
             'batch_id': state.batch_id}
            for state in sorted(states.values(), key=lambda item: item.edition_key)
        ]
