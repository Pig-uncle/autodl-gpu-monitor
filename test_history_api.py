import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ui


class HistoryApiTests(unittest.TestCase):
    def request(self, path, payload, app=None):
        app = app or ui.App()
        cls = ui.make_handler(app)
        handler = cls.__new__(cls)
        body = json.dumps(payload).encode()
        handler.server = SimpleNamespace(server_port=8765)
        handler.headers = {'Host': '127.0.0.1:8765', 'Content-Type': 'application/json',
                           'Content-Length': str(len(body))}
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler.send = Mock()
        handler.do_POST()
        return handler.send.call_args

    def test_bills_are_read_only_and_do_not_leak_account_fields(self):
        api = Mock()
        api.call.return_value = {'list': [{'asset': 1234, 'bill_type': 'charge',
                                           'user_phone': 'secret', 'uid': 'secret'}],
                                 'max_page': 2, 'result_total': 11}
        with patch.object(ui, 'AutoDL', return_value=api):
            response = self.request('/api/bills', {'page': 2}).args[0]
        self.assertEqual(response['bills'], [{'asset': 1234, 'bill_type': 'charge'}])
        self.assertEqual(response['page'], 2)
        self.assertEqual(api.call.call_args.args[:2], ('POST', 'bill/list'))
        self.assertEqual(api.call.call_args.args[2]['page_index'], 2)

    def test_changed_order_cannot_be_confirmed_from_stale_history(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Path(d) / 'order.json'
            journal.write_text('{"state":"submitting"}', encoding='utf8')
            app = ui.App()
            with patch.object(ui, 'JOURNAL', journal), patch.object(ui, 'LOCAL', Path(d)):
                response = self.request('/api/reset-order', {'fingerprint': 'stale'}, app)
            self.assertEqual(response.args[1], 400)
            self.assertTrue(journal.exists())
            self.assertEqual(list(Path(d).glob('order-archived-*')), [])


if __name__ == '__main__':
    unittest.main()
