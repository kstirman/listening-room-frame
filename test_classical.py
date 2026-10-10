import tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
from classical import Tracks,normalize
from bridge import Bridge

class ClassicalTests(unittest.TestCase):
 def test_recital_standalone_titles_and_disc_labels(self):
  p=Tracks();p.feed('''<div id="playerTracks"><p class="player__work">Two Pieces</p><div data-track="1" data-gtm='{"product":{"category":"classique"}}'><div class="track__item--name">No. 1, Nocturne</div></div><div data-track="2" data-gtm='{"product":{"category":"classique"}}'><div class="track__item--name">Barcarolle No. 5, Op. 66</div></div><p class="player__work" data-disk="2">DISC 2</p><div data-track="3" data-gtm='{"product":{"category":"jazz"}}'><div class="track__item--name">A Song</div></div></div>''')
  self.assertEqual(normalize(p.tracks['1'])['work'],'Two Pieces')
  self.assertEqual(normalize(p.tracks['2'])['work'],'Barcarolle No. 5, Op. 66')
  self.assertNotIn('movementName',p.tracks['2'])
  self.assertIsNone(normalize(p.tracks['3']))
 def test_full_work_title_remains_independent_of_previous_heading(self):
  p=Tracks();p.feed('<div id="playerTracks"><p class="player__work">Previous Work</p><div data-track="1"><div class="track__item--name">Different Concerto: II. Adagio</div></div></div>')
  self.assertEqual(normalize(p.tracks['1'])['work'],'Different Concerto')
  self.assertEqual(normalize(p.tracks['1'])['movement'],'II. Adagio')
 def test_work_headings_attach_to_exact_tracks_without_genre(self):
  p=Tracks();p.feed('''<div id="playerTracks"><div class="player__tracks"><p class="player__work">Symphony No. 6 <span>in F Major</span></p><div data-track="1"><div class="track__item--name">I. <span>Allegro</span></div></div></div><div class="player__tracks"><div data-track="2"><div class="track__item--name">II. Andante</div></div></div><div class="player__tracks"><p class="player__work">Symphony No. 7</p><div data-track="3"><div class="track__item--name">I. Vivace</div></div></div></div><div id="playerTracks"><div data-track="4"><div class="track__item--name">Unrelated piece</div></div></div>''')
  first=normalize(p.tracks['1']);second=normalize(p.tracks['2'])
  self.assertEqual(first['work'],'Symphony No. 6 in F Major')
  self.assertEqual(first['movement'],'I. Allegro')
  self.assertEqual(second['work'],first['work'])
  self.assertEqual(second['movement'],'II. Andante')
  self.assertEqual(normalize(p.tracks['3'])['work'],'Symphony No. 7')
  self.assertNotIn('work',p.tracks['4'])
  self.assertIsNone(normalize(p.tracks['4']))
 def test_qobuz_grouped_work_reaches_bridge_details(self):
  with tempfile.TemporaryDirectory() as root:
   b=Bridge({},root)
   b.album_page=Mock(return_value='<div id="playerTracks"><div class="player__tracks"><p class="player__work">Symphony No. 6</p><div data-track="22"><div class="track__item--name">II. Andante</div></div></div></div>')
   d=b.details({'album_id':'example','object_id':'qobuz/track/22'})
   self.assertEqual(d['work'],'Symphony No. 6')
   self.assertEqual(d['movement'],'II. Andante')
 def test_placeholder_titles_do_not_become_classical_works(self):
  for title in ['Track01','Track 02','Unknown Title','Untitled']:
   with self.subTest(title=title):
    d=normalize({'classical':True,'title':title,'soloist':'Ervin Nyiregyházi'})
    self.assertNotIn('work',d)
    self.assertEqual(d['soloist'],'Ervin Nyiregyházi')
    self.assertEqual(normalize({'classical':True,'title':title,'work':'Verified work'})['work'],'Verified work')
  self.assertEqual(normalize({'classical':True,'title':'Adagio'})['work'],'Adagio')
 def test_roles_and_movement(self):
  d=normalize({'title':'Symphony No. 4: I. Bewegt, nicht zu schnell','credits':'Anton Bruckner, Composer - Sergiu Celibidache, Conductor, MainArtist - Münchner Philharmoniker, Orchestra'})
  self.assertEqual(d['composer'],'Anton Bruckner');self.assertEqual(d['conductor'],'Sergiu Celibidache');self.assertEqual(d['movement'],'I. Bewegt, nicht zu schnell')
 def test_jazz_composer_is_not_classical(self):
  self.assertIsNone(normalize({'title':'Basin Street Blues','composer':'Spencer Williams','genre':'Jazz'}))
 def test_missing_roles_not_guessed(self):
  d=normalize({'genre':'Classical','title':'Adagio','artist':'Brahms'})
  self.assertNotIn('composer',d);self.assertNotIn('conductor',d)
 def test_track_changes_same_album_refresh_card(self):
  with tempfile.TemporaryDirectory() as root:
   b=Bridge({'idle_seconds':120},root)
   b.state.update(album='qobuz:a',owned='OLD',album_image='OLD',original='ORIGINAL',history=[])
   b.playback=Mock(return_value=('PLAYING',{'album_id':'a','object_id':'qobuz/track/2'}))
   b.details=Mock(return_value={'composer':'Composer','work':'Symphony','movement':'II. Andante'})
   b.card=Mock(return_value=(Path('card.jpg'),'Album'))
   tv=Mock();tv.get_artmode.return_value='on';tv.get_current.side_effect=[{'content_id':'OLD'},{'content_id':'NEW'}];tv.upload.return_value='NEW'
   b.television=Mock(return_value=tv);b.step()
   tv.select_image.assert_called_once_with('NEW');self.assertEqual(b.state['classical']['movement'],'II. Andante')
   tv.delete.assert_called_once_with('OLD')
 def test_track_parser_matches_exact_id(self):
  p=Tracks();p.feed('<div data-track="1"><div class="track__item--name">First</div><p class="track__info">A, Composer</p></div></div><div class="player__tracks"><div data-track="2"><div class="track__item--name">Second</div></div>')
  self.assertEqual(p.tracks['1']['credits'],'A, Composer');self.assertEqual(p.tracks['2']['title'],'Second');self.assertNotIn('credits',p.tracks['2'])
if __name__=='__main__':unittest.main()

class ClassicalCoverageTests(unittest.TestCase):
 def test_reviewed_classical_and_genre_aliases(self):
  for genre in ['Opera','Classique','Symphony','Chamber music','Medieval']:
   self.assertIsNotNone(normalize({'genre':genre,'title':'Piece'}))
  self.assertIsNotNone(normalize({'classical':True,'title':'Piece','genre':''}))
 def test_composer_does_not_turn_jazz_into_classical(self):
  self.assertIsNone(normalize({'genre':'Jazz','composer':'Duke Ellington','title':'Piece'}))
 def test_ensemble_and_choir_are_displayed(self):
  self.assertEqual(normalize({'genre':'Opera','ensemble':'Ensemble','title':'Piece'})['orchestra'],'Ensemble')
