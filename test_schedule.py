import copy,time,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
import monitor as m

class ScheduleTests(unittest.TestCase):
 def setUp(self):
  self.config=copy.deepcopy(m.DEFAULTS);self.config.update(strategy='new_only',schedule_enabled=True,schedule_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
 def test_invalid_and_past_times_rejected(self):
  for value in ['', 'nonsense', '2026-01-01T09:00', (datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()]:
   with self.subTest(value=value),self.assertRaises(ValueError):m.validate_config({**self.config,'schedule_at':value})
 def test_future_date_allowed_and_offset_respected(self):
  c=m.validate_config(self.config);self.assertTrue(c['schedule_enabled'])
  self.assertEqual(datetime.fromisoformat(c['schedule_at']).utcoffset(),timedelta(0))
 def test_scheduled_wait_is_cancelable_without_api_calls(self):
  w=m.Monitor();w.log=lambda _:None
  def cancel_wait(seconds):w.stop_event.set();return True
  with patch.object(w.stop_event,'wait',side_effect=cancel_wait),patch.object(m,'AutoDL') as api:w._run(self.config)
  api.assert_not_called();self.assertEqual(w.status['phase'],'stopped')
 def test_scheduled_wait_starts_only_when_due(self):
  w=m.Monitor();w.log=lambda _:None;self.config['schedule_at']=datetime.fromtimestamp(105,timezone.utc).isoformat()
  with patch.object(m.time,'time',side_effect=[100,105]),patch.object(w.stop_event,'wait',return_value=False) as wait,patch.object(m,'AutoDL') as api,patch.object(w,'_buy_new') as buy,patch.object(m,'JOURNAL',Mock(exists=Mock(return_value=False))):w._run(self.config)
  wait.assert_called_once_with(5);api.assert_called_once();buy.assert_called_once()
 def test_disabled_schedule_ignores_old_time(self):
  c=m.validate_config({**self.config,'schedule_enabled':False,'schedule_at':''});self.assertFalse(c['schedule_enabled'])

if __name__=='__main__':unittest.main()
