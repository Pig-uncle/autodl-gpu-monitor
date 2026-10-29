import unittest
from unittest.mock import patch
import threading
import login_helper as h

class PhoneLoginTests(unittest.TestCase):
    def test_send_requires_user_captcha(self):
        with patch.object(h, '_phone_api') as api:
            with self.assertRaises(ValueError): h.send_phone_code('13800138000', '+86', '')
            api.assert_not_called()
            h.send_phone_code('13800138000', '+86', 'verified-proof')
            self.assertEqual(api.call_args.args[0], 'send_message')
            self.assertEqual(api.call_args.args[1]['captcha_verify_param'], 'verified-proof')
    def test_phone_login_exchanges_ticket_and_saves_token_only(self):
        with patch.object(h, '_phone_api', side_effect=[{'ticket': 'ticket'}, {'token': 'token'}]) as api, patch.object(h, 'update_config_authorization') as save:
            h.phone_login('13800138000', '+86', '123456')
            self.assertEqual([c.args[0] for c in api.call_args_list], ['fast_login', 'passport'])
            self.assertEqual(api.call_args_list[0].args[1]['v_code'], '123456')
            save.assert_called_once_with('token')
    def test_invalid_code_and_phone_do_not_call_api(self):
        with patch.object(h, '_phone_api') as api:
            for phone, code in [('invalid', '123456'), ('13800138000', 'oops')]:
                with self.assertRaises(ValueError): h.phone_login(phone, '+86', code)
            api.assert_not_called()
    def test_login_failure_never_saves(self):
        with patch.object(h, '_phone_api', side_effect=RuntimeError('failed')), patch.object(h, 'update_config_authorization') as save:
            with self.assertRaises(RuntimeError): h.phone_login('13800138000', '+86', '123456')
            save.assert_not_called()

if __name__ == '__main__': unittest.main()
