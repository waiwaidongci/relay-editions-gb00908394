# Import every model before applying the initial schema.
from app.analysis.models import AnalysisRun, Review
from app.auth.models import Account
from app.data.models import Dataset, Observation
from app.delivery.models import Delivery
from app.ledger.models import ReceiptBatch, ReceiptLedgerEntry
from app.ops.audit import AuditEvent
from app.database import Base, engine
from domain.relay_models import EditionRelease


def initialize():
    Base.metadata.create_all(engine)
