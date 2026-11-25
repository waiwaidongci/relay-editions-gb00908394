from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def to_utc(day: date, at: time, zone_name: str, fold: str) -> tuple[datetime, bool]:
    """Resolve a local wall clock by doing a UTC round trip for each fold."""
    zone = ZoneInfo(zone_name)
    wall_clock = datetime.combine(day, at)
    instants = set()
    for occurrence in (0, 1):
        local = wall_clock.replace(tzinfo=zone, fold=occurrence)
        candidate = local.astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == wall_clock:
            instants.add(candidate)
    if not instants:
        raise ValueError('the local due time falls in a daylight-saving gap')
    choices = sorted(instants)
    return (choices[-1] if fold == 'later' else choices[0]), len(choices) == 2
