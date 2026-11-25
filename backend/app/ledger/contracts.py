from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.clock import from_aware, utc_iso


class LedgerEntryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)
    edition_key: str = Field(min_length=1, max_length=70)
    kind: Literal['receipt', 'withdrawal']
    # Optional: defaults to the batch posting time (ordinary live intake).
    # A back-dated补录 passes an earlier business timestamp explicitly.
    received_at: datetime | None = None
    source_ref: str | None = Field(default=None, min_length=1, max_length=70)

    @field_validator('received_at')
    @classmethod
    def require_offset(cls, value: datetime | None):
        if value is None:
            return None
        return from_aware(value).replace(microsecond=0)

    @model_validator(mode='after')
    def validate_payload_pair(self):
        if self.kind == 'receipt':
            if self.source_ref is None or self.received_at is None:
                raise ValueError('a receipt entry needs received_at and source_ref')
        elif self.source_ref is not None or self.received_at is not None:
            raise ValueError('a withdrawal entry carries no receipt payload')
        return self


class LedgerBatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    # When omitted, the server stamps the booking instant itself.
    posted_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)
    entries: list[LedgerEntryInput] = Field(min_length=1, max_length=5000)

    @field_validator('posted_at')
    @classmethod
    def require_offset(cls, value: datetime | None):
        if value is None:
            return None
        return from_aware(value).replace(microsecond=0)


def describe_entry(entry):
    return {'id': entry.id, 'batch_id': entry.batch_id, 'seq': entry.seq,
            'edition_key': entry.edition_key, 'kind': entry.kind,
            'posted_at': utc_iso(entry.posted_at),
            'received_at': utc_iso(entry.received_at) if entry.received_at else None,
            'source_ref': entry.source_ref}


def describe_batch(batch, entries=None):
    data = {'id': batch.id, 'batch_id': batch.batch_id,
            'posted_at': utc_iso(batch.posted_at), 'status': batch.status,
            'reject_reason': batch.reject_reason, 'detail': batch.detail,
            'note': batch.note, 'created_by': batch.created_by,
            'created_at': utc_iso(batch.created_at)}
    if entries is not None:
        data['entries'] = [describe_entry(item) for item in entries]
    return data
