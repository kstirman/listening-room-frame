import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from setup_preview import PreviewBridge
import test_setup


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.b=PreviewBridge({'source':'roon','roon_state_dir':str(self.root/'roon')},self.root/'display')
        art=io.BytesIO();Image.new('RGB',(80,80),'#ae4930').save(art,format='JPEG')
        self.b.sonos=Mock();self.b.sonos.cover.return_value=art.getvalue()
        self.metadata={'source':'sonos','source_album_key':'sonos:example','object_id':'track1',
                       'album':'Example album','artist':'Example artist','sonos_lines':['First movement','Example artist','Example album']}

    def tearDown(self):self.tmp.cleanup()

    def manifest(self):return json.loads((self.root/'presentation.json').read_text())

    def test_prepared_is_exact_image_without_tv_confirmation(self):
        image,_=self.b.card(self.metadata,self.b.details(self.metadata))
        self.assertEqual((self.root/'preview.jpg').read_bytes(),image.read_bytes())
        data=self.manifest()
        self.assertEqual(data['revision'],hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertEqual(data['status'],'prepared');self.assertIsNone(data['confirmed_at'])

    def test_catalog_diagnostic_is_published(self):
        self.b.card(self.metadata, {'catalog_status':'ambiguous'})
        self.assertEqual(self.manifest()['catalog_status'],'ambiguous')

    def test_failed_selection_never_claims_confirmation(self):
        self.b.card(self.metadata,self.b.details(self.metadata))
        tv=Mock();tv.get_current.return_value={'content_id':'WRONG'}
        with self.assertRaises(RuntimeError):self.b.select(tv,'CARD')
        self.assertEqual(self.manifest()['status'],'prepared')

    def test_verified_selection_and_restoration_evidence(self):
        self.b.card(self.metadata,self.b.details(self.metadata))
        tv=Mock();current=['CARD'];tv.get_current.side_effect=lambda:{'content_id':current[0]}
        tv.select_image.side_effect=lambda value:current.__setitem__(0,value)
        tv.get_artmode.return_value='on';self.b.television=Mock(return_value=tv)
        self.b.state['original']='ORIGINAL'
        self.b.select(tv,'CARD')
        self.assertEqual(self.manifest()['status'],'confirmed')
        self.assertIsNotNone(self.manifest()['confirmed_at'])
        self.b.restore()
        self.assertEqual(current[0],'ORIGINAL')
        self.assertEqual(self.manifest()['status'],'released')

    def test_collage_preview_uses_actual_collage(self):
        image=self.b.root/'collage.jpg';Image.new('RGB',(160,90),'#305b8e').save(image)
        self.b.state['collage_image']='COLLAGE'
        tv=Mock();tv.get_current.return_value={'content_id':'COLLAGE'}
        self.b.select(tv,'COLLAGE')
        self.assertEqual(self.manifest()['kind'],'collage')
        self.assertEqual((self.root/'preview.jpg').read_bytes(),image.read_bytes())

    def test_preview_worker_never_connects_to_tv(self):
        import setup_worker
        (self.root/'request.json').write_text('{}');(self.root/'config.json').write_text('{"source":"sonos"}')
        b=Mock();b.playback.return_value=('PLAYING',{'title':'Example','album':'Album'});b.album_key.return_value='album'
        with patch('setup_worker.sys.argv',['worker',str(self.root),'preview']),patch('setup_worker.Bridge',return_value=b),patch('setup_worker.run_bounded',side_effect=lambda f,*args:f()),patch('setup_worker.signal.signal'):
            setup_worker.main()
        b.card.assert_called_once();b.television.assert_not_called();b.step.assert_not_called()
        self.assertTrue(json.loads((self.root/'result.json').read_text())['data']['prepared'])


class ImageHTTPTests(unittest.TestCase):
    setUp = test_setup.HTTPTests.setUp
    tearDown = test_setup.HTTPTests.tearDown
    request = test_setup.HTTPTests.request

    def test_images_require_auth_and_no_path_traversal_or_symlink(self):
        image=self.app.root/'preview.jpg';image.write_bytes(b'EXACT_IMAGE')
        headers={'Authorization':'Bearer '+self.app.token}
        self.assertEqual(self.request('/api/image/current')[0],401)
        self.assertEqual(self.request('/api/image/current',headers),(200,b'EXACT_IMAGE'))
        self.assertEqual(self.request('/api/cover/../../api-token',headers)[0],404)
        image.unlink();image.symlink_to(self.app.root/'api-token')
        self.assertEqual(self.request('/api/image/current',headers)[0],404)

    def test_recent_excludes_unexpected_paths(self):
        display=self.app.root/'display';display.mkdir()
        (display/'state.json').write_text(json.dumps({'history':[{'cover':'../../api-token','title':'bad'},{'cover':'covers/'+'a'*64+'.jpg','title':'Example'}]}))
        recent=self.app.status()['recent']
        self.assertEqual(len(recent),1);self.assertEqual(recent[0]['title'],'Example')

class PhotoPreviewTests(unittest.TestCase):
    def test_photo_selection_publishes_actual_photo(self):
        with tempfile.TemporaryDirectory() as t:
            b=PreviewBridge({},Path(t)/'display')
            image=b.root/'idle-photo.jpg';Image.new('RGB',(80,60),'navy').save(image)
            b._selecting_photo=True
            tv=Mock();tv.get_current.return_value={'content_id':'PHOTO'}
            b.select(tv,'PHOTO')
            self.assertEqual(json.loads((Path(t)/'presentation.json').read_text())['kind'],'photo')
            self.assertEqual((Path(t)/'preview.jpg').read_bytes(),image.read_bytes())
