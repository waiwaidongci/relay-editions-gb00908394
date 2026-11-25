from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class EditionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)
    venue: str = Field(pattern=r'^[A-Z][A-Z0-9-]{1,23}$')
    edition: Literal['matinee', 'evening']
    performance_day: date
    local_due: time
    zone: str = Field(max_length=50)
    overlap_policy: Literal['earlier', 'later'] = 'later'
    recipient: str = Field(pattern=r'^[a-z0-9_.-]+@demo\.local$', max_length=120)
    received_at: AwareDatetime | None = None
    source_ref: str | None = Field(default=None, min_length=1, max_length=70)

    @field_validator('local_due')
    @classmethod
    def local_clock_only(cls, value):
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError('local_due must be an unzoned clock in whole minutes')
        return value

    @field_validator('zone')
    @classmethod
    def known_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('zone must be an installed IANA zone') from exc
        return value

    @model_validator(mode='after')
    def validate_receipt(self):
        if (self.received_at is None) != (self.source_ref is None):
            raise ValueError('received_at and source_ref must both be present or both be absent')
        if not 2000 <= self.performance_day.year <= 2100:
            raise ValueError('performance_day must be in 2000–2100')
        return self


class EditionOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    as_of: AwareDatetime = datetime.fromisoformat('2026-11-01T12:00:00+00:00')
    backfill_hours: int = Field(ge=1, le=168, default=24)


def record_key(row: EditionInput) -> str:
    return f'{row.venue}/{row.performance_day.isoformat()}/{row.edition}'


def validate_rows(rows: list[EditionInput]) -> None:
    zones = {}
    seen = set()
    for row in rows:
        key = record_key(row)
        if key in seen:
            raise ValueError(f'Duplicate edition {key}')
        seen.add(key)
        if zones.setdefault(row.venue, row.zone) != row.zone:
            raise ValueError(f'Conflicting time zones at venue {row.venue}')
