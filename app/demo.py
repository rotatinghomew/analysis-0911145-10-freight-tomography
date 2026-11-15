import json
import threading

from app.client import analysis, request
from app.server import make_server


def main():
    with make_server() as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        print(json.dumps(request(base, '/health'), ensure_ascii=False))
        for case in request(base, '/cases')['cases']:
            # Existing preview capability stays available unchanged.
            preview = request(base, '/preview', {'case': case})
            print(json.dumps({'case': case, 'preview': preview}, ensure_ascii=False))
            # New demand inversion over real HTTP: counters, path samples and
            # business bounds jointly invert to OD flows with identified ranges.
            result = analysis(base, case)
            print(json.dumps({'case': case, 'inference': result}, ensure_ascii=False))
        # Closure can be overridden per request; here a corridor with no
        # reroute uncertainty is requested, which only projects demand.
        override = {'edges': ['h-y'], 'reroute_band': None}
        scenario_only = analysis(base, 'standard', closure=override)
        print(json.dumps({'case': 'standard', 'closure_override': override,
                          'scenarios': scenario_only['scenarios'],
                          'diagnostics': scenario_only['diagnostics']},
                         ensure_ascii=False))
        server.shutdown()
        worker.join(timeout=3)


if __name__ == '__main__':
    main()
