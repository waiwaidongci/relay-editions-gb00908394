from domain.fixtures import seed
from domain.intake import EditionInput, EditionOptions, record_key, validate_rows
from domain.ledger import EditionState
from domain.planning import plan_edition
from domain.rollup import build_report


TITLE = '海外器乐巡演场馆场务晨报补发'
METHOD = 'IANA 时区归一化、夏令时歧义消解与逐场缺报补发'
SEED_NAME = 'overseas-editions-2026.csv'
ROW_MODEL = EditionInput
OPTIONS_MODEL = EditionOptions


def analyze(rows: list[EditionInput], options: EditionOptions,
            states: dict[str, EditionState] | None = None) -> dict:
    validate_rows(rows)
    states = states or {}
    plans = [plan_edition(row, options, states.get(record_key(row)))
             for row in sorted(rows, key=record_key)]
    return build_report(plans, options)
