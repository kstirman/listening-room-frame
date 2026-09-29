import json,tempfile,unittest
from pathlib import Path
from bridge import Bridge

class MultiDiscHistory(unittest.TestCase):
 def test_same_album_across_numbered_disc_folders(self):
  def key(folder,album='Box',parent='Artist/Box'):
   return Bridge.album_key({'album':album,'resource':'http://nuc/music/'+parent+'/'+folder+'/1.flac','albumArtURI':'http://nuc/art.jpg'})
  for folder in ['CD*2001','CD%2002','CD3','Disc 4','Disk_5']:
   self.assertEqual(key(folder),key('CD1'))
  self.assertNotEqual(key('CD1'),key('CD2','Another album'))
  self.assertNotEqual(key('CD1'),key('CD1',parent='Artist/Other edition'))
  self.assertNotEqual(key('Concert1'),key('Concert2'))
 def test_verified_legacy_folder_names(self):
  for a,b in [('local:/Scherchen/Platz.HS.LvB1:Beethoven - Symphonies 1-9','local:/Scherchen/Platz.HS.LvB6:Beethoven - Symphonies 1-9'),('local:/Various/The_Cobra_Records_Story_Disc_1:The Cobra Records Story','local:/Various/The_Cobra_Records_Story_Disc_2:The Cobra Records Story')]:
   self.assertEqual(Bridge.canonical_album_key(a),Bridge.canonical_album_key(b))
 def test_migration_keeps_most_recent_and_cover_and_current_state(self):
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'state.json';newest='local:/music/Box/CD*2002:Box';oldest='local:/music/Box/CD*2001:Box'
   p.write_text(json.dumps({'album':newest,'display_key':newest+':{"work":"x"}','history':[{'key':newest,'cover':'new.jpg'},{'key':'qobuz:xyz','cover':'q.jpg'},{'key':oldest,'cover':'old.jpg'}],'collage_revision':'old'}))
   b=Bridge({},root)
   self.assertEqual([x['cover'] for x in b.state['history']],['new.jpg','q.jpg'])
   self.assertEqual(b.state['album'],'local:/music/Box:Box')
   self.assertEqual(b.state['display_key'],'local:/music/Box:Box:{"work":"x"}')
   self.assertNotIn('collage_revision',b.state)
   self.assertEqual(Bridge({},root).state,b.state)
if __name__=='__main__':unittest.main()
