import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base, route, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    query = Request(base + route, data=body, headers={'Content-Type': 'application/json'})
    with urlopen(query, timeout=15) as response:
        return json.load(response)


def analysis(base, case='standard', **options):
    return request(base, '/infer', {'case': case, **options})
