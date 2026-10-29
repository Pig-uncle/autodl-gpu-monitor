"""No live mutations: exercise billing boundaries and fallback state transitions."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests
import monitor as m


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(m, 'LOCAL', self.root),
                        patch.object(m, 'JOURNAL', self.root / 'order.json'),
                        patch.object(m, 'load_authorization', return_value='')]
        for p in self.patches:
            p.start()
        self.config = copy.deepcopy(m.DEFAULTS)
        self.config['strategy'] = 'existing_then_new'
        self.config['fallback_after_minutes'] = 0

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def machine(self, **changes):
        return dict(machine_id='machine-1', gpu_name='RTX PRO 6000', gpu_idle_num=2,
                    max_data_disk_expand_size=300*m.GIB, on_line=2,
                    binding_instance_num=1, max_instance_num=10, rent_mode='', payg_price=7000, **changes)

    def stopped(self):
        return {'uuid': self.config['instance_uuid'], 'status': 'shutdown', 'start_mode': 'gpu'}

    def run_monitor(self, api):
        worker = m.Monitor()
        with patch.object(m, 'AutoDL', return_value=api):
            worker._run(self.config)
        return worker

    def test_disk_bytes_in_both_order_sections(self):
        order = m.create_payload(self.config, 'm1', 2)
        self.assertEqual(order['instance_info']['expand_data_disk'], 250*m.GIB)
        self.assertEqual(order['price_info']['expand_data_disk'], 250*m.GIB)
        self.assertEqual(order['instance_info']['req_gpu_amount'], 2)

    def test_disk_shortage_and_offline_are_excluded(self):
        row = self.machine()
        self.assertTrue(m.eligible(row, 'RTX PRO 6000', 1, 250))
        for field, value in [('max_data_disk_expand_size', 200*m.GIB), ('on_line', 3),
                             ('gpu_idle_num', 0), ('binding_instance_num', 10)]:
            changed = {**row, field: value}
            self.assertFalse(m.eligible(changed, 'RTX PRO 6000', 1, 250))

    def test_minimum_and_group_rental_respected(self):
        row = self.machine()
        row['rent_mode'] = 'Base2'
        self.assertFalse(m.eligible(row, 'RTX PRO 6000', 1, 250))
        self.assertTrue(m.eligible(row, 'RTX PRO 6000', 2, 250))
        row['rent_mode'] = 'Group2'
        self.assertFalse(m.eligible(row, 'RTX PRO 6000', 1, 250))

    def test_running_existing_never_buys(self):
        api = Mock()
        api.instance.return_value = {**self.stopped(), 'status': 'running'}
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'success')
        api.start.assert_not_called()
        api.machines.assert_not_called()

    def test_accepted_power_on_waits_and_never_falls_back(self):
        api = Mock()
        api.instance.side_effect = [self.stopped(), {**self.stopped(), 'status': 'running'}]
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'success')
        api.start.assert_called_once()
        api.machines.assert_not_called()

    def test_no_gpu_falls_back_once_and_stops(self):
        api = Mock()
        api.start.side_effect = m.ApiError('InsufficientGPU', 'GPU不足')
        api.instance.side_effect = [self.stopped(), self.stopped(),
            {'uuid': 'new-uuid', 'status': 'running', 'start_mode': 'gpu'}]
        api.machines.return_value = [self.machine()]
        api.call.side_effect = [{'data_disk_info': {'data_disk_deal_daily_price': 1660}}, 'new-uuid']
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'success')
        self.assertEqual(worker.snapshot()['result']['gpu_count'], 1)
        self.assertEqual(json.loads(m.JOURNAL.read_text())['uuid'], 'new-uuid')
        self.assertEqual(len([x for x in api.call.call_args_list if x.args[1].endswith('create/payg')]), 1)

    def test_purchase_timeout_blocks_repeat_on_restart(self):
        self.config['strategy'] = 'new_only'
        api = Mock()
        api.machines.return_value = [self.machine()]
        api.call.side_effect = [{'data_disk_info': {'data_disk_deal_daily_price': 1660}}, requests.Timeout()]
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'error')
        self.assertTrue(m.JOURNAL.exists())
        api2 = Mock()
        self.run_monitor(api2)
        api2.machines.assert_not_called()
        api2.call.assert_not_called()

    def test_auth_failure_never_falls_back(self):
        api = Mock()
        api.instance.return_value = self.stopped()
        api.start.side_effect = m.ApiError('AuthorizeFailed', '登录超时')
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'error')
        api.machines.assert_not_called()

    def test_cpu_only_running_is_not_success(self):
        api = Mock()
        api.instance.return_value = {**self.stopped(), 'status': 'running', 'start_mode': 'non_gpu'}
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'error')
        api.machines.assert_not_called()

    def test_budget_includes_disk_daily_equivalent(self):
        self.config.update(strategy='new_only', max_hourly_price=7.01)
        api = Mock()
        worker = m.Monitor()
        def machines(*args):
            worker.stop_event.set()
            return [self.machine()]
        # Stop after a complete first round without entering the polling wait.
        api.machines.return_value = [self.machine()]
        def call(method, path, payload):
            if path.endswith('preview'):
                worker.stop_event.set()
                return {'data_disk_info': {'data_disk_deal_daily_price': 2400}}
            self.fail('Budget must prevent creation')
        api.call.side_effect = call
        worker._buy_new(api, self.config)
        self.assertFalse(m.JOURNAL.exists())

    def test_invalid_configuration_rejected(self):
        for key, value in [('poll_seconds', 0), ('gpu_counts', [0]),
                           ('expand_disk_gb', -1), ('max_hourly_price', -5),
                           ('fallback_after_minutes', float('nan'))]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.validate_config({**self.config, key: value})

    def test_missing_disk_quote_prevents_purchase(self):
        self.config['strategy'] = 'new_only'
        api = Mock()
        api.machines.return_value = [self.machine()]
        api.call.return_value = {'data_disk_info': {}}
        worker = self.run_monitor(api)
        self.assertEqual(worker.snapshot()['phase'], 'error')
        self.assertFalse(m.JOURNAL.exists())

    def run_parallel(self, old, new):
        self.config['strategy'] = 'parallel'
        worker = m.Monitor()
        def factory():
            return old if threading.current_thread().name == 'autodl-existing' else new
        with patch.object(m, 'AutoDL', side_effect=factory):
            run = threading.Thread(target=worker._run_parallel, args=(self.config,), daemon=True)
            run.start()
            run.join(3)
            if run.is_alive():
                worker.stop()
                run.join(2)
                self.fail('Parallel workers did not finish promptly')
        return worker

    def test_parallel_search_overlaps_existing_and_existing_win_prevents_order(self):
        market_entered, accepted = threading.Event(), threading.Event()
        old, new = Mock(), Mock()
        def old_instance(uuid):
            self.assertTrue(market_entered.wait(1))
            return {**self.stopped(), 'status': 'running' if accepted.is_set() else 'shutdown'}
        def start(uuid):
            accepted.set()
        def machines(*args):
            market_entered.set()
            self.assertTrue(accepted.wait(1))
            return [self.machine()]
        old.instance.side_effect = old_instance
        old.start.side_effect = start
        new.machines.side_effect = machines
        worker = self.run_parallel(old, new)
        self.assertEqual(worker.snapshot()['result']['source'], 'existing')
        new.call.assert_not_called()
        old.start.assert_called_once()

    def test_parallel_new_win_stops_existing_retry(self):
        attempted = threading.Event()
        old, new = Mock(), Mock()
        old.instance.return_value = self.stopped()
        def start(uuid):
            attempted.set()
            raise m.ApiError('BadRequest', '机器空闲GPU不足')
        old.start.side_effect = start
        def machines(*args):
            self.assertTrue(attempted.wait(1))
            return [self.machine()]
        new.machines.side_effect = machines
        new.instance.side_effect = [self.stopped(), {'uuid': 'new-uuid', 'status': 'running', 'start_mode': 'gpu'}]
        new.call.side_effect = [{'data_disk_info': {'data_disk_deal_daily_price': 1660}}, 'new-uuid']
        worker = self.run_parallel(old, new)
        self.assertEqual(worker.snapshot()['result']['source'], 'new')
        self.assertEqual(worker.snapshot()['result']['uuid'], 'new-uuid')
        old.start.assert_called_once()

    def test_parallel_purchase_timeout_stops_both_branches(self):
        attempted = threading.Event()
        old, new = Mock(), Mock()
        old.instance.return_value = self.stopped()
        def start(uuid):
            attempted.set()
            raise m.ApiError('BadRequest', '机器空闲GPU不足')
        old.start.side_effect = start
        def machines(*args):
            self.assertTrue(attempted.wait(1))
            return [self.machine()]
        new.machines.side_effect = machines
        new.instance.return_value = self.stopped()
        new.call.side_effect = [{'data_disk_info': {'data_disk_deal_daily_price': 1660}}, requests.Timeout()]
        worker = self.run_parallel(old, new)
        self.assertEqual(worker.snapshot()['phase'], 'error')
        self.assertTrue(m.JOURNAL.exists())
        old.start.assert_called_once()
        self.assertTrue(worker.stop_event.is_set())

    def test_account_response_allowlists_fields_and_converts_currency(self):
        api = object.__new__(m.AutoDL)
        api.call = Mock(side_effect=[{'id': 42, 'username': '测试账号', 'phone': 'secret', 'open_id': 'secret'},
            {'assets': 276320, 'blocked_asset': 1500, 'voucher_balance': 1230, 'remittance_code': 'secret'}])
        result = api.account()
        self.assertEqual(result['balance'], 276.32)
        self.assertEqual(result['frozen_balance'], 1.5)
        self.assertEqual(result['voucher_balance'], 1.23)
        self.assertEqual(set(result), {'id', 'name', 'balance', 'frozen_balance', 'voucher_balance', 'updated_at'})

    def test_account_cache_and_manual_refresh(self):
        from ui import App
        app = App()
        with patch('ui.AutoDL') as api:
            api.return_value.account.return_value = {'id': 42, 'balance': 2}
            app.account()
            app.account()
            api.return_value.account.assert_called_once()
            app.account(force=True)
            self.assertEqual(api.return_value.account.call_count, 2)

    def test_parallel_cpu_running_keeps_new_search_active(self):
        seen = threading.Event()
        old, new = Mock(), Mock()
        def old_instance(uuid):
            seen.set()
            return {**self.stopped(), 'status': 'running', 'start_mode': 'non_gpu'}
        old.instance.side_effect = old_instance
        def machines(*args):
            self.assertTrue(seen.wait(1))
            return [self.machine()]
        new.machines.side_effect = machines
        new.instance.side_effect = [{**self.stopped(), 'status': 'running', 'start_mode': 'non_gpu'},
                                   {'uuid': 'new-uuid', 'status': 'running', 'start_mode': 'gpu'}]
        new.call.side_effect = [{'data_disk_info': {'data_disk_deal_daily_price': 1660}}, 'new-uuid']
        worker = self.run_parallel(old, new)
        self.assertEqual(worker.snapshot()['result']['source'], 'new')
        old.start.assert_not_called()

    def test_stop_inactive_error_clears_error_phase(self):
        worker = m.Monitor()
        worker.phase('error')
        worker.stop()
        self.assertEqual(worker.snapshot()['phase'], 'stopped')


if __name__ == '__main__':
    unittest.main()
