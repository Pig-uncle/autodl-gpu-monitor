import unittest
from unittest.mock import Mock

from monitor import AutoDL


class RegionModelTests(unittest.TestCase):
    def setUp(self):
        self.api = AutoDL.__new__(AutoDL)
        self.api.call = Mock()

    def test_reads_all_pages_and_filters_region_and_cpu(self):
        self.api.call.side_effect = [
            {'list': [{'gpu_name': 'RTX 5090', 'region_sign': 'west-D'},
                      {'gpu_name': 'RTX 4090', 'region_sign': 'other'}], 'max_page': 2},
            {'list': [{'gpu_name': 'RTX 6000D', 'region_sign': 'west-D'},
                      {'gpu_name': 'CPU', 'region_sign': 'west-D'},
                      {'gpu_name': 'RTX 5090', 'region_sign': 'west-D'}], 'max_page': 2},
        ]
        self.assertEqual(self.api.region_gpu_models(['west-D', 'west-D']), ['RTX 5090', 'RTX 6000D'])
        payloads = [call.args[2] for call in self.api.call.call_args_list]
        self.assertEqual([p['page_index'] for p in payloads], [1, 2])
        self.assertTrue(all(p['gpu_idle_num'] == 0 and p['region_sign_list'] == ['west-D'] for p in payloads))

    def test_empty_and_incomplete_catalog_are_distinct(self):
        self.api.call.return_value = {'list': [], 'max_page': 0}
        self.assertEqual(self.api.region_gpu_models(['west-D']), [])
        self.api.call.return_value = {'list': [], 'max_page': 2}
        with self.assertRaisesRegex(ValueError, '分页不完整'):
            self.api.region_gpu_models(['west-D'])

    def test_invalid_region_does_not_query_platform(self):
        for invalid in ([], [''], [None], ['west-D,west-C'], 'west-D'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.api.region_gpu_models(invalid)
        self.api.call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
