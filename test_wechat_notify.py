import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import requests
import monitor
import ui
import wechat_notify as wechat


class WechatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'wechat.json'
        self.patch = patch.object(wechat, 'SETTINGS', self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_disabled_by_default_and_token_is_bound_to_current_account(self):
        self.assertFalse(wechat.load_settings()['enabled'])
        wechat.save_settings(True, 'developer-token', 123)
        self.assertTrue(wechat.load_settings()['enabled'])
        with self.assertRaisesRegex(ValueError, '账号'):
            wechat.save_settings(True, '', 456)
        self.assertEqual(wechat.load_settings()['account_id'], '123')
        wechat.save_settings(False, '', None, clear=True)
        self.assertEqual(wechat.load_settings()['token'], '')

    def test_send_uses_official_endpoint_and_detects_business_failure(self):
        settings = {'token': 'developer-token', 'account_id': '123'}
        response = Mock()
        response.json.return_value = {'code': 'Success'}
        with patch.object(wechat.requests, 'post', return_value=response) as post:
            wechat.send_message(settings, 123, 'title', 'name', 'content')
        self.assertEqual(post.call_args.args[0], wechat.URL)
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'developer-token')
        self.assertEqual(post.call_args.kwargs['json']['content'], 'content')
        response.json.return_value = {'code': 'Failed', 'msg': 'developer-token'}
        with patch.object(wechat.requests, 'post', return_value=response):
            with self.assertRaises(ValueError) as err:
                wechat.send_message(settings, 123, 'title', 'name', 'content')
        self.assertNotIn('developer-token', str(err.exception))

    def test_account_mismatch_never_sends(self):
        with patch.object(wechat.requests, 'post') as post:
            with self.assertRaisesRegex(ValueError, '账号'):
                wechat.send_message({'token': 'abc', 'account_id': '123'}, 456, 't', 'n', 'c')
        post.assert_not_called()

    def test_notification_failure_never_changes_success_result(self):
        worker = monitor.Monitor()
        worker.log = Mock()
        with patch.object(monitor, 'AutoDL', side_effect=requests.Timeout()):
            worker._notify_success({'uuid': 'instance-1', 'source': 'new'}, {'token': 'secret'})
        self.assertTrue(any('抢卡结果不受影响' in c.args[0] for c in worker.log.call_args_list))
        self.assertNotIn('secret', str(worker.log.call_args_list))

    def test_one_success_triggers_at_most_one_notification(self):
        worker = monitor.Monitor()
        worker.log = Mock()
        with patch.object(wechat, 'load_settings', return_value={'enabled': True}), \
             patch.object(monitor.threading, 'Thread') as thread:
            worker.finish({'uuid': 'instance-1', 'source': 'existing'})
            worker.finish({'uuid': 'instance-1', 'source': 'existing'})
        self.assertEqual(thread.call_count, 1)
        thread.return_value.start.assert_called_once()
        self.assertEqual(worker.status['phase'], 'success')

    def test_corrupt_settings_fail_without_exposing_contents(self):
        self.path.write_text('{secret-invalid', encoding='utf8')
        with self.assertRaises(ValueError) as err:
            wechat.load_settings()
        self.assertNotIn('secret-invalid', str(err.exception))

    def test_local_api_returns_only_notification_status(self):
        wechat.save_settings(True, 'private-developer-token', 123)
        cls = ui.make_handler(ui.App())
        handler = cls.__new__(cls)
        handler.server = SimpleNamespace(server_port=8765)
        handler.headers = {'Host': '127.0.0.1:8765'}
        handler.path = '/api/wechat-notification'
        handler.send = Mock()
        handler.do_GET()
        result = handler.send.call_args.args[0]
        self.assertEqual(result, {'enabled': True, 'has_token': True})
        self.assertNotIn('private-developer-token', str(result))


if __name__ == '__main__':
    unittest.main()
