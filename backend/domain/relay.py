from datetime import datetime

from sqlalchemy import func, select
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


def _active_release(db, key):
    return db.scalar(select(EditionRelease).where(
        EditionRelease.edition_key == key, EditionRelease.active.is_(True)))


def _delivery_for(db, release):
    return db.get(Delivery, release.delivery_id) if release else None


def existing_delivery(db, key):
    """The current evidence for an edition: delivered first, else active row."""
    release = _active_release(db, key)
    if release is not None:
        return _delivery_for(db, release)
    # No active row: an already-sent delivery still answers rerun lookups.
    return db.scalar(select(Delivery).where(
        Delivery.channel == f'edition:{key}',
        Delivery.delivered_at.is_not(None)).order_by(Delivery.id.desc()))


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

            # Delivery-first race: an already delivered report is immutable
            # evidence; a rerun returns the same delivery instead of resending.
            already = db.scalar(select(Delivery).where(
                Delivery.channel == f'edition:{key}',
                Delivery.delivered_at.is_not(None)).order_by(Delivery.id.desc()))
            if already is not None:
                return already, False

            active = _active_release(db, key)
            if active is not None:
                existing = _delivery_for(db, active)
                if existing is not None and existing.superseded_at is None:
                    # Same active version requested again: idempotent return.
                    return existing, False
                # The queued report was intercepted by a correction; the
                # release row is retired before the replacement is issued.
                active.active = False

            prior_count = db.scalar(select(func.count(EditionRelease.id)).where(
                EditionRelease.edition_key == key))
            version = prior_count + 1
            ready_at = from_aware(datetime.fromisoformat(plan['ready_at']))
            delivery = Delivery(run_id=run_id, recipient=recipient, due_at=ready_at,
                                channel=f'edition:{key}', expires_at=deadline, scheduled_by=actor,
                                report={'edition': plan, 'run_id': run.id, 'dataset': report['dataset'],
                                        'review': report['review']})
            db.add(delivery)
            db.flush()
            replaces = active.delivery_id if active is not None else None
            db.add(EditionRelease(edition_key=key, delivery_id=delivery.id, version=version,
                                  active=True, replaces_delivery_id=replaces))
            audit(db, actor, 'edition_queued', f'{key}#v{version}')
        wake_worker()
        return delivery, True
    except IntegrityError:
        with Session() as db:
            if (existing := existing_delivery(db, key)) is not None:
                return existing, False
        raise
