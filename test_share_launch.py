import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import share_main

class LaunchTests(TestCase):
 def test_direct_entry_runs_single_foreground_server(self):
  with patch('ui.main') as run, patch('share_main.webbrowser.open') as opened, patch.object(sys,'argv',['app.exe']):
   share_main.main();run.assert_called_once();opened.assert_not_called();self.assertEqual(sys.argv,['app.exe','--port','18765'])
 def test_bat_entry_matches_direct(self):
  with patch('ui.main') as run, patch('share_main.webbrowser.open') as opened, patch.object(sys,'argv',['app.exe','--serve']):
   share_main.main();run.assert_called_once();opened.assert_not_called();self.assertEqual(sys.argv,['app.exe','--port','18765'])
 def test_same_service_reused(self):
  error=OSError(98,'address in use')
  with patch('ui.main',side_effect=error),patch('share_main.inspect',return_value=True),patch('share_main.webbrowser.open') as opened,patch.object(sys,'argv',['app.exe']):
   share_main.main();opened.assert_called_once_with(share_main.URL)
 def test_unknown_service_not_opened(self):
  error=OSError(98,'address in use')
  with patch('ui.main',side_effect=error),patch('share_main.inspect',return_value=False),patch('share_main.webbrowser.open') as opened,patch.object(sys,'argv',['app.exe']):
   with self.assertRaises(SystemExit): share_main.main()
   opened.assert_not_called()
 def test_other_errors_not_misreported_as_port_conflicts(self):
  with patch('ui.main',side_effect=OSError(5,'permission denied')),patch.object(sys,'argv',['app.exe']):
   with self.assertRaises(OSError): share_main.main()

if __name__=='__main__':main()
