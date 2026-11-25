from collections import Counter


ELIGIBLE = frozenset({'on_time', 'backfill'})


def build_report(plans, options):
    counts = Counter(row['status'] for row in plans)
    summary = {'edition_count': len(plans), 'venue_count': len({row['venue'] for row in plans}),
               'status_counts': dict(sorted(counts.items())), 'as_of': options.as_of.isoformat(),
               'backfill_hours': options.backfill_hours}
    eligible = [row for row in plans if row['status'] in ELIGIBLE]
    cautions = [f'{row["edition_key"]}: {row["status"]}' for row in plans
                if row['status'] not in ELIGIBLE]
    return {'summary': summary, 'tables': {'edition_plan': plans, 'eligible_editions': eligible},
            'warnings': cautions, 'publishable': bool(eligible)}
