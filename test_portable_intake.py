import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image
from intake.prepare import prepare, digest, open_audio
from mutagen.flac import FLAC, Picture
from mutagen.id3 import TXXX, APIC


class PortableIntakeTests(unittest.TestCase):
    def test_roundtrip_all_supported_formats_and_rollback(self):
        for ext, codec in [('flac','flac'), ('wav','pcm_s24le'), ('aiff','pcm_s24be')]:
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'source';root.mkdir();track=root/('track.'+ext)
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.1','-c:a',codec,str(track)],check=True)
                Image.new('RGB',(32,32),'blue').save(root/'cover.png')
                audio=open_audio(track)
                if audio.tags is None: audio.add_tags()
                if isinstance(audio,FLAC):
                    audio['COMMENT']=['Keep me']
                    picture=Picture();picture.type=4;picture.mime='image/png';picture.data=(root/'cover.png').read_bytes();audio.add_picture(picture)
                else:
                    audio.tags.add(TXXX(encoding=3,desc='COMMENT',text=['Keep me']))
                    audio.tags.add(APIC(encoding=3,mime='image/png',type=4,desc='Back cover',data=(root/'cover.png').read_bytes()))
                audio.save()
                before=digest(track)
                fields={'title':'Reviewed title','composer':'Composer','recordingDate':'circa 1951','releaseDate':'2000','compositionDate':'1808','work':'Work','movementName':'II. Slow'}
                plan={'tracks':[{'path':track.name,'sha256':before,'review_status':'reviewed','source':'Synthetic fixture','reviewed_at':'2026-01-01','fields':fields,'artwork':{'path':'cover.png','sha256':digest(root/'cover.png')}}]}
                output=Path(tmp)/'output';result=prepare(root,plan,output)
                self.assertEqual(digest(track),before)
                self.assertEqual(digest(output/'originals'/track.name),before)
                self.assertNotEqual(result[0]['prepared_sha256'],before)
                self.assertTrue((output/'verified.json').exists())
                changed=open_audio(output/'prepared'/track.name)
                if isinstance(changed,FLAC):
                    self.assertEqual(changed['TITLE'],['Reviewed title'])
                    self.assertEqual(changed['COMMENT'],['Keep me'])
                    self.assertEqual(changed['RECORDINGDATE'],['circa 1951'])
                    self.assertEqual(sorted(p.type for p in changed.pictures),[3,4])
                else:
                    self.assertEqual(changed.tags['TIT2'].text,['Reviewed title'])
                    self.assertEqual(changed.tags['TXXX:COMMENT'].text,['Keep me'])
                    self.assertNotIn('TDRC',changed.tags)
                    self.assertEqual(changed.tags['TXXX:RECORDINGDATE'].text,['circa 1951'])
                    self.assertEqual(sorted(p.type for p in changed.tags.getall('APIC')),[3,4])
                with self.assertRaises(FileExistsError):prepare(root,plan,output)
                # Reject stale input and unknown fields before creating output.
                plan['tracks'][0]['sha256']='0'*64
                with self.assertRaises(ValueError):prepare(root,plan,Path(tmp)/'bad')
                self.assertFalse((Path(tmp)/'bad').exists())
                plan['tracks'][0]['fields']['madeUp']='bad'
                with self.assertRaises(ValueError):prepare(root,plan,Path(tmp)/'bad')

    def test_path_and_review_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir()
            for path in ['../outside.flac','/outside.flac']:
                with self.assertRaises(ValueError):prepare(root,{'tracks':[{'path':path}]},Path(tmp)/'output')
            with self.assertRaises(ValueError):prepare(root,{'tracks':[]},Path(tmp)/'output')
            with self.assertRaises(ValueError):prepare(root,{'tracks':[{'path':'a.flac'}]},Path(tmp)/'output')
