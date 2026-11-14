import hashlib
import json
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / 'data'


def names():
    return sorted(path.stem for path in DATA.glob('*.json'))


def load_case(name):
    if name not in names():
        raise ValueError('unknown case')
    raw = (DATA / (name + '.json')).read_bytes()
    case = json.loads(raw)
    case['_snapshot'] = hashlib.sha256(raw).hexdigest()
    return case


def unique(rows, field):
    indexed = {}
    for row in rows:
        identifier = row[field]
        if identifier in indexed:
            raise ValueError('duplicate ' + field + ': ' + str(identifier))
        indexed[identifier] = row
    return indexed


def finite_number(value):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite numeric value required')
    return value
