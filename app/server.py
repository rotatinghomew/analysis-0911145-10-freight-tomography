import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.store import load_case, names
from app.flows import preview
from app.inversion import infer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def send_json(self, code, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'status': 'ok'})
        elif self.path == '/cases':
            self.send_json(200, {'cases': names()})
        else:
            self.send_json(404, {'error': 'unknown route'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1048576:
                raise ValueError('invalid body size')
            request = json.loads(self.rfile.read(length))
            case = load_case(request['case'])
            route = urlsplit(self.path).path
            if route == '/preview':
                self.send_json(200, preview(case))
            elif route == '/infer':
                self.send_json(200, infer(case, request.get('closure')))
            else:
                self.send_json(404, {'error': 'unknown route'})
                return
        except (ValueError, KeyError, TypeError) as error:
            self.send_json(400, {'error': str(error)})


def make_server(port=0):
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    import os
    with make_server(int(os.environ.get('PORT', '8080'))) as server:
        print('listening on ' + str(server.server_address), flush=True)
        server.serve_forever()
