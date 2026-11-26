import json,tempfile,unittest
from unittest.mock import Mock,patch
from bridge import Bridge
class ArtModeTests(unittest.TestCase):
 def test_standby_artmode_is_usable(self):
  with tempfile.TemporaryDirectory() as d:
   b=Bridge({'tv':'192.0.2.1','tv_model':'Frame'},d)
   b.session=Mock();b.session.get.return_value.content=json.dumps({'device':{'modelName':'Frame','PowerState':'standby'}}).encode()
   tv=Mock();tv.get_artmode.return_value='on'
   with patch('bridge.SamsungTVArt',return_value=tv):self.assertIs(b.television(),tv)
   self.assertEqual([x[0] for x in tv.method_calls],['get_artmode'])
 def test_non_art_mode_closed_without_control(self):
  with tempfile.TemporaryDirectory() as d:
   b=Bridge({'tv':'192.0.2.1','tv_model':'Frame'},d)
   b.session=Mock();b.session.get.return_value.content=json.dumps({'device':{'modelName':'Frame','PowerState':'on'}}).encode()
   tv=Mock();tv.get_artmode.return_value='off'
   with patch('bridge.SamsungTVArt',return_value=tv):self.assertIsNone(b.television())
   self.assertEqual([x[0] for x in tv.method_calls],['get_artmode','close'])
