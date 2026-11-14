import json
import threading
import unittest
from urllib.error import HTTPError

from app.client import request
from app.server import make_server
from app.store import load_case, names


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server()
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=3)
        cls.server.server_close()

    def test_all_previews_over_real_http(self):
        self.assertEqual(request(self.base, '/cases')['cases'], names())
        for case in names():
            result = request(self.base, '/preview', {'case': case})
            self.assertEqual(result['snapshot'], load_case(case)['_snapshot'])
            json.dumps(result, allow_nan=False)

    def test_missing_case_is_bad_request(self):
        with self.assertRaises(HTTPError) as caught:
            request(self.base, '/preview', {'case': 'missing'})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_snapshot_changes_with_content(self):
        self.assertNotEqual(load_case('standard')['_snapshot'], load_case('edge')['_snapshot'])
