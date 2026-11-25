from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.analysis.repository import get_run
from app.auth.models import Account
from app.clock import from_aware, now_utc
from app.database import Session
from app.delivery.models import Delivery
from app.delivery.report import approved_report
from app.errors import Problem
from app.ops.audit import audit
from app.ops.redis_bus import wake_worker
from domain.relay_models import EditionRelease
from domain.rollup import ELIGIBLE


def existing_delivery(db, key):
    release = db.get(EditionRelease, key)
    return db.get(Delivery, release.delivery_id) if release else None


def release_edition(run_id: int, key: str, actor: str):
    try:
        with Session.begin() as db:
            run = get_run(db, run_id, lock=True)
            report = approved_report(db, run)
            plan = next((item for item in report['result']['tables']['edition_plan']
                         if item['edition_key'] == key), None)
            if plan is None:
                raise Problem(404, 'edition_not_found', 'This edition is absent from the report')
            if plan['status'] not in ELIGIBLE:
                raise Problem(409, 'edition_not_eligible', f"Edition status: {plan['status']}")
            existing = existing_delivery(db, key)
            if existing is not None:
                return existing, False
            recipient = plan['recipient']
            if db.get(Account, recipient) is None:
                raise Problem(422, 'unknown_recipient', 'Edition recipient must be a local account')
            current = now_utc()
            deadline = from_aware(datetime.fromisoformat(plan['expires_at']))
            if current >= deadline:
                raise Problem(409, 'edition_expired', 'The backfill window has closed')
            received = from_aware(datetime.fromisoformat(plan['received_at']))
            if received > current:
                raise Problem(409, 'receipt_not_available', 'The reported source has not yet arrived')
            ready_at = from_aware(datetime.fromisoformat(plan['ready_at']))
            delivery = Delivery(run_id=run_id, recipient=recipient, due_at=ready_at,
                                channel=f'edition:{key}', expires_at=deadline, scheduled_by=actor,
                                report={'edition': plan, 'run_id': run.id, 'dataset': report['dataset'],
                                        'review': report['review']})
            db.add(delivery)
            db.flush()
            db.add(EditionRelease(edition_key=key, delivery_id=delivery.id))
            audit(db, actor, 'edition_queued', key)
        wake_worker()
        return delivery, True
    except IntegrityError:
        with Session() as db:
            if (existing := existing_delivery(db, key)) is not None:
                return existing, False
        raise
