from datetime import datetime


def seed():
    definitions = [
        ('NY-HALL', 'matinee', '2026-11-01', '01:30', 'America/New_York', 'later', '06:45', 'NY-1101.csv'),
        ('LDN-HALL', 'matinee', '2026-11-01', '08:00', 'Europe/London', 'later', '07:20', 'LDN-1101.csv'),
        ('MAD-HALL', 'evening', '2026-11-01', '08:30', 'Europe/Madrid', 'later', None, None),
        ('TYO-HALL', 'matinee', '2026-11-01', '08:00', 'Asia/Tokyo', 'later', '01:15', 'TYO-1101.csv'),
        ('NY-HALL', 'evening', '2026-03-08', '02:30', 'America/New_York', 'later', None, None),
    ]
    return [
        {'venue': venue, 'edition': edition, 'performance_day': day, 'local_due': clock, 'zone': zone,
         'overlap_policy': fold, 'recipient': 'viewer@demo.local', 'source_ref': source,
         'received_at': datetime.fromisoformat(f'{day}T{received}:00+00:00').isoformat() if received else None}
        for venue, edition, day, clock, zone, fold, received, source in definitions
    ]
