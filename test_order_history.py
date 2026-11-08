import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
from order_history import list_history, template_for_order


class HistoryTests(unittest.TestCase):
    def test_old_record_restores_only_known_fields(self):
        old = {'gpu_model': 'RTX 5090', 'gpu_count': 1, 'expand_disk_gb': 500,
               'creation_method': 'clone', 'clone_copy_data': True, 'password': 'secret'}
        template, complete = template_for_order(old)
        self.assertFalse(complete)
        self.assertEqual(template['gpu_models'], ['RTX 5090'])
        self.assertEqual(template['gpu_counts'], [1])
        self.assertNotIn('clone_source_uuid', template)
        self.assertNotIn('password', template)
        self.assertFalse(template['schedule_enabled'])

    def test_current_and_archived_are_visible_but_sensitive_fields_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d)
            current = local / 'order.json'
            current.write_text(json.dumps({'uuid': 'abcde', 'state': 'accepted', 'created_at': 2,
                                           'token': 'secret'}), encoding='utf8')
            (local / 'order-archived-123.json').write_text(json.dumps({'created_at': 1}), encoding='utf8')
            rows = list_history(local, current)
            self.assertEqual([r['current'] for r in rows], [True, False])
            self.assertEqual(len(rows[0]['fingerprint']), 64)
            self.assertNotIn('secret', json.dumps(rows))

    def test_restored_full_template_cannot_reactivate_stale_schedule(self):
        saved = copy.deepcopy(monitor.DEFAULTS)
        saved.update(schedule_enabled=True, schedule_at='2020-01-01T12:00:00+08:00')
        template, complete = template_for_order({'template': saved})
        self.assertTrue(complete)
        self.assertFalse(template['schedule_enabled'])
        self.assertEqual(template['schedule_at'], '')

    def test_corrupt_current_record_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'order.json'
            p.write_bytes(b'{broken')
            rows = list_history(Path(d), p)
            self.assertTrue(rows[0]['unreadable'])
            self.assertTrue(rows[0]['current'])
            self.assertTrue(p.exists())


if __name__ == '__main__':
    unittest.main()
